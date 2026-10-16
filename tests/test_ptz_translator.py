"""Tests for PTZ translation logic."""

import pytest

from reolink_onvif_proxy.ptz_translator import (
    ZoomCurve,
    absolute_zoom_to_position,
    center_box,
    continuous_move_to_op,
    target_magnification,
)

W, H = 3840, 2160


def box_center(box):
    return box.pos_x + box.pos_width / 2, box.pos_y + box.pos_height / 2


class TestCenterBox:
    """Test RelativeMove pan/tilt -> Set3DPos re-centering box."""

    def test_no_offset_is_noop(self):
        assert center_box(0, 0, W, H) is None

    def test_right_uses_full_frame_box(self):
        """Right/down targets keep a full-size box (no zoom) that runs off the edge."""
        box = center_box(0.5, 0, W, H)
        assert (box.pos_width, box.pos_height) == (W, H)
        assert box_center(box) == (2880, 1080)

    def test_down_uses_full_frame_box(self):
        box = center_box(0, -0.5, W, H)
        assert (box.pos_width, box.pos_height) == (W, H)
        assert box_center(box) == (1920, 1620)

    def test_left_shrinks_box_to_stay_in_bounds(self):
        """Camera ignores negative posX, so left targets get a smaller box."""
        box = center_box(-0.5, 0, W, H)
        assert box.pos_x == 0
        assert box.pos_width == W // 2
        assert box_center(box) == (960, 1080)

    def test_up_left_keeps_aspect_and_center(self):
        box = center_box(-0.5, 0.5, W, H)
        assert (box.pos_x, box.pos_y) == (0, 0)
        assert box.pos_width / box.pos_height == pytest.approx(W / H, rel=0.01)
        assert box_center(box) == (960, 540)

    def test_origin_never_negative(self):
        for pan in (-1, -0.9, -0.3, 0.3, 1):
            for tilt in (-1, -0.4, 0.4, 0.95, 1):
                box = center_box(pan, tilt, W, H)
                assert box.pos_x >= 0 and box.pos_y >= 0

    def test_extreme_edge_uses_minimum_box(self):
        box = center_box(-1.0, 0, W, H)
        assert box.pos_width == int(W * 0.05)

    def test_clamping(self):
        assert box_center(center_box(5.0, 0, W, H)) == box_center(center_box(1.0, 0, W, H))

    def test_speed_mapping(self):
        assert center_box(0.5, 0, W, H, speed=1.0).speed == 61
        assert center_box(0.5, 0, W, H, speed=0.0).speed == 1

    def test_stream_dimensions_passed_through(self):
        box = center_box(0.5, 0, 1920, 1080)
        assert (box.width, box.height) == (1920, 1080)


class TestTargetMagnification:
    """Frigate drag-to-zoom sends the drawn box size as a frame fraction."""

    def test_zoom_in_fills_frame_with_box(self):
        assert target_magnification(1.0, 0.5) == pytest.approx(2.0)
        assert target_magnification(2.0, 0.25) == pytest.approx(8.0)

    def test_zoom_out_shrinks_view_to_box(self):
        assert target_magnification(4.0, -0.5) == pytest.approx(2.0)

    def test_no_zoom(self):
        assert target_magnification(3.0, 0) == 3.0

    def test_fraction_floor(self):
        assert target_magnification(1.0, 0.001) == pytest.approx(100.0)


class TestZoomCurve:
    POINTS = {0: 1.0, 8: 1.42, 16: 2.12, 24: 3.45, 32: 6.14, 41: 12.7}

    def test_calibrated_points_round_trip(self):
        curve = ZoomCurve(0, 41, self.POINTS)
        for step, mag in self.POINTS.items():
            assert curve.magnification(step) == pytest.approx(mag)
            assert curve.step_for(mag) == step

    def test_interpolates_in_log_space(self):
        curve = ZoomCurve(0, 41, {0: 1.0, 10: 4.0})
        assert curve.magnification(5) == pytest.approx(2.0)
        assert curve.step_for(2.0) == 5

    def test_clamps_to_range(self):
        curve = ZoomCurve(0, 41, self.POINTS)
        assert curve.step_for(0.5) == 0
        assert curve.step_for(50) == 41

    def test_default_geometric_curve(self):
        curve = ZoomCurve(0, 30, max_magnification=4.0)
        assert curve.magnification(0) == pytest.approx(1.0)
        assert curve.magnification(15) == pytest.approx(2.0)
        assert curve.magnification(30) == pytest.approx(4.0)

    def test_half_frame_drag_doubles_magnification(self):
        curve = ZoomCurve(0, 41, self.POINTS)
        start = 0
        target = curve.step_for(target_magnification(curve.magnification(start), 0.5))
        assert curve.magnification(target) == pytest.approx(2.0, rel=0.06)


class TestContinuousMoveToOp:
    """Test ContinuousMove velocity to PtzCtrl op translation."""

    def test_pan_right(self):
        op, speed = continuous_move_to_op(0.5, 0, 0)
        assert op == "Right"
        assert speed > 0

    def test_pan_left(self):
        op, _ = continuous_move_to_op(-0.5, 0, 0)
        assert op == "Left"

    def test_tilt_up(self):
        op, _ = continuous_move_to_op(0, 0.5, 0)
        assert op == "Up"

    def test_tilt_down(self):
        op, _ = continuous_move_to_op(0, -0.5, 0)
        assert op == "Down"

    def test_zoom_in(self):
        op, _ = continuous_move_to_op(0, 0, 0.5)
        assert op == "ZoomInc"

    def test_zoom_out(self):
        op, _ = continuous_move_to_op(0, 0, -0.5)
        assert op == "ZoomDec"

    def test_diagonal(self):
        op, _ = continuous_move_to_op(0.5, 0.5, 0)
        assert op == "RightUp"

    def test_no_movement(self):
        op, _ = continuous_move_to_op(0, 0, 0)
        assert op == "Stop"

    def test_zoom_dominates(self):
        """When zoom velocity is larger than pan/tilt, zoom wins."""
        op, _ = continuous_move_to_op(0.1, 0.1, 0.5)
        assert op == "ZoomInc"


class TestAbsoluteZoomToPosition:
    """Test ONVIF zoom (0-1) to Reolink zoom position conversion."""

    def test_min_zoom(self):
        assert absolute_zoom_to_position(0.0, 0, 33) == 0

    def test_max_zoom(self):
        assert absolute_zoom_to_position(1.0, 0, 33) == 33

    def test_mid_zoom(self):
        result = absolute_zoom_to_position(0.5, 0, 33)
        assert 16 <= result <= 17  # approximately half

    def test_clamping(self):
        assert absolute_zoom_to_position(-0.5, 0, 33) == 0
        assert absolute_zoom_to_position(1.5, 0, 33) == 33
