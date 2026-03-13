"""Translation logic for ONVIF RelativeMove to Reolink Set3DPos."""

from dataclasses import dataclass


@dataclass
class Set3DPosParams:
    pos_x: int
    pos_y: int
    pos_width: int
    pos_height: int
    width: int
    height: int
    speed: int = 20


def relative_move_to_3d_pos(
    pan: float,
    tilt: float,
    zoom: float,
    stream_width: int,
    stream_height: int,
    speed: float = 1.0,
) -> Set3DPosParams:
    """Convert ONVIF RelativeMove (FOV-relative) to Reolink Set3DPos params.

    Args:
        pan: -1.0 to 1.0 (left to right within FOV)
        tilt: -1.0 to 1.0 (down to up within FOV)
        zoom: relative zoom change (-1.0 to 1.0, positive = zoom in)
        stream_width: stream resolution width (from Get3DPos)
        stream_height: stream resolution height (from Get3DPos)
        speed: ONVIF speed parameter (0.0 to 1.0)

    Returns:
        Set3DPosParams ready to send to the camera.
    """
    # Clamp inputs
    pan = max(-1.0, min(1.0, pan))
    tilt = max(-1.0, min(1.0, tilt))
    zoom = max(-1.0, min(1.0, zoom))

    # Convert relative offset to pixel position
    # pan=0,tilt=0 means center of frame (no movement)
    # pan=1.0 means right edge, pan=-1.0 means left edge
    target_x = (stream_width / 2) + (pan * stream_width / 2)
    target_y = (stream_height / 2) - (tilt * stream_height / 2)

    # Clamp to stream bounds
    target_x = max(0, min(stream_width, int(target_x)))
    target_y = max(0, min(stream_height, int(target_y)))

    # Box size determines zoom level
    # No zoom component: full frame (reposition only)
    # Positive zoom: smaller box (zoom in)
    # Negative zoom: larger box (zoom out) — capped at stream size
    if abs(zoom) < 0.01:
        box_width = stream_width
        box_height = stream_height
    else:
        # Scale factor: zoom=1.0 → box is 10% of frame (10x zoom)
        # zoom=-1.0 → box is full frame (1x zoom / zoom out)
        # zoom=0.5 → box is ~55% of frame
        scale = 1.0 - (zoom * 0.9)  # range: 0.1 to 1.9
        scale = max(0.1, min(2.0, scale))
        box_width = int(stream_width * scale)
        box_height = int(stream_height * scale)

    # Ensure minimum box size
    box_width = max(100, box_width)
    box_height = max(100, box_height)

    # Map ONVIF speed (0-1) to Reolink speed (1-64)
    reolink_speed = max(1, min(64, int(speed * 60) + 1))

    return Set3DPosParams(
        pos_x=target_x,
        pos_y=target_y,
        pos_width=box_width,
        pos_height=box_height,
        width=stream_width,
        height=stream_height,
        speed=reolink_speed,
    )


def continuous_move_to_op(pan_velocity: float, tilt_velocity: float, zoom_velocity: float) -> tuple[str, int]:
    """Convert ONVIF ContinuousMove velocities to a Reolink PtzCtrl op and speed.

    Returns:
        Tuple of (op_name, speed).
    """
    # Determine dominant axis
    abs_pan = abs(pan_velocity)
    abs_tilt = abs(tilt_velocity)
    abs_zoom = abs(zoom_velocity)

    if abs_zoom > abs_pan and abs_zoom > abs_tilt:
        op = "ZoomInc" if zoom_velocity > 0 else "ZoomDec"
        speed = max(1, min(64, int(abs_zoom * 60) + 1))
        return op, speed

    if abs_pan < 0.01 and abs_tilt < 0.01:
        return "Stop", 0

    # Pan/tilt: determine direction
    if abs_pan > abs_tilt * 1.5:
        op = "Right" if pan_velocity > 0 else "Left"
    elif abs_tilt > abs_pan * 1.5:
        op = "Up" if tilt_velocity > 0 else "Down"
    else:
        # Diagonal
        if pan_velocity > 0 and tilt_velocity > 0:
            op = "RightUp"
        elif pan_velocity > 0 and tilt_velocity < 0:
            op = "RightDown"
        elif pan_velocity < 0 and tilt_velocity > 0:
            op = "LeftUp"
        else:
            op = "LeftDown"

    speed = max(1, min(64, int(max(abs_pan, abs_tilt) * 60) + 1))
    return op, speed


def absolute_zoom_to_position(zoom_value: float, zoom_min: int, zoom_max: int) -> int:
    """Convert ONVIF absolute zoom (0.0-1.0) to Reolink zoom position.

    Args:
        zoom_value: ONVIF normalized zoom (0.0 to 1.0)
        zoom_min: Camera minimum zoom position
        zoom_max: Camera maximum zoom position

    Returns:
        Integer zoom position for Reolink StartZoomFocus.
    """
    zoom_value = max(0.0, min(1.0, zoom_value))
    return int(zoom_min + zoom_value * (zoom_max - zoom_min))
