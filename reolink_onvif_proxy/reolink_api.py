"""Reolink HTTP API client for PTZ operations."""

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

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
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
