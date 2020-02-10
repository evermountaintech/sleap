import pytest
import numpy as np
import tensorflow as tf

tf.config.experimental.set_visible_devices([], device_type="GPU")  # hide GPUs for test

from sleap.nn.data import normalization


def test_ensure_min_image_rank():
    assert normalization.ensure_min_image_rank(tf.zeros([2, 2])).shape == (2, 2, 1)
    assert normalization.ensure_min_image_rank(tf.zeros([2, 2, 1])).shape == (2, 2, 1)


def test_ensure_float():
    assert normalization.ensure_float(tf.zeros([2, 2], tf.uint8)).dtype == tf.float32
    assert normalization.ensure_float(tf.zeros([2, 2], tf.float32)).dtype == tf.float32


def test_ensure_grayscale():
    np.testing.assert_array_equal(
        normalization.ensure_grayscale(tf.ones([2, 2, 3], tf.uint8) * 255),
        tf.ones([2, 2, 1], tf.uint8) * 255)
    np.testing.assert_array_equal(
        normalization.ensure_grayscale(tf.ones([2, 2, 1], tf.uint8) * 255),
        tf.ones([2, 2, 1], tf.uint8) * 255)
    np.testing.assert_allclose(
        normalization.ensure_grayscale(tf.ones([2, 2, 3], tf.float32)),
        tf.ones([2, 2, 1], tf.float32),
        atol=1e-4)

    with pytest.raises(ValueError):
        normalization.ensure_grayscale(tf.ones([2, 2, 5]))


def test_ensure_rgb():
    np.testing.assert_array_equal(
        normalization.ensure_rgb(tf.ones([2, 2, 3], tf.uint8) * 255),
        tf.ones([2, 2, 3], tf.uint8) * 255)
    np.testing.assert_array_equal(
        normalization.ensure_rgb(tf.ones([2, 2, 1], tf.uint8) * 255),
        tf.ones([2, 2, 3], tf.uint8) * 255)


def test_convert_rgb_to_bgr():
    img_rgb = tf.stack([
        tf.ones([2, 2], dtype=tf.uint8) * 1,
        tf.ones([2, 2], dtype=tf.uint8) * 2,
        tf.ones([2, 2], dtype=tf.uint8) * 3,
    ], axis=-1)
    img_bgr = tf.stack([
        tf.ones([2, 2], dtype=tf.uint8) * 3,
        tf.ones([2, 2], dtype=tf.uint8) * 2,
        tf.ones([2, 2], dtype=tf.uint8) * 1,
    ], axis=-1)

    np.testing.assert_array_equal(
        normalization.convert_rgb_to_bgr(img_rgb),
        img_bgr)


def test_scale_image_range():
    np.testing.assert_array_equal(normalization.scale_image_range(
        tf.cast([0, 0.5, 1.0], tf.float32),
        min_val=-1.0,
        max_val=1.0
    ), [-1, 0, 1])