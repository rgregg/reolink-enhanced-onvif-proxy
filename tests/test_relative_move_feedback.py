"""Tests for closed-loop RelativeMove on cameras without Set3DPos."""

import asyncio
import time

from reolink_onvif_proxy.reolink_api import PtzPosition, ReolinkAPI


class SimulatedCamera(ReolinkAPI):
    """Pan/tilt motor with a start delay, coast after Stop, and a counter
    that may run either way, like the real cameras."""

    def __init__(self, right_increases: bool, rate: float = 400.0, coast: float = 6.0):
        super().__init__("sim", 80)
        self.sign = 1 if right_increases else -1
        self.rate = rate
        self.coast = coast
        self.pan = 1000.0
        self.tilt = 100.0
        self._moving: tuple[str, float, float] | None = None  # (op, started, start_value)

    def _value(self, axis: str) -> float:
        base = self.pan if axis == "pan" else self.tilt
        if self._moving is None:
            return base
        op, started, _ = self._moving
        if (op in ("Left", "Right")) != (axis == "pan"):
            return base
        dirn = self.sign if op in ("Right", "Down") else -self.sign
        return base + dirn * self.rate * (time.monotonic() - started)

    async def ptz_control(self, username, password, op, speed=25, channel=0):
        if op == "Stop" and self._moving is not None:
            op_was = self._moving[0]
            axis = "pan" if op_was in ("Left", "Right") else "tilt"
            dirn = self.sign if op_was in ("Right", "Down") else -self.sign
            value = self._value(axis) + dirn * self.coast
            if axis == "pan":
                self.pan = value
            else:
                self.tilt = value
            self._moving = None
        elif op != "Stop":
            self._moving = (op, time.monotonic(), 0.0)
        return True

    async def get_position(self, username, password, channel=0):
        return PtzPosition(pan=int(self._value("pan")), tilt=int(self._value("tilt")))


def run(coro):
    return asyncio.run(coro)


def test_pan_right_reaches_target_when_counter_decreases():
    cam = SimulatedCamera(right_increases=False)
    # 0.5 of a 400-unit view = 100 units
    run(cam.relative_move_feedback("u", "p", pan=0.5, tilt=0, pan_units=400, tilt_units=300))
    assert abs((1000 - cam.pan) - 100) <= 15
    assert cam._axis_direction["pan"] == -1


def test_pan_right_reaches_target_when_counter_increases():
    cam = SimulatedCamera(right_increases=True)
    run(cam.relative_move_feedback("u", "p", pan=0.5, tilt=0, pan_units=400, tilt_units=300))
    assert abs((cam.pan - 1000) - 100) <= 15
    assert cam._axis_direction["pan"] == 1


def test_tilt_down_and_left_combined():
    cam = SimulatedCamera(right_increases=True)
    run(cam.relative_move_feedback("u", "p", pan=-0.4, tilt=-0.4, pan_units=400, tilt_units=300))
    assert abs((1000 - cam.pan) - 80) <= 15
    assert abs((cam.tilt - 100) - 60) <= 15


def test_tiny_move_below_tolerance_is_skipped():
    cam = SimulatedCamera(right_increases=True)
    run(cam.relative_move_feedback("u", "p", pan=0.005, tilt=0, pan_units=400, tilt_units=300))
    assert cam.pan == 1000
