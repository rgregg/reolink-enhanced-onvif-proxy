"""Reolink HTTP API client for PTZ operations."""

import asyncio
import logging
from dataclasses import dataclass

import aiohttp

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
    """Client for Reolink camera HTTP API."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._session: aiohttp.ClientSession | None = None
        self._stream_resolution: StreamResolution | None = None
        self._supports_3d_pos: bool | None = None  # None = not yet probed
        self._has_tilt: bool | None = None  # None = not yet probed

    @property
    def base_url(self) -> str:
        scheme = "https" if self.port == 443 else "http"
        return f"{scheme}://{self.host}:{self.port}"

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # Disable SSL verification for cameras with self-signed certs
            conn = aiohttp.TCPConnector(ssl=False)
            self._session = aiohttp.ClientSession(connector=conn)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _send_command(
        self, cmd: str, params: dict, username: str, password: str, action: int = 0
    ) -> list[dict]:
        """Send a command to the Reolink HTTP API."""
        session = await self._ensure_session()
        url = f"{self.base_url}/api.cgi?cmd={cmd}&user={username}&password={password}"
        body = [{"cmd": cmd, "action": action, "param": params}]

        try:
            async with session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json(content_type=None)
                return data
        except aiohttp.ClientError as e:
            logger.error("Failed to send %s to %s: %s", cmd, self.host, e)
            raise

    async def probe_capabilities(self, username: str, password: str, channel: int = 0) -> None:
        """Probe camera to determine which PTZ features it supports."""
        # Check Set3DPos support via Get3DPos
        data = await self._send_command("Get3DPos", {"channel": channel}, username, password, action=1)
        self._supports_3d_pos = bool(data and data[0].get("code") == 0)

        # Check if camera reports tilt position
        pos = await self.get_position(username, password, channel)
        self._has_tilt = pos.tilt != 0 or True  # We can't tell from one reading

        # Actually check by looking at the position fields returned
        data = await self._send_command(
            "GetPtzCurPos", {"PtzCurPos": {"channel": channel}}, username, password
        )
        if data and data[0].get("code") == 0:
            pos_data = data[0]["value"].get("PtzCurPos", {})
            self._has_tilt = "Tpos" in pos_data

        logger.info(
            "Camera %s capabilities: Set3DPos=%s, has_tilt=%s",
            self.host, self._supports_3d_pos, self._has_tilt,
        )

    @property
    def supports_3d_pos(self) -> bool:
        return self._supports_3d_pos is True

    @property
    def has_tilt(self) -> bool:
        return self._has_tilt is True

    async def relative_move_feedback(
        self,
        username: str,
        password: str,
        pan: float,
        tilt: float,
        speed: float = 1.0,
        channel: int = 0,
    ) -> bool:
        """Implement RelativeMove using position-feedback ContinuousMove.

        Starts moving in the target direction, polls position, and stops
        when the camera has moved approximately the right amount.

        Args:
            pan: -1.0 to 1.0 (left to right within FOV)
            tilt: -1.0 to 1.0 (down to up within FOV)
            speed: 0.0 to 1.0
        """
        # Get starting position
        start_pos = await self.get_position(username, password, channel)

        # Estimate target movement in camera position units
        # Reolink pan range is typically 0-3600 (360 degrees * 10)
        # A full FOV is roughly 60-100 degrees depending on zoom
        # Estimate FOV as ~800 position units (rough approximation)
        fov_pan_units = 800
        fov_tilt_units = 450

        target_pan_delta = pan * fov_pan_units / 2
        target_tilt_delta = tilt * fov_tilt_units / 2

        if abs(target_pan_delta) < 5 and abs(target_tilt_delta) < 5:
            return True  # Movement too small, skip

        # Determine direction
        from .ptz_translator import continuous_move_to_op
        op, cmd_speed = continuous_move_to_op(pan, tilt, 0)
        if op == "Stop":
            return True

        # Scale speed
        cmd_speed = max(1, min(64, int(speed * 40) + 1))

        # Start moving
        await self.ptz_control(username, password, op, cmd_speed, channel)

        # Poll position until we've moved enough or timeout
        max_polls = 30  # 3 seconds max
        poll_interval = 0.1
        target_pan = start_pos.pan + target_pan_delta
        target_tilt = start_pos.tilt + target_tilt_delta

        for _ in range(max_polls):
            await asyncio.sleep(poll_interval)
            current = await self.get_position(username, password, channel)

            pan_done = abs(current.pan - target_pan) < abs(target_pan_delta) * 0.3
            tilt_done = (
                not self._has_tilt
                or abs(current.tilt - target_tilt) < abs(target_tilt_delta) * 0.3
            )

            # Check if we've overshot
            pan_overshot = abs(current.pan - start_pos.pan) > abs(target_pan_delta) * 1.2
            tilt_overshot = self._has_tilt and abs(current.tilt - start_pos.tilt) > abs(target_tilt_delta) * 1.2

            if pan_done or tilt_done or pan_overshot or tilt_overshot:
                break

        # Stop
        await self.ptz_control(username, password, "Stop", channel=channel)
        return True

    async def get_stream_resolution(self, username: str, password: str, channel: int = 0) -> StreamResolution:
        """Get stream resolutions via Get3DPos. Caches the result."""
        if self._stream_resolution is not None:
            return self._stream_resolution

        data = await self._send_command("Get3DPos", {"channel": channel}, username, password, action=1)

        if data and data[0].get("code") == 0:
            pos = data[0]["value"]["3d_pos"]
            main = pos.get("mainStream", {})
            self._stream_resolution = StreamResolution(
                width=main.get("width", 3840),
                height=main.get("height", 2160),
            )
        else:
            logger.warning("Get3DPos failed for %s, using defaults", self.host)
            self._stream_resolution = StreamResolution()

        return self._stream_resolution

    async def get_position(self, username: str, password: str, channel: int = 0) -> PtzPosition:
        """Get current PTZ pan/tilt position."""
        data = await self._send_command(
            "GetPtzCurPos", {"PtzCurPos": {"channel": channel}}, username, password
        )

        if data and data[0].get("code") == 0:
            pos = data[0]["value"].get("PtzCurPos", {})
            return PtzPosition(pan=pos.get("Ppos", 0), tilt=pos.get("Tpos", 0))

        return PtzPosition()

    async def get_zoom_focus(self, username: str, password: str, channel: int = 0) -> ZoomFocus:
        """Get current zoom and focus values."""
        data = await self._send_command("GetZoomFocus", {"channel": channel}, username, password, action=1)

        if data and data[0].get("code") == 0:
            val = data[0]["value"].get("ZoomFocus", {})
            zoom = val.get("zoom", {})
            focus = val.get("focus", {})
            rng = data[0].get("range", {}).get("ZoomFocus", {})
            zoom_range = rng.get("zoom", {}).get("pos", {})
            focus_range = rng.get("focus", {}).get("pos", {})
            return ZoomFocus(
                zoom_pos=zoom.get("pos", 0),
                zoom_min=zoom_range.get("min", 0),
                zoom_max=zoom_range.get("max", 33),
                focus_pos=focus.get("pos", 0),
                focus_min=focus_range.get("min", 0),
                focus_max=focus_range.get("max", 255),
            )

        return ZoomFocus()

    async def get_presets(self, username: str, password: str, channel: int = 0) -> list[dict]:
        """Get list of PTZ presets."""
        data = await self._send_command("GetPtzPreset", {"channel": channel}, username, password)

        presets = []
        if data and data[0].get("code") == 0:
            for preset in data[0]["value"].get("PtzPreset", []):
                if int(preset.get("enable", 0)) == 1:
                    presets.append({
                        "token": str(preset["id"]),
                        "name": preset.get("name", f"Preset {preset['id']}"),
                    })
        return presets

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
        data = await self._send_command(
            "Set3DPos",
            {
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
            username,
            password,
        )

        return bool(data and data[0].get("code") == 0)

    async def ptz_control(
        self, username: str, password: str, op: str, speed: int = 25, channel: int = 0
    ) -> bool:
        """Send a PTZ control command (ContinuousMove, Stop, etc.)."""
        params: dict = {"channel": channel, "op": op}
        if op != "Stop":
            params["speed"] = speed

        data = await self._send_command("PtzCtrl", params, username, password)
        return bool(data and data[0].get("code") == 0)

    async def goto_preset(
        self, username: str, password: str, preset_id: int, speed: int = 25, channel: int = 0
    ) -> bool:
        """Move to a PTZ preset."""
        params: dict = {"channel": channel, "op": "ToPos", "id": preset_id, "speed": speed}
        data = await self._send_command("PtzCtrl", params, username, password)
        return bool(data and data[0].get("code") == 0)

    async def set_zoom(
        self, username: str, password: str, zoom_pos: int, channel: int = 0
    ) -> bool:
        """Set absolute zoom position."""
        data = await self._send_command(
            "StartZoomFocus",
            {"ZoomFocus": {"channel": channel, "op": "ZoomPos", "pos": zoom_pos}},
            username,
            password,
        )
        return bool(data and data[0].get("code") == 0)
