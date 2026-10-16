"""Reolink API client using reolink_aio (Baichuan + HTTP)."""

import asyncio
import logging
import time
from dataclasses import dataclass

from reolink_aio.api import Host
from reolink_aio.exceptions import NotSupportedError

logger = logging.getLogger(__name__)


@dataclass
class PtzPosition:
    pan: int = 0
    tilt: int = 0


@dataclass
class ZoomFocus:
    zoom_pos: int = 0
    zoom_min: int = 0
    zoom_max: int = 33
    focus_pos: int = 0
    focus_min: int = 0
    focus_max: int = 255


@dataclass
class StreamResolution:
    width: int = 3840
    height: int = 2160


class ReolinkAPI:
    """Client for Reolink camera using reolink_aio (Baichuan protocol)."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._api: Host | None = None
        self._logged_in = False
        self._stream_resolution: StreamResolution | None = None
        self._supports_3d_pos: bool | None = None
        self._has_tilt: bool = True  # Baichuan always returns tilt
        self._supports_ptz_speed: bool = True  # assume yes, learn on first failure
        # Learned per-model movement behaviour for position-feedback moves
        self._axis_direction: dict[str, int] = {}  # +1 if Right/Up increases position
        self._lead: dict[str, float] = {}  # axis -> overshoot past the stop point
        self._min_pulse: dict[str, float] = {}  # axis -> smallest movement one command makes

    async def _ensure_connected(self, username: str, password: str) -> Host:
        """Ensure we have a connected reolink_aio Host."""
        if self._api is None:
            self._api = Host(self.host, username, password, port=self.port)

        if not self._logged_in:
            try:
                await self._api.get_host_data()
                self._logged_in = True
                logger.info("Connected to camera %s via reolink_aio", self.host)
            except Exception as e:
                logger.error("Failed to connect to %s: %s", self.host, e)
                raise

        return self._api

    async def close(self):
        if self._api and self._logged_in:
            try:
                await self._api.logout()
            except Exception:
                pass
            self._logged_in = False

    async def probe_capabilities(self, username: str, password: str, channel: int = 0) -> None:
        """Probe camera to determine which PTZ features it supports."""
        api = await self._ensure_connected(username, password)

        # Check Set3DPos support
        self._supports_3d_pos = api.supported(channel, "ptz_3d_zoom")

        # If reolink_aio doesn't know about ptz_3d_zoom yet, probe directly
        if not self._supports_3d_pos:
            try:
                result = await api.send(
                    [{"cmd": "Get3DPos", "action": 1, "param": {"channel": channel}}],
                    expected_response_type="json",
                )
                if result and result[0].get("code") == 0:
                    self._supports_3d_pos = True
            except Exception:
                self._supports_3d_pos = False

        # Baichuan always reports both Ppos and Tpos
        self._has_tilt = True

        logger.info(
            "Camera %s capabilities: Set3DPos=%s, has_tilt=%s",
            self.host, self._supports_3d_pos, self._has_tilt,
        )

    @property
    def supports_3d_pos(self) -> bool:
        return self._supports_3d_pos is True

    @property
    def has_tilt(self) -> bool:
        return self._has_tilt

    async def get_position(self, username: str, password: str, channel: int = 0) -> PtzPosition:
        """Get current PTZ pan/tilt position via Baichuan."""
        try:
            api = await self._ensure_connected(username, password)
            pan = api.ptz_pan_position(channel)
            tilt = api.ptz_tilt_position(channel)
            # Refresh position data
            await api.get_states(cmd_list={"GetPtzCurPos": {channel: 1}})
            pan = api.ptz_pan_position(channel) or 0
            tilt = api.ptz_tilt_position(channel) or 0
            return PtzPosition(pan=pan, tilt=tilt)
        except Exception as e:
            logger.warning("Failed to get position for %s: %s", self.host, e)
            return PtzPosition()

    async def get_zoom_focus(self, username: str, password: str, channel: int = 0) -> ZoomFocus:
        """Get current zoom and focus values."""
        try:
            api = await self._ensure_connected(username, password)
            zf = api.zoom_range(channel)
            zoom = dict(zf.get("zoom", {}))
            focus = dict(zf.get("focus", {}))
            if api.supported(channel, "zoom"):
                await api.get_states(cmd_list={"GetZoomFocus": {channel: 1}})
                zf = api.zoom_range(channel)
                zoom = zf.get("zoom", {})
                focus = zf.get("focus", {})
            else:
                # reolink_aio skips GetZoomFocus when it doesn't detect zoom
                # (e.g. TrackMix), leaving the startup value; ask directly.
                result = await api.send(
                    [{"cmd": "GetZoomFocus", "action": 0, "param": {"channel": channel}}],
                    expected_response_type="json",
                )
                if result and result[0].get("code") == 0:
                    value = result[0]["value"]["ZoomFocus"]
                    zoom["pos"] = value.get("zoom", {}).get("pos", zoom.get("pos", 0))
                    focus["pos"] = value.get("focus", {}).get("pos", focus.get("pos", 0))
            return ZoomFocus(
                zoom_pos=zoom.get("pos", 0),
                zoom_min=zoom.get("min", 0),
                zoom_max=zoom.get("max", 33),
                focus_pos=focus.get("pos", 0),
                focus_min=focus.get("min", 0),
                focus_max=focus.get("max", 255),
            )
        except Exception as e:
            logger.warning("Failed to get zoom/focus for %s: %s", self.host, e)
            return ZoomFocus()

    async def get_presets(self, username: str, password: str, channel: int = 0) -> list[dict]:
        """Get list of PTZ presets."""
        api = await self._ensure_connected(username, password)
        try:
            presets_dict = api.ptz_presets(channel)
            return [
                {"token": str(preset_id), "name": name}
                for name, preset_id in presets_dict.items()
            ]
        except Exception:
            return []

    async def get_stream_resolution(self, username: str, password: str, channel: int = 0) -> StreamResolution:
        """Get stream resolutions via Get3DPos. Caches the result."""
        if self._stream_resolution is not None:
            return self._stream_resolution

        api = await self._ensure_connected(username, password)
        try:
            result = await api.send(
                [{"cmd": "Get3DPos", "action": 1, "param": {"channel": channel}}],
                expected_response_type="json",
            )
            if result and result[0].get("code") == 0:
                pos = result[0]["value"]["3d_pos"]
                main = pos.get("mainStream", {})
                self._stream_resolution = StreamResolution(
                    width=main.get("width", 3840),
                    height=main.get("height", 2160),
                )
        except Exception as e:
            logger.warning("Get3DPos failed for %s: %s", self.host, e)

        if self._stream_resolution is None:
            self._stream_resolution = StreamResolution()

        return self._stream_resolution

    async def set_3d_pos(
        self,
        username: str,
        password: str,
        pos_x: int,
        pos_y: int,
        pos_width: int,
        pos_height: int,
        stream_width: int,
        stream_height: int,
        speed: int = 20,
        channel: int = 0,
    ) -> bool:
        """Send a 3D zoom command (Set3DPos)."""
        api = await self._ensure_connected(username, password)
        try:
            await api.send_setting(
                [
                    {
                        "cmd": "Set3DPos",
                        "action": 0,
                        "param": {
                            "3DPos": {
                                "channel": channel,
                                "posX": pos_x,
                                "posY": pos_y,
                                "posWidth": pos_width,
                                "posHeight": pos_height,
                                "speed": speed,
                                "width": stream_width,
                                "height": stream_height,
                            }
                        },
                    }
                ]
            )
            return True
        except Exception as e:
            logger.error("Set3DPos failed for %s: %s", self.host, e)
            return False

    async def ptz_control(
        self, username: str, password: str, op: str, speed: int = 25, channel: int = 0
    ) -> bool:
        """Send a PTZ control command."""
        api = await self._ensure_connected(username, password)
        try:
            if self._supports_ptz_speed:
                await api.set_ptz_command(channel, command=op, speed=speed)
            else:
                await api.set_ptz_command(channel, command=op)
            return True
        except NotSupportedError:
            self._supports_ptz_speed = False
            logger.info("Camera %s: PTZ speed not supported, disabling for future commands", self.host)
            try:
                await api.set_ptz_command(channel, command=op)
                return True
            except Exception as e:
                logger.error("PtzCtrl %s failed for %s: %s", op, self.host, e)
                return False
        except Exception as e:
            logger.error("PtzCtrl %s failed for %s: %s", op, self.host, e)
            return False

    async def goto_preset(
        self, username: str, password: str, preset_id: int, speed: int = 25, channel: int = 0
    ) -> bool:
        """Move to a PTZ preset."""
        api = await self._ensure_connected(username, password)
        try:
            if self._supports_ptz_speed:
                await api.set_ptz_command(channel, preset=preset_id, speed=speed)
            else:
                await api.set_ptz_command(channel, preset=preset_id)
            return True
        except NotSupportedError:
            self._supports_ptz_speed = False
            logger.info("Camera %s: PTZ speed not supported, disabling for future commands", self.host)
            try:
                await api.set_ptz_command(channel, preset=preset_id)
                return True
            except Exception as e:
                logger.error("GotoPreset failed for %s: %s", self.host, e)
                return False
        except Exception as e:
            logger.error("GotoPreset failed for %s: %s", self.host, e)
            return False

    async def set_preset(
        self, username: str, password: str, preset_id: int, name: str, channel: int = 0
    ) -> bool:
        """Save current position as a PTZ preset."""
        api = await self._ensure_connected(username, password)
        try:
            await api.send_setting(
                [
                    {
                        "cmd": "PtzCtrl",
                        "action": 0,
                        "param": {
                            "channel": channel,
                            "op": "SetPreset",
                            "id": preset_id,
                            "name": name,
                        },
                    }
                ]
            )
            return True
        except Exception as e:
            logger.error("SetPreset failed for %s: %s", self.host, e)
            return False

    async def remove_preset(
        self, username: str, password: str, preset_id: int, channel: int = 0
    ) -> bool:
        """Delete a PTZ preset."""
        api = await self._ensure_connected(username, password)
        try:
            await api.send_setting(
                [
                    {
                        "cmd": "PtzCtrl",
                        "action": 0,
                        "param": {
                            "channel": channel,
                            "op": "DelPreset",
                            "id": preset_id,
                        },
                    }
                ]
            )
            return True
        except Exception as e:
            logger.error("RemovePreset failed for %s: %s", self.host, e)
            return False

    async def set_zoom(
        self, username: str, password: str, zoom_pos: int, channel: int = 0
    ) -> bool:
        """Set absolute zoom position."""
        api = await self._ensure_connected(username, password)
        try:
            await api.set_zoom(channel, zoom_pos)
            return True
        except NotSupportedError:
            # reolink_aio does not detect zoom on some models (e.g. TrackMix),
            # but the camera still accepts StartZoomFocus directly.
            pass
        except Exception as e:
            logger.error("SetZoom failed for %s: %s", self.host, e)
            return False
        try:
            result = await api.send(
                [{
                    "cmd": "StartZoomFocus",
                    "action": 0,
                    "param": {"ZoomFocus": {"channel": channel, "op": "ZoomPos", "pos": zoom_pos}},
                }],
                expected_response_type="json",
            )
            if result and result[0].get("code") == 0:
                return True
            logger.error("SetZoom failed for %s: %s", self.host, result)
        except Exception as e:
            logger.error("SetZoom failed for %s: %s", self.host, e)
        return False

    async def relative_move_feedback(
        self,
        username: str,
        password: str,
        pan: float,
        tilt: float,
        pan_units: float,
        tilt_units: float,
        move_speed: int = 15,
        channel: int = 0,
    ) -> bool:
        """Implement RelativeMove with ContinuousMove and position feedback.

        pan/tilt are ONVIF FOV offsets (-1..1); pan_units/tilt_units are the
        camera position units spanning the current view (already scaled for
        zoom). Each axis moves in turn, closed-loop, with correction passes.
        """
        for axis, amount, units in (("pan", pan, pan_units), ("tilt", tilt, tilt_units)):
            distance = abs(amount) * units / 2
            tolerance = max(2.0, units * 0.015)
            if distance < tolerance:
                continue
            if axis == "pan":
                ops = ("Right", "Left") if amount > 0 else ("Left", "Right")
            else:
                ops = ("Up", "Down") if amount > 0 else ("Down", "Up")
            await self._move_axis(
                username, password, axis, ops, distance, tolerance, move_speed, channel
            )
        return True

    async def _axis_position(self, username: str, password: str, axis: str, channel: int) -> int:
        pos = await self.get_position(username, password, channel)
        return pos.pan if axis == "pan" else pos.tilt

    async def _move_axis(
        self,
        username: str,
        password: str,
        axis: str,
        ops: tuple[str, str],
        distance: float,
        tolerance: float,
        move_speed: int,
        channel: int,
    ) -> None:
        """Move one axis `distance` units toward ops[0], correcting overshoot.

        Which way the position counter runs differs by model (lake_view's pan
        counts down going right, TrackMix counts up), so it is learned from
        the first movement. The overshoot (polling lag plus coast after Stop)
        is learned and used as lead. Each command also moves some minimum
        distance (~20 units on a TrackMix, whatever the speed), so a correction
        that would land further off than the current error is skipped.
        """
        start = await self._axis_position(username, password, axis, channel)
        current = start
        forward_positive = ops[0] in ("Right", "Up")

        for attempt in range(3):
            direction = self._axis_direction.get(axis)
            if direction is None:
                progress = abs(current - start)
            else:
                progress = (current - start) * (direction if forward_positive else -direction)
            remaining = distance - progress
            if abs(remaining) < tolerance:
                break

            op = ops[0] if remaining > 0 else ops[1]
            speed = move_speed if attempt == 0 else max(1, move_speed // 3)
            lead = self._lead.get(axis, 0.0)
            target = max(abs(remaining) - lead, 1.0)
            if attempt > 0 and self._min_pulse.get(axis, 0.0) >= 2 * abs(remaining):
                break  # smallest possible nudge would land further off
            segment_start = current

            await self.ptz_control(username, password, op, speed, channel)
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                await asyncio.sleep(0.05)
                current = await self._axis_position(username, password, axis, channel)
                if abs(current - segment_start) >= target:
                    break
            await self.ptz_control(username, password, "Stop", channel=channel)
            stopped_at = current

            # Let it coast to rest
            last = None
            for _ in range(20):
                await asyncio.sleep(0.1)
                current = await self._axis_position(username, password, axis, channel)
                if current == last:
                    break
                last = current

            moved = current - segment_start
            if moved != 0 and axis not in self._axis_direction:
                increases = moved > 0
                self._axis_direction[axis] = 1 if increases == (op in ("Right", "Up")) else -1
                logger.info(
                    "Camera %s: %s position %s when moving %s",
                    self.host, axis, "increases" if increases else "decreases", op,
                )
            if target <= 1.0:
                prev = self._min_pulse.get(axis)
                self._min_pulse[axis] = abs(moved) if prev is None else 0.5 * prev + 0.5 * abs(moved)
            else:
                overshoot = max(0.0, abs(moved) - target)
                prev = self._lead.get(axis)
                self._lead[axis] = overshoot if prev is None else 0.5 * prev + 0.5 * overshoot
            logger.info(
                "Camera %s: %s %s speed=%d wanted=%.0f moved=%d coast=%d",
                self.host, axis, op, speed, abs(remaining), abs(moved), abs(current - stopped_at),
            )
