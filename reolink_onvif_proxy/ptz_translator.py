"""Translation logic for ONVIF RelativeMove to Reolink Set3DPos."""

import math
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


def center_box(
    pan: float,
    tilt: float,
    stream_width: int,
    stream_height: int,
    speed: float = 1.0,
    min_scale: float = 0.05,
) -> Set3DPosParams | None:
    """Build a Set3DPos box that re-centers the view on a point without zooming.

    ``pan``/``tilt`` are ONVIF FOV-space offsets (-1..1) of the target point from
    the frame center, as Frigate sends for click-to-move (tilt up is positive).

    Set3DPos reframes the camera on the box's center, and the box size drives
    the camera's own zoom choice. A full-frame box shifted so its center is on
    the target pans without zooming, and the camera accepts a box running past
    the right/bottom edge. It ignores boxes with a negative posX/posY, so for
    targets left of or above center the box is shrunk until it fits; the camera
    then zooms in somewhat, and the caller restores the zoom level afterwards.

    Returns None when the target is the current center (nothing to do).
    """
    pan = max(-1.0, min(1.0, pan))
    tilt = max(-1.0, min(1.0, tilt))
    if abs(pan) < 0.01 and abs(tilt) < 0.01:
        return None

    cx = stream_width / 2 * (1 + pan)
    cy = stream_height / 2 * (1 - tilt)

    # Largest frame-aspect box centered on (cx, cy) with a non-negative origin
    scale = min(1.0, 2 * cx / stream_width, 2 * cy / stream_height)
    scale = max(min_scale, scale)
    box_width = int(stream_width * scale)
    box_height = int(stream_height * scale)

    pos_x = max(0, int(cx - box_width / 2))
    pos_y = max(0, int(cy - box_height / 2))

    return Set3DPosParams(
        pos_x=pos_x,
        pos_y=pos_y,
        pos_width=box_width,
        pos_height=box_height,
        width=stream_width,
        height=stream_height,
        speed=max(1, min(64, int(speed * 60) + 1)),
    )


def target_magnification(current_mag: float, zoom: float) -> float:
    """Magnification Frigate asks for with a RelativeMove zoom value.

    Frigate's drag-to-zoom sends the drawn box's size as a fraction of the
    frame (0.01..1). Positive means "zoom so the box fills the frame"; negative
    (box drawn right-to-left) means "zoom out so the current view shrinks to
    the box size".
    """
    fraction = max(0.01, min(1.0, abs(zoom)))
    if zoom > 0:
        return current_mag / fraction
    if zoom < 0:
        return current_mag * fraction
    return current_mag


class ZoomCurve:
    """Maps camera zoom steps to optical magnification (1.0 at the widest step).

    Built from calibration points ``{step: magnification}``; values between
    points are interpolated in log space. Without calibration points, assumes
    magnification grows geometrically from 1x at ``zoom_min`` to
    ``max_magnification`` at ``zoom_max``.
    """

    def __init__(
        self,
        zoom_min: int,
        zoom_max: int,
        points: dict[int, float] | None = None,
        max_magnification: float = 4.0,
    ):
        self.zoom_min = zoom_min
        self.zoom_max = zoom_max
        if points:
            pts = sorted((int(k), float(v)) for k, v in points.items())
        else:
            pts = [(zoom_min, 1.0), (zoom_max, max_magnification)]
        self._steps = [p[0] for p in pts]
        self._logs = [math.log(max(p[1], 1e-6)) for p in pts]

    def magnification(self, step: float) -> float:
        return math.exp(self._interp(step, self._steps, self._logs))

    def step_for(self, magnification: float) -> int:
        """Nearest zoom step for a magnification, clamped to the camera range."""
        log_mag = math.log(max(magnification, 1e-6))
        if self._logs[0] <= self._logs[-1]:
            step = self._interp(log_mag, self._logs, self._steps)
        else:
            step = self._interp(log_mag, self._logs[::-1], self._steps[::-1])
        return max(self.zoom_min, min(self.zoom_max, round(step)))

    @staticmethod
    def _interp(x: float, xs: list[float], ys: list[float]) -> float:
        if len(xs) == 1:
            return ys[0]
        # Clamp to the calibrated range rather than extrapolating
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        for i in range(1, len(xs)):
            if x <= xs[i]:
                t = (x - xs[i - 1]) / (xs[i] - xs[i - 1])
                return ys[i - 1] + t * (ys[i] - ys[i - 1])
        return ys[-1]


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
