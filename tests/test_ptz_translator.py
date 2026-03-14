"""Tests for PTZ translation logic."""

from reolink_onvif_proxy.ptz_translator import (
    absolute_zoom_to_position,
    continuous_move_to_op,
    relative_move_to_3d_pos,
)


class TestRelativeMoveTo3DPos:
    """Test RelativeMove → Set3DPos translation."""

    def test_center_no_zoom(self):
        """pan=0, tilt=0 should center the box (minimal movement)."""
        result = relative_move_to_3d_pos(0, 0, 0, 3840, 2160)
        # Box should be centered: pos_x = (3840 - boxW) / 2
        assert result.pos_x == (3840 - result.pos_width) // 2
        assert result.pos_y == (2160 - result.pos_height) // 2
        # No zoom: box should be ~90% of frame
        assert result.pos_width > 3000
        assert result.pos_height > 1900

    def test_pan_right(self):
        """pan=1.0 should shift box to right side."""
        result = relative_move_to_3d_pos(1.0, 0, 0, 3840, 2160)
        center = relative_move_to_3d_pos(0, 0, 0, 3840, 2160)
        assert result.pos_x > center.pos_x

    def test_pan_left(self):
        """pan=-1.0 should shift box to left side."""
        result = relative_move_to_3d_pos(-1.0, 0, 0, 3840, 2160)
        center = relative_move_to_3d_pos(0, 0, 0, 3840, 2160)
        assert result.pos_x < center.pos_x

    def test_tilt_up(self):
        """tilt=1.0 should shift box upward (smaller posY)."""
        result = relative_move_to_3d_pos(0, 1.0, 0, 3840, 2160)
        center = relative_move_to_3d_pos(0, 0, 0, 3840, 2160)
        assert result.pos_y < center.pos_y

    def test_tilt_down(self):
        """tilt=-1.0 should shift box downward (larger posY)."""
        result = relative_move_to_3d_pos(0, -1.0, 0, 3840, 2160)
        center = relative_move_to_3d_pos(0, 0, 0, 3840, 2160)
        assert result.pos_y > center.pos_y

    def test_zoom_in(self):
        """Positive zoom should produce a smaller box."""
        result = relative_move_to_3d_pos(0, 0, 0.5, 3840, 2160)
        assert result.pos_width < 3840
        assert result.pos_height < 2160

    def test_zoom_out(self):
        """Negative zoom should produce a larger box."""
        result = relative_move_to_3d_pos(0, 0, -0.5, 3840, 2160)
        assert result.pos_width > 3840
        assert result.pos_height > 2160

    def test_clamping(self):
        """Values beyond -1/1 should be clamped."""
        result = relative_move_to_3d_pos(5.0, -5.0, 0, 3840, 2160)
        assert result.pos_x >= 0
        assert result.pos_y >= 0

    def test_speed_mapping(self):
        """Speed 1.0 should map to ~61, speed 0 to 1."""
        result = relative_move_to_3d_pos(0, 0, 0, 3840, 2160, speed=1.0)
        assert result.speed == 61
        result = relative_move_to_3d_pos(0, 0, 0, 3840, 2160, speed=0.0)
        assert result.speed == 1

    def test_minimum_box_size(self):
        """Even max zoom should not produce a box smaller than 100px."""
        result = relative_move_to_3d_pos(0, 0, 1.0, 3840, 2160)
        assert result.pos_width >= 100
        assert result.pos_height >= 100

    def test_stream_dimensions_passed_through(self):
        """Stream dimensions should be in the output."""
        result = relative_move_to_3d_pos(0, 0, 0, 1920, 1080)
        assert result.width == 1920
        assert result.height == 1080


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
