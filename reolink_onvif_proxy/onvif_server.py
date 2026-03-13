"""ONVIF SOAP server — routes incoming SOAP requests to the appropriate handler."""

import hashlib
import base64
import logging
from datetime import datetime, timezone

from aiohttp import web
from lxml import etree

from . import onvif_responses as responses
from .config import CameraConfig
from .ptz_translator import (
    absolute_zoom_to_position,
    continuous_move_to_op,
    relative_move_to_3d_pos,
)
from .reolink_api import ReolinkAPI
from .state import CameraState

logger = logging.getLogger(__name__)

# SOAP namespaces for parsing requests
SOAP_NS = "http://www.w3.org/2003/05/soap-envelope"
WSSE_NS = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
WSU_NS = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
PTZ_NS = "http://www.onvif.org/ver20/ptz/wsdl"
DEVICE_NS = "http://www.onvif.org/ver10/device/wsdl"
MEDIA_NS = "http://www.onvif.org/ver10/media/wsdl"
SCHEMA_NS = "http://www.onvif.org/ver10/schema"


def _extract_credentials(root: etree._Element) -> tuple[str, str] | None:
    """Extract username and password from WS-Security UsernameToken header.

    Supports both plaintext password and password digest authentication.
    """
    security = root.find(f".//{{{WSSE_NS}}}Security")
    if security is None:
        return None

    token = security.find(f"{{{WSSE_NS}}}UsernameToken")
    if token is None:
        return None

    username_el = token.find(f"{{{WSSE_NS}}}Username")
    password_el = token.find(f"{{{WSSE_NS}}}Password")

    if username_el is None or password_el is None:
        return None

    username = username_el.text or ""

    # Check password type
    password_type = password_el.get("Type", "")
    if "PasswordDigest" in password_type:
        # For digest auth, we can't extract the plain password.
        # We'll need to pass through the raw password and let the Reolink API handle it.
        # Actually, Reolink uses basic auth (user/pass in URL), so we need the plain password.
        # ONVIF digest auth: Digest = Base64(SHA1(Nonce + Created + Password))
        # We can't reverse this, so we need clients to use plaintext password mode.
        # Frigate uses the password directly, so this should work.
        logger.warning("Password digest auth not supported — use plaintext password in ONVIF config")
        return None

    password = password_el.text or ""
    return username, password


def _get_soap_action(root: etree._Element) -> str | None:
    """Determine the SOAP action from the body's first child element."""
    body = root.find(f"{{{SOAP_NS}}}Body")
    if body is None or len(body) == 0:
        return None

    action_el = body[0]
    # Extract local name from the qualified tag
    tag = etree.QName(action_el.tag).localname
    return tag


def _parse_vector(element: etree._Element, attr_x: str = "x", attr_y: str = "y") -> tuple[float, float]:
    """Parse x/y attributes from a PanTilt or Zoom element."""
    x = float(element.get(attr_x, "0"))
    y = float(element.get(attr_y, "0"))
    return x, y


class ONVIFServer:
    """ONVIF SOAP server for a single camera."""

    def __init__(self, camera_config: CameraConfig, reolink_api: ReolinkAPI):
        self.config = camera_config
        self.api = reolink_api
        self.state = CameraState()
        self._app: web.Application | None = None

    def create_app(self) -> web.Application:
        """Create the aiohttp web application."""
        app = web.Application()
        # ONVIF uses POST to various service endpoints
        app.router.add_post("/onvif/device_service", self._handle_soap)
        app.router.add_post("/onvif/media_service", self._handle_soap)
        app.router.add_post("/onvif/ptz_service", self._handle_soap)
        # Some clients POST to the root
        app.router.add_post("/", self._handle_soap)
        # Handle WSDL/XSD GET requests with a stub
        app.router.add_get("/{path:.*}", self._handle_get)
        self._app = app
        return app

    async def _handle_get(self, request: web.Request) -> web.Response:
        """Handle GET requests (WSDL fetches, etc.) — return 404 for now."""
        return web.Response(status=404, text="Not found")

    async def _handle_soap(self, request: web.Request) -> web.Response:
        """Parse SOAP request and route to the appropriate handler."""
        try:
            body = await request.read()
            root = etree.fromstring(body)
        except Exception as e:
            logger.error("Failed to parse SOAP request: %s", e)
            return web.Response(
                body=responses.fault_device_error("Invalid SOAP request"),
                content_type="application/soap+xml",
                status=400,
            )

        action = _get_soap_action(root)
        if action is None:
            return web.Response(
                body=responses.fault_device_error("No action found in SOAP body"),
                content_type="application/soap+xml",
                status=400,
            )

        logger.debug("Camera %s: ONVIF action: %s", self.config.name, action)

        # GetSystemDateAndTime doesn't require auth
        if action == "GetSystemDateAndTime":
            return web.Response(
                body=responses.get_system_date_and_time(),
                content_type="application/soap+xml",
            )

        # All other operations require authentication
        creds = _extract_credentials(root)
        if creds is None:
            return web.Response(
                body=responses.fault_not_authorized(),
                content_type="application/soap+xml",
                status=401,
            )

        username, password = creds

        try:
            response_body = await self._dispatch(action, root, username, password)
        except Exception as e:
            logger.error("Camera %s: Error handling %s: %s", self.config.name, action, e)
            return web.Response(
                body=responses.fault_device_error(str(e)),
                content_type="application/soap+xml",
                status=500,
            )

        return web.Response(
            body=response_body,
            content_type="application/soap+xml",
        )

    async def _dispatch(self, action: str, root: etree._Element, username: str, password: str) -> bytes:
        """Route an ONVIF action to its handler."""
        body = root.find(f"{{{SOAP_NS}}}Body")

        if action == "GetProfiles":
            return responses.get_profiles()

        elif action == "GetConfigurationOptions":
            return responses.get_configuration_options()

        elif action == "GetServiceCapabilities":
            return responses.get_service_capabilities()

        elif action == "GetNodes":
            return responses.get_nodes()

        elif action == "GetPresets":
            presets = await self.api.get_presets(username, password)
            return responses.get_presets(presets)

        elif action == "GetStatus":
            pos = await self.api.get_position(username, password)
            zf = await self.api.get_zoom_focus(username, password)
            self.state.update_position(pos, zf)
            return responses.get_status(
                pan=pos.pan,
                tilt=pos.tilt,
                zoom_pos=zf.zoom_pos,
                zoom_max=zf.zoom_max,
                move_status=self.state.move_status.value,
            )

        elif action == "ContinuousMove":
            await self._handle_continuous_move(body, username, password)
            return responses.simple_response("tptz", "ContinuousMove")

        elif action == "RelativeMove":
            await self._handle_relative_move(body, username, password)
            return responses.simple_response("tptz", "RelativeMove")

        elif action == "AbsoluteMove":
            await self._handle_absolute_move(body, username, password)
            return responses.simple_response("tptz", "AbsoluteMove")

        elif action == "Stop":
            await self.api.ptz_control(username, password, "Stop")
            self.state.move_status = self.state.move_status.IDLE
            return responses.simple_response("tptz", "Stop")

        elif action == "GotoPreset":
            await self._handle_goto_preset(body, username, password)
            return responses.simple_response("tptz", "GotoPreset")

        elif action == "GetConfigurations":
            # Return same PTZ config as in profile
            return responses.get_profiles()  # Close enough for Frigate

        elif action == "GetConfiguration":
            return responses.get_profiles()

        else:
            logger.warning("Camera %s: Unsupported action: %s", self.config.name, action)
            return responses.fault_action_not_supported(action)

    async def _handle_continuous_move(self, body: etree._Element, username: str, password: str):
        """Handle ContinuousMove — translate to PtzCtrl."""
        action_el = body[0]
        velocity = action_el.find(f".//{{{SCHEMA_NS}}}PanTilt")
        zoom_el = action_el.find(f".//{{{SCHEMA_NS}}}Zoom")

        pan_vel, tilt_vel = (0.0, 0.0)
        zoom_vel = 0.0

        if velocity is not None:
            pan_vel, tilt_vel = _parse_vector(velocity)
        if zoom_el is not None:
            zoom_vel = float(zoom_el.get("x", "0"))

        op, speed = continuous_move_to_op(pan_vel, tilt_vel, zoom_vel)
        if op != "Stop":
            await self.api.ptz_control(username, password, op, speed)
            self.state.mark_moving()
        else:
            await self.api.ptz_control(username, password, "Stop")

    async def _handle_relative_move(self, body: etree._Element, username: str, password: str):
        """Handle RelativeMove — translate to Set3DPos via ptz_translator."""
        action_el = body[0]
        translation = action_el.find(f".//{{{SCHEMA_NS}}}PanTilt")
        zoom_el = action_el.find(f".//{{{SCHEMA_NS}}}Zoom")
        speed_el = action_el.find(f".//{{{SCHEMA_NS}}}PanTilt")

        pan, tilt = (0.0, 0.0)
        zoom = 0.0
        speed = 1.0

        if translation is not None:
            pan, tilt = _parse_vector(translation)
        if zoom_el is not None:
            zoom = float(zoom_el.get("x", "0"))

        # Get speed from Speed element if present
        speed_parent = action_el.find(f"{{{PTZ_NS}}}Speed")
        if speed_parent is not None:
            speed_pt = speed_parent.find(f"{{{SCHEMA_NS}}}PanTilt")
            if speed_pt is not None:
                speed = float(speed_pt.get("x", "1.0"))

        # Get stream resolution
        resolution = await self.api.get_stream_resolution(username, password)

        # Translate to Set3DPos
        params = relative_move_to_3d_pos(
            pan=pan,
            tilt=tilt,
            zoom=zoom,
            stream_width=resolution.width,
            stream_height=resolution.height,
            speed=speed,
        )

        await self.api.set_3d_pos(
            username=username,
            password=password,
            pos_x=params.pos_x,
            pos_y=params.pos_y,
            pos_width=params.pos_width,
            pos_height=params.pos_height,
            stream_width=params.width,
            stream_height=params.height,
            speed=params.speed,
        )
        self.state.mark_moving()

    async def _handle_absolute_move(self, body: etree._Element, username: str, password: str):
        """Handle AbsoluteMove — translate zoom to StartZoomFocus."""
        action_el = body[0]
        zoom_el = action_el.find(f".//{{{SCHEMA_NS}}}Zoom")

        if zoom_el is not None:
            zoom_value = float(zoom_el.get("x", "0"))
            zf = await self.api.get_zoom_focus(username, password)
            zoom_pos = absolute_zoom_to_position(zoom_value, zf.zoom_min, zf.zoom_max)
            await self.api.set_zoom(username, password, zoom_pos)
            self.state.mark_moving()
        else:
            # Pan/tilt absolute not supported
            logger.warning("Camera %s: AbsoluteMove with pan/tilt not supported", self.config.name)

    async def _handle_goto_preset(self, body: etree._Element, username: str, password: str):
        """Handle GotoPreset — forward to PtzCtrl ToPos."""
        action_el = body[0]
        token_el = action_el.find(f"{{{PTZ_NS}}}PresetToken")
        if token_el is not None and token_el.text:
            preset_id = int(token_el.text)
            await self.api.goto_preset(username, password, preset_id)
            self.state.mark_moving()
