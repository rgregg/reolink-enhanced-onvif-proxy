"""Reolink API client using reolink_aio (Baichuan + HTTP)."""

import asyncio
import logging
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
            await api.get_states(cmd_list={"GetZoomFocus": {channel: 1}})
            zf = api.zoom_range(channel)
            zoom = zf.get("zoom", {})
            focus = zf.get("focus", {})
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
            await api.set_ptz_command(channel, command=op, speed=speed)
            return True
        except NotSupportedError:
            # Camera doesn't support speed parameter — retry without it
            logger.debug("Camera %s: retrying %s without speed", self.host, op)
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
            await api.set_ptz_command(channel, preset=preset_id, speed=speed)
            return True
        except NotSupportedError:
            try:
                await api.set_ptz_command(channel, preset=preset_id)
                return True
            except Exception as e:
                logger.error("GotoPreset failed for %s: %s", self.host, e)
                return False
        except Exception as e:
            logger.error("GotoPreset failed for %s: %s", self.host, e)
            return False

    async def set_zoom(
        self, username: str, password: str, zoom_pos: int, channel: int = 0
    ) -> bool:
        """Set absolute zoom position."""
        api = await self._ensure_connected(username, password)
        try:
            await api.set_zoom(channel, zoom_pos)
            return True
        except Exception as e:
            logger.error("SetZoom failed for %s: %s", self.host, e)
            return False

    async def relative_move_feedback(
        self,
        username: str,
        password: str,
        pan: float,
        tilt: float,
        speed: float = 1.0,
        fov_pan_units: int = 170,
        fov_tilt_units: int = 95,
        move_speed: int = 15,
        channel: int = 0,
    ) -> bool:
        """Implement RelativeMove using position-feedback ContinuousMove.

        Uses Baichuan for position feedback (both pan and tilt).
        """
        start_pos = await self.get_position(username, password, channel)
        zoom_focus = await self.get_zoom_focus(username, password, channel)

        # Scale FOV by zoom level
        zoom_ratio = zoom_focus.zoom_pos / max(zoom_focus.zoom_max, 1)
        zoom_scale = 1.0 / (1.0 + zoom_ratio * 19)
        effective_pan_fov = fov_pan_units * zoom_scale
        effective_tilt_fov = fov_tilt_units * zoom_scale

        logger.debug(
            "Camera %s: zoom_pos=%d/%d, zoom_scale=%.2f, effective_fov=%.0f/%.0f",
            self.host, zoom_focus.zoom_pos, zoom_focus.zoom_max,
            zoom_scale, effective_pan_fov, effective_tilt_fov,
        )

        # Reolink Ppos decreases when panning right, so invert pan
        target_pan_delta = -pan * effective_pan_fov / 2
        target_tilt_delta = tilt * effective_tilt_fov / 2

        if abs(target_pan_delta) < 3 and abs(target_tilt_delta) < 3:
            return True

        from .ptz_translator import continuous_move_to_op
        op, _ = continuous_move_to_op(pan, tilt, 0)
        if op == "Stop":
            return True

        cmd_speed = max(1, min(64, int(speed * move_speed)))

        # Full feedback loop — Baichuan gives us both pan and tilt
        await self.ptz_control(username, password, op, cmd_speed, channel)

        target_pan = start_pos.pan + target_pan_delta
        target_tilt = start_pos.tilt + target_tilt_delta

        for _ in range(30):
            await asyncio.sleep(0.1)
            current = await self.get_position(username, password, channel)

            pan_reached = abs(target_pan_delta) < 3 or abs(current.pan - target_pan) < abs(target_pan_delta) * 0.3
            tilt_reached = abs(target_tilt_delta) < 3 or abs(current.tilt - target_tilt) < abs(target_tilt_delta) * 0.3
            pan_overshot = abs(current.pan - start_pos.pan) > abs(target_pan_delta) * 1.2
            tilt_overshot = abs(current.tilt - start_pos.tilt) > abs(target_tilt_delta) * 1.2

            if (pan_reached and tilt_reached) or pan_overshot or tilt_overshot:
                break

        await self.ptz_control(username, password, "Stop", channel=channel)
        return True
