import os
import cattr

from datetime import datetime
import multiprocessing
from functools import reduce
from pkg_resources import Requirement, resource_filename
from typing import Dict, List, Optional

from sleap.io.dataset import Labels
from sleap.io.video import Video
from sleap.gui.training_editor import TrainingEditor
from sleap.gui.formbuilder import YamlFormWidget
from sleap.nn.model import ModelOutputType
from sleap.nn.training import TrainingJob

from PySide2 import QtWidgets, QtCore

class ActiveLearningDialog(QtWidgets.QDialog):

    learningFinished = QtCore.Signal()

    def __init__(self,
                 labels_filename: str, labels: Labels,
                 mode: str="expert",
                 only_predict: bool=False,
                 *args, **kwargs):

        super(ActiveLearningDialog, self).__init__(*args, **kwargs)

        self.labels_filename = labels_filename
        self.labels = labels
        self.mode = mode
        self.only_predict = only_predict

        print(f"Number of frames to train on: {len(labels.user_labeled_frames)}")

        title = dict(learning="Active Learning",
                     inference="Inference",
                     expert="Inference Pipeline",
                     )

        learning_yaml = resource_filename(Requirement.parse("sleap"),"sleap/config/active.yaml")
        self.form_widget = YamlFormWidget(
                                yaml_file=learning_yaml,
                                which_form=self.mode,
                                title=title[self.mode] + " Settings")

        # form ui

        self.training_profile_widgets = dict()

        if "conf_job" in self.form_widget.fields:
            self.training_profile_widgets[ModelOutputType.CONFIDENCE_MAP] = self.form_widget.fields["conf_job"]
        if "paf_job" in self.form_widget.fields:
            self.training_profile_widgets[ModelOutputType.PART_AFFINITY_FIELD] = self.form_widget.fields["paf_job"]
        if "centroid_job" in self.form_widget.fields:
            self.training_profile_widgets[ModelOutputType.CENTROIDS] = self.form_widget.fields["centroid_job"]

        self._rebuild_job_options()
        self._update_job_menus(init=True)

        buttons = QtWidgets.QDialogButtonBox()
        self.cancel_button = buttons.addButton(QtWidgets.QDialogButtonBox.Cancel)
        self.run_button = buttons.addButton(
                                "Run "+title[self.mode],
                                QtWidgets.QDialogButtonBox.AcceptRole)

        self.status_message = QtWidgets.QLabel("hi!")

        buttons_layout = QtWidgets.QHBoxLayout()
        buttons_layout.addWidget(self.status_message)
        buttons_layout.addWidget(buttons, alignment=QtCore.Qt.AlignTop)

        buttons_layout_widget = QtWidgets.QWidget()
        buttons_layout_widget.setLayout(buttons_layout)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.form_widget)
        layout.addWidget(buttons_layout_widget)

        self.setLayout(layout)

        # connect actions to buttons

        def edit_conf_profile():
            self.view_profile(self.form_widget["conf_job"],
                                model_type=ModelOutputType.CONFIDENCE_MAP)
        def edit_paf_profile():
            self.view_profile(self.form_widget["paf_job"],
                                model_type=ModelOutputType.PART_AFFINITY_FIELD)
        def edit_cent_profile():
            self.view_profile(self.form_widget["centroid_job"],
                                model_type=ModelOutputType.CENTROIDS)

        if "_view_conf" in self.form_widget.buttons:
            self.form_widget.buttons["_view_conf"].clicked.connect(edit_conf_profile)
        if "_view_paf" in self.form_widget.buttons:
            self.form_widget.buttons["_view_paf"].clicked.connect(edit_paf_profile)
        if "_view_centoids" in self.form_widget.buttons:
            self.form_widget.buttons["_view_centoids"].clicked.connect(edit_cent_profile)
        if "_view_datagen" in self.form_widget.buttons:
            self.form_widget.buttons["_view_datagen"].clicked.connect(self.view_datagen)

        self.form_widget.valueChanged.connect(lambda: self.update_gui())

        buttons.accepted.connect(self.run)
        buttons.rejected.connect(self.reject)

        self.update_gui()

    def _rebuild_job_options(self):
        # load list of job profiles from directory
        profile_dir = resource_filename(Requirement.parse("sleap"), "sleap/training_profiles")
        labels_dir = os.path.join(os.path.dirname(self.labels_filename), "models")

        self.job_options = dict()

        # list any profiles from previous runs
        if os.path.exists(labels_dir):
            find_saved_jobs(labels_dir, self.job_options)
        # list default profiles
        find_saved_jobs(profile_dir, self.job_options)

    def _update_job_menus(self, init=False):
        for model_type, field in self.training_profile_widgets.items():
            if model_type not in self.job_options:
                self.job_options[model_type] = []
            if init:
                field.currentIndexChanged.connect(lambda idx, mt=model_type: self.select_job(mt, idx))
            else:
                # block signals so we can update combobox without overwriting
                # any user data with the defaults from the profile
                field.blockSignals(True)
            field.set_options(self.option_list_from_jobs(model_type))
            # enable signals again so that choice of profile will update params
            field.blockSignals(False)

    @property
    def frame_selection(self):
        return self._frame_selection

    @frame_selection.setter
    def frame_selection(self, frame_selection):
        self._frame_selection = frame_selection

        if "_predict_frames" in self.form_widget.fields.keys():
            prediction_options = []

            def count_total_frames(videos_frames):
                return reduce(lambda x,y:x+y, map(len, videos_frames.values()))

            # Determine which options are available given _frame_selection

            total_random = count_total_frames(self._frame_selection["random"])
            total_suggestions = count_total_frames(self._frame_selection["suggestions"])
            clip_length = count_total_frames(self._frame_selection["clip"])
            video_length = count_total_frames(self._frame_selection["video"])

            # Build list of options

            prediction_options.append("current frame")

            option = f"random frames ({total_random} total frames)"
            prediction_options.append(option)
            default_option = option

            if total_suggestions > 0:
                option = f"suggested frames ({total_suggestions} total frames)"
                prediction_options.append(option)
                default_option = option

            if clip_length > 0:
                option = f"selected clip ({clip_length} frames)"
                prediction_options.append(option)
                default_option = option

            prediction_options.append(f"entire video ({video_length} frames)")

            self.form_widget.fields["_predict_frames"].set_options(prediction_options, default_option)

    def show(self):
        super(ActiveLearningDialog, self).show()

        # TODO: keep selection and any items added from training editor

        self._rebuild_job_options()
        self._update_job_menus()

    def update_gui(self):
        form_data = self.form_widget.get_form_data()

        can_run = True

        if "_use_centroids" in self.form_widget.fields:
            use_centroids = form_data.get("_use_centroids", False)

            if form_data.get("_use_trained_centroids", False):
                # you must use centroids if you are using a centroid model
                use_centroids = True
                self.form_widget.set_form_data(dict(_use_centroids=True))
                self.form_widget.fields["_use_centroids"].setEnabled(False)
            else:
                self.form_widget.fields["_use_centroids"].setEnabled(True)

            if use_centroids:
                # you must crop if you are using centroids
                self.form_widget.set_form_data(dict(instance_crop=True))
                self.form_widget.fields["instance_crop"].setEnabled(False)
            else:
                self.form_widget.fields["instance_crop"].setEnabled(True)

        error_messages = []
        if form_data.get("_use_trained_confmaps", False) and \
                form_data.get("_use_trained_pafs", False):
            # make sure trained models are compatible
            conf_job, _ = self._get_current_job(ModelOutputType.CONFIDENCE_MAP)
            paf_job, _ = self._get_current_job(ModelOutputType.PART_AFFINITY_FIELD)

            if conf_job.trainer.scale != paf_job.trainer.scale:
                can_run = False
                error_messages.append(f"training image scale for confmaps ({conf_job.trainer.scale}) does not match pafs ({paf_job.trainer.scale})")
            if conf_job.trainer.instance_crop != paf_job.trainer.instance_crop:
                can_run = False
                crop_model_name = "confmaps" if conf_job.trainer.instance_crop else "pafs"
                error_messages.append(f"exactly one model ({crop_model_name}) was trained on crops")
            if use_centroids and not conf_job.trainer.instance_crop:
                can_run = False
                error_messages.append(f"models used with centroids must be trained on cropped images")

        message = ""
        if not can_run:
            message = "Unable to run with selected models:\n- " + \
                      ";\n- ".join(error_messages) + "."
        self.status_message.setText(message)

        self.run_button.setEnabled(can_run)

    def _get_current_job(self, model_type):
        # by default use the first model for a given type
        idx = 0
        if model_type in self.training_profile_widgets:
            field = self.training_profile_widgets[model_type]
            idx = field.currentIndex()

        job_filename, job = self.job_options[model_type][idx]

        if model_type == ModelOutputType.CENTROIDS:
            # reload centroid profile since we always want to use this
            # rather than any scale and such entered by user
            job = TrainingJob.load_json(job_filename)

        return job, job_filename

    def _get_model_types_to_use(self):
        form_data = self.form_widget.get_form_data()
        types_to_use = []

        types_to_use.append(ModelOutputType.CONFIDENCE_MAP)
        types_to_use.append(ModelOutputType.PART_AFFINITY_FIELD)

        # by default we want to use centroids
        if form_data.get("_use_centroids", True):
            types_to_use.append(ModelOutputType.CENTROIDS)

        return types_to_use

    def _get_current_training_jobs(self):
        form_data = self.form_widget.get_form_data()
        training_jobs = dict()

        default_use_trained = (self.mode == "inference")

        for model_type in self._get_model_types_to_use():
            job, _ = self._get_current_job(model_type)

            if job.model.output_type != ModelOutputType.CENTROIDS:
                # update training job from params in form
                trainer = job.trainer
                for key, val in form_data.items():
                    # check if field name is [var]_[model_type] (eg sigma_confmaps)
                    if key.split("_")[-1] == str(model_type):
                        key = "_".join(key.split("_")[:-1])
                    # check if form field matches attribute of Trainer object
                    if key in dir(trainer):
                        setattr(trainer, key, val)
            # Use already trained model if desired
            if form_data.get(f"_use_trained_{str(model_type)}", default_use_trained):
                job.use_trained_model = True

            training_jobs[model_type] = job

        return training_jobs

    def run(self):
        # Collect TrainingJobs and params from form
        form_data = self.form_widget.get_form_data()
        training_jobs = self._get_current_training_jobs()

        # Close the dialog now that we have the data from it
        self.accept()

        with_tracking = False
        predict_frames_choice = form_data.get("_predict_frames", "")
        if predict_frames_choice.startswith("current frame"):
            frames_to_predict = self._frame_selection["frame"]
        elif predict_frames_choice.startswith("random"):
            frames_to_predict = self._frame_selection["random"]
        elif predict_frames_choice.startswith("selected clip"):
            frames_to_predict = self._frame_selection["clip"]
            with_tracking = True
        elif predict_frames_choice.startswith("suggested"):
            frames_to_predict = self._frame_selection["suggestions"]
        elif predict_frames_choice.startswith("entire video"):
            frames_to_predict = self._frame_selection["video"]
            with_tracking = True
        else:
            frames_to_predict = dict()

        # Run active learning pipeline using the TrainingJobs
        new_lfs = run_active_learning_pipeline(
                    labels_filename = self.labels_filename,
                    labels = self.labels,
                    training_jobs = training_jobs,
                    frames_to_predict = frames_to_predict,
                    with_tracking = with_tracking)

        # remove labeledframes without any predicted instances
        new_lfs = list(filter(lambda lf: len(lf.instances), new_lfs))
        # Update labels with results of active learning

        new_tracks = {inst.track for lf in new_lfs for inst in lf.instances if inst.track is not None}
        if len(new_tracks) < 50:
            self.labels.tracks = list(set(self.labels.tracks).union(new_tracks))
        # if there are more than 50 predicted tracks, assume this is wrong (FIXME?)
        elif len(new_tracks):
            for lf in new_lfs:
                for inst in lf.instances:
                    inst.track = None

        # Update Labels with new data
        # add new labeled frames
        self.labels.extend_from(new_lfs)
        # combine instances from labeledframes with same video/frame_idx
        self.labels.merge_matching_frames()

        self.learningFinished.emit()

        QtWidgets.QMessageBox(text=f"Active learning has finished. Instances were predicted on {len(new_lfs)} frames.").exec_()

    def view_datagen(self):
        from sleap.nn.datagen import generate_images, generate_points, instance_crops, \
                                generate_confmaps_from_points, generate_pafs_from_points
        from sleap.io.video import Video
        from sleap.gui.confmapsplot import demo_confmaps
        from sleap.gui.quiverplot import demo_pafs

        conf_job, _ = self._get_current_job(ModelOutputType.CONFIDENCE_MAP)

        # settings for datagen
        form_data = self.form_widget.get_form_data()
        scale = form_data.get("scale", conf_job.trainer.scale)
        sigma = form_data.get("sigma", None)
        sigma_confmaps = form_data.get("sigma_confmaps", sigma)
        sigma_pafs = form_data.get("sigma_pafs", sigma)
        instance_crop = form_data.get("instance_crop", conf_job.trainer.instance_crop)
        min_crop_size = form_data.get("min_crop_size", 0)
        negative_samples = form_data.get("negative_samples", 0)

        labels = self.labels
        imgs = generate_images(labels, scale, frame_limit=10)
        points = generate_points(labels, scale, frame_limit=10)

        if instance_crop:
            imgs, points = instance_crops(imgs, points,
                                          min_crop_size=min_crop_size,
                                          negative_samples=negative_samples)

        skeleton = labels.skeletons[0]
        img_shape = (imgs.shape[1], imgs.shape[2])
        vid = Video.from_numpy(imgs * 255)

        confmaps = generate_confmaps_from_points(points, skeleton, img_shape, sigma=sigma_confmaps)
        conf_win = demo_confmaps(confmaps, vid)
        conf_win.activateWindow()
        conf_win.move(200, 200)

        pafs = generate_pafs_from_points(points, skeleton, img_shape, sigma=sigma_pafs)
        paf_win = demo_pafs(pafs, vid)
        paf_win.activateWindow()
        paf_win.move(220+conf_win.rect().width(), 200)

        # FIXME: hide dialog so use can see other windows
        # can we show these windows without closing dialog?
        self.hide()

    # open profile editor in new dialog window
    def view_profile(self, filename, model_type, windows=[]):
        saved_files = []
        win = TrainingEditor(filename, saved_files=saved_files, parent=self)
        windows.append(win)
        win.exec_()

        for new_filename in saved_files:
            self._add_job_file_to_list(new_filename, model_type)

    def option_list_from_jobs(self, model_type):
        jobs = self.job_options[model_type]
        option_list = [name for (name, job) in jobs]
        option_list.append("---")
        option_list.append("Select a training profile file...")
        return option_list

    def add_job_file(self, model_type):
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
                        None, dir=None,
                        caption="Select training profile...",
                        filter="TrainingJob JSON (*.json)")

        self._add_job_file_to_list(filename, model_type)
        field = self.training_profile_widgets[model_type]
        # if we didn't successfully select a new file, then clear selection
        if field.currentIndex() == field.count()-1: # subtract 1 for separator
            field.setCurrentIndex(-1)

    def _add_job_file_to_list(self, filename, model_type):
        if len(filename):
            try:
                # try to load json as TrainingJob
                job = TrainingJob.load_json(filename)
            except:
                # but do raise any other type of error
                QtWidgets.QMessageBox(text=f"Unable to load a training profile from {filename}.").exec_()
                raise
            else:
                # we loaded the json as a TrainingJob, so see what type of model it's for
                file_model_type = job.model.output_type
                # make sure the users selected a file with the right model type
                if model_type == file_model_type:
                    # insert at beginning of list
                    self.job_options[model_type].insert(0, (filename, job))
                    # update ui list
                    if model_type in self.training_profile_widgets:
                        field = self.training_profile_widgets[model_type]
                        field.set_options(self.option_list_from_jobs(model_type), filename)
                else:
                    QtWidgets.QMessageBox(text=f"Profile selected is for training {str(file_model_type)} instead of {str(model_type)}.").exec_()

    def select_job(self, model_type, idx):
        jobs = self.job_options[model_type]
        if idx == -1: return
        if idx < len(jobs):
            name, job = jobs[idx]

            training_params = cattr.unstructure(job.trainer)
            training_params_specific = {f"{key}_{str(model_type)}":val for key,val in training_params.items()}
            # confmap and paf models should share some params shown in dialog (e.g. scale)
            # but centroids does not, so just set any centroid_foo fields from its profile
            if model_type in [ModelOutputType.CENTROIDS]:
                training_params = training_params_specific
            else:
                training_params = {**training_params, **training_params_specific}
            self.form_widget.set_form_data(training_params)

            # is the model already trained?
            has_trained = False
            final_model_filename = job.final_model_filename
            if final_model_filename is not None:
                if os.path.exists(os.path.join(job.save_dir, final_model_filename)):
                    has_trained = True
            field_name = f"_use_trained_{str(model_type)}"
            # update "use trained" checkbox
            self.form_widget.fields[field_name].setEnabled(has_trained)
            self.form_widget[field_name] = has_trained
        else:
            # last item is "select file..."
            self.add_job_file(model_type)


def make_default_training_jobs():
    from sleap.nn.model import Model, ModelOutputType
    from sleap.nn.training import Trainer, TrainingJob
    from sleap.nn.architectures import unet, leap

    # Build Models (wrapper for Keras model with some metadata)

    models = dict()
    models[ModelOutputType.CONFIDENCE_MAP] = Model(
            output_type=ModelOutputType.CONFIDENCE_MAP,
            backbone=unet.UNet(num_filters=32))
    models[ModelOutputType.PART_AFFINITY_FIELD] = Model(
            output_type=ModelOutputType.PART_AFFINITY_FIELD,
            backbone=leap.LeapCNN(num_filters=64))

    # Build Trainers

    defaults = dict()
    defaults["shared"] = dict(
            instance_crop = True,
            val_size = 0.1,
            augment_rotation=180,
            batch_size=4,
            learning_rate = 1e-4,
            reduce_lr_factor=0.5,
            reduce_lr_cooldown=3,
            reduce_lr_min_delta=1e-6,
            reduce_lr_min_lr = 1e-10,
            amsgrad = True,
            shuffle_every_epoch=True,
            save_every_epoch = False,
#             val_batches_per_epoch = 10,
#             upsampling_layers = True,
#             depth = 3,
    )
    defaults[ModelOutputType.CONFIDENCE_MAP] = dict(
            **defaults["shared"],
            num_epochs=100,
            steps_per_epoch=200,
            reduce_lr_patience=5,
            )

    defaults[ModelOutputType.PART_AFFINITY_FIELD] = dict(
            **defaults["shared"],
            num_epochs=75,
            steps_per_epoch = 100,
            reduce_lr_patience=8,
            )

    trainers = dict()
    for type in models.keys():
        trainers[type] = Trainer(**defaults[type])

    # Build TrainingJobs from Models and Trainers

    training_jobs = dict()
    for type in models.keys():
        training_jobs[type] = TrainingJob(models[type], trainers[type])

    return training_jobs

def find_saved_jobs(job_dir, jobs=None):
    """Find all the TrainingJob json files in a given directory.

    Args:
        job_dir: the directory in which to look for json files
        jobs (optional): append to jobs, rather than creating new dict
    Returns:
        dict of {ModelOutputType: list of (filename, TrainingJob) tuples}
    """
    from sleap.nn.training import TrainingJob

    files = os.listdir(job_dir)

    json_files = [os.path.join(job_dir, f) for f in files if f.endswith(".json")]
    # sort newest to oldest
    json_files.sort(key=lambda f: os.path.getmtime(f), reverse=True)

    jobs = dict() if jobs is None else jobs
    for full_filename in json_files:
        try:
            # try to load json as TrainingJob
            job = TrainingJob.load_json(full_filename)
        except ValueError:
            # couldn't load as TrainingJob so just ignore this json file
            # probably it's a json file for something else
            pass
        except:
            # but do raise any other type of error
            raise
        else:
            # we loaded the json as a TrainingJob, so see what type of model it's for
            model_type = job.model.output_type
            if model_type not in jobs:
                jobs[model_type] = []
            jobs[model_type].append((full_filename, job))

    return jobs

def run_active_learning_pipeline(labels_filename, labels=None, training_jobs=None,
                                    frames_to_predict=None, with_tracking=False,
                                    skip_learning=False):
    # Imports here so we don't load TensorFlow before necessary
    from sleap.nn.monitor import LossViewer
    from sleap.nn.training import TrainingJob
    from sleap.nn.model import ModelOutputType
    from sleap.nn.inference import Predictor

    from PySide2 import QtWidgets

    labels = labels or Labels.load_json(labels_filename)

    # Prepare our TrainingJobs

    # Load the defaults we use for active learning
    if training_jobs is None:
        training_jobs = make_default_training_jobs()

    # Set the parameters specific to this run
    for job in training_jobs.values():
        job.labels_filename = labels_filename
#         job.trainer.scale = scale

    # Run the TrainingJobs

    save_dir = os.path.join(os.path.dirname(labels_filename), "models")

    # open training monitor window
    win = LossViewer()
    win.resize(600, 400)
    win.show()

    for model_type, job in training_jobs.items():
        if getattr(job, "use_trained_model", False):
            # set path to TrainingJob already trained from previous run
            json_name = f"{job.run_name}.json"
            training_jobs[model_type] = os.path.join(job.save_dir, json_name)
            print(f"Using already trained model: {training_jobs[model_type]}")

        else:
            print("Resetting monitor window.")
            win.reset(what=str(model_type))
            win.setWindowTitle(f"Training Model - {str(model_type)}")
            print(f"Start training {str(model_type)}...")

            if not skip_learning:
                # run training
                pool, result = job.trainer.train_async(model=job.model, labels=labels,
                                        save_dir=save_dir)

                while not result.ready():
                    QtWidgets.QApplication.instance().processEvents()
                    # win.check_messages()
                    result.wait(.01)

                if result.successful():
                    # get the path to the resulting TrainingJob file
                    training_jobs[model_type] = result.get()
                    print(f"Finished training {str(model_type)}.")
                else:
                    training_jobs[model_type] = None
                    win.close()
                    QtWidgets.QMessageBox(text=f"An error occured while training {str(model_type)}. Your command line terminal may have more information about the error.").exec_()
                    result.get()


    if not skip_learning:
        for model_type, job in training_jobs.items():
            # load job from json
            training_jobs[model_type] = TrainingJob.load_json(training_jobs[model_type])

    # close training monitor window
    win.close()

    if not skip_learning:
        # Create Predictor from the results of training
        predictor = Predictor(sleap_models=training_jobs)
        predictor.with_tracking = with_tracking

    # Run the Predictor for suggested frames
    # We want to predict for suggested frames that don't already have user instances

    new_labeled_frames = []
    user_labeled_frames = labels.user_labeled_frames

    # show message while running inference
    win = QtWidgets.QProgressDialog()
    win.setLabelText("    Running inference on selected frames...    ")
    win.show()
    QtWidgets.QApplication.instance().processEvents()

    for video, frames in frames_to_predict.items():
        if len(frames):
            if not skip_learning:
                timestamp = datetime.now().strftime("%y%m%d_%H%M%S")
                inference_output_path = os.path.join(save_dir, f"{timestamp}.inference.json")

                # run predictions for desired frames in this video
                # video_lfs = predictor.predict(input_video=video, frames=frames, output_path=inference_output_path)

                pool, result = predictor.predict_async(input_video=video, frames=frames,
                                        output_path=inference_output_path)

                while not result.ready():
                    QtWidgets.QApplication.instance().processEvents()
                    result.wait(.01)

                if result.successful():
                    new_labels_json = result.get()
                    new_labels = Labels.from_json(new_labels_json, match_to=labels)

                    video_lfs = new_labels.labeled_frames
                else:
                    QtWidgets.QMessageBox(text=f"An error occured during inference. Your command line terminal may have more information about the error.").exec_()
                    result.get()
            else:
                import time
                time.sleep(1)
                video_lfs = []

            new_labeled_frames.extend(video_lfs)

    # close message window
    win.close()

    return new_labeled_frames

if __name__ == "__main__":
    import sys

#     labels_filename = "/Volumes/fileset-mmurthy/nat/shruthi/labels-mac.json"
    labels_filename = sys.argv[1]
    labels = Labels.load_json(labels_filename)

    app = QtWidgets.QApplication()
    win = ActiveLearningDialog(labels=labels,labels_filename=labels_filename)
    win.show()
    app.exec_()

#     labeled_frames = run_active_learning_pipeline(labels_filename)
#     print(labeled_frames)