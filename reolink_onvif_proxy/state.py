"""Per-camera state tracking for position and move status."""

import time
from dataclasses import dataclass, field
from enum import Enum

from .reolink_api import PtzPosition, ZoomFocus


class MoveStatus(Enum):
    IDLE = "IDLE"
    MOVING = "MOVING"


@dataclass
class CameraState:
    """Tracks the current state of a camera for ONVIF status reporting."""

    position: PtzPosition = field(default_factory=PtzPosition)
    zoom_focus: ZoomFocus = field(default_factory=ZoomFocus)
    move_status: MoveStatus = MoveStatus.IDLE
    last_command_time: float = 0.0
    last_position_check: float = 0.0
    _prev_position: PtzPosition | None = None

    def mark_moving(self):
        """Mark camera as moving after a command is sent."""
        self.move_status = MoveStatus.MOVING
        self.last_command_time = time.monotonic()

    def update_position(self, position: PtzPosition, zoom_focus: ZoomFocus):
        """Update position and infer move status."""
        prev = self._prev_position
        self._prev_position = self.position
        self.position = position
        self.zoom_focus = zoom_focus
        self.last_position_check = time.monotonic()

        if self.move_status == MoveStatus.MOVING:
            elapsed = time.monotonic() - self.last_command_time
            # Keep reporting MOVING for at least 0.5s after a command to avoid
            # race condition where Frigate polls before the camera starts moving
            if elapsed < 0.5:
                return
            # If position hasn't changed since last check, mark as idle
            if prev is not None and prev.pan == position.pan and prev.tilt == position.tilt:
                self.move_status = MoveStatus.IDLE
            # Timeout: if moving for more than 15 seconds, assume idle
            elif elapsed > 15.0:
                self.move_status = MoveStatus.IDLE
