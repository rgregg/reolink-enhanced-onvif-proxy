"""ONVIF SOAP XML response builders.

Builds the exact XML responses that Frigate's ONVIF client expects.
"""

from datetime import datetime, timezone

from lxml import etree

# ONVIF namespaces
NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "tptz": "http://www.onvif.org/ver20/ptz/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
}

# PTZ space URIs
CONTINUOUS_PT_SPACE = "http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocityGenericSpace"
CONTINUOUS_ZOOM_SPACE = "http://www.onvif.org/ver10/tptz/ZoomSpaces/VelocityGenericSpace"
RELATIVE_PT_SPACE = "http://www.onvif.org/ver10/tptz/PanTiltSpaces/TranslationGenericSpace"
RELATIVE_PT_FOV_SPACE = "http://www.onvif.org/ver10/tptz/PanTiltSpaces/TranslationSpaceFov"
RELATIVE_ZOOM_SPACE = "http://www.onvif.org/ver10/tptz/ZoomSpaces/TranslationGenericSpace"
ABSOLUTE_PT_SPACE = "http://www.onvif.org/ver10/tptz/PanTiltSpaces/PositionGenericSpace"
ABSOLUTE_ZOOM_SPACE = "http://www.onvif.org/ver10/tptz/ZoomSpaces/PositionGenericSpace"
PT_SPEED_SPACE = "http://www.onvif.org/ver10/tptz/PanTiltSpaces/GenericSpeedSpace"
ZOOM_SPEED_SPACE = "http://www.onvif.org/ver10/tptz/ZoomSpaces/ZoomGenericSpeedSpace"

PROFILE_TOKEN = "000"
PTZ_CONFIG_TOKEN = "PTZConfig_000"
PTZ_NODE_TOKEN = "PTZNode_000"
VIDEO_SOURCE_TOKEN = "VideoSource_000"
VIDEO_ENCODER_TOKEN = "VideoEncoder_000"


def _soap_envelope(body_content: etree._Element) -> bytes:
    """Wrap content in a SOAP envelope."""
    env = etree.Element(f"{{{NS['s']}}}Envelope", nsmap=NS)
    header = etree.SubElement(env, f"{{{NS['s']}}}Header")  # noqa: F841
    body = etree.SubElement(env, f"{{{NS['s']}}}Body")
    body.append(body_content)
    return etree.tostring(env, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def _soap_fault(code: str, reason: str, detail: str = "") -> bytes:
    """Build a SOAP fault response."""
    env = etree.Element(f"{{{NS['s']}}}Envelope", nsmap=NS)
    header = etree.SubElement(env, f"{{{NS['s']}}}Header")  # noqa: F841
    body = etree.SubElement(env, f"{{{NS['s']}}}Body")
    fault = etree.SubElement(body, f"{{{NS['s']}}}Fault")

    code_el = etree.SubElement(fault, f"{{{NS['s']}}}Code")
    value_el = etree.SubElement(code_el, f"{{{NS['s']}}}Value")
    value_el.text = f"s:{code}"

    reason_el = etree.SubElement(fault, f"{{{NS['s']}}}Reason")
    text_el = etree.SubElement(reason_el, f"{{{NS['s']}}}Text")
    text_el.set("{http://www.w3.org/XML/1998/namespace}lang", "en")
    text_el.text = reason

    if detail:
        detail_el = etree.SubElement(fault, f"{{{NS['s']}}}Detail")
        detail_el.text = detail

    return etree.tostring(env, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def fault_not_authorized() -> bytes:
    return _soap_fault("Sender", "Not Authorized", "Authentication failed")


def fault_action_not_supported(action: str) -> bytes:
    return _soap_fault("Sender", "Action not supported", f"The requested action '{action}' is not supported")


def get_capabilities(base_url: str) -> bytes:
    """Response for GetCapabilities — tells the client where to find services."""
    resp = etree.Element(f"{{{NS['tds']}}}GetCapabilitiesResponse")
    caps = etree.SubElement(resp, f"{{{NS['tds']}}}Capabilities")

    # Device capability
    device = etree.SubElement(caps, f"{{{NS['tt']}}}Device")
    etree.SubElement(device, f"{{{NS['tt']}}}XAddr").text = f"{base_url}/onvif/device_service"

    # Media capability
    media = etree.SubElement(caps, f"{{{NS['tt']}}}Media")
    etree.SubElement(media, f"{{{NS['tt']}}}XAddr").text = f"{base_url}/onvif/media_service"

    # PTZ capability
    ptz = etree.SubElement(caps, f"{{{NS['tt']}}}PTZ")
    etree.SubElement(ptz, f"{{{NS['tt']}}}XAddr").text = f"{base_url}/onvif/ptz_service"

    return _soap_envelope(resp)


def get_services(base_url: str) -> bytes:
    """Response for GetServices — lists available ONVIF services with their URLs."""
    resp = etree.Element(f"{{{NS['tds']}}}GetServicesResponse")

    services = [
        ("http://www.onvif.org/ver10/device/wsdl", f"{base_url}/onvif/device_service"),
        ("http://www.onvif.org/ver10/media/wsdl", f"{base_url}/onvif/media_service"),
        ("http://www.onvif.org/ver20/ptz/wsdl", f"{base_url}/onvif/ptz_service"),
    ]

    for namespace, xaddr in services:
        svc = etree.SubElement(resp, f"{{{NS['tds']}}}Service")
        etree.SubElement(svc, f"{{{NS['tds']}}}Namespace").text = namespace
        etree.SubElement(svc, f"{{{NS['tds']}}}XAddr").text = xaddr
        ver = etree.SubElement(svc, f"{{{NS['tds']}}}Version")
        etree.SubElement(ver, f"{{{NS['tt']}}}Major").text = "2"
        etree.SubElement(ver, f"{{{NS['tt']}}}Minor").text = "0"

    return _soap_envelope(resp)


def get_device_information() -> bytes:
    """Response for GetDeviceInformation."""
    resp = etree.Element(f"{{{NS['tds']}}}GetDeviceInformationResponse")
    etree.SubElement(resp, f"{{{NS['tds']}}}Manufacturer").text = "Reolink"
    etree.SubElement(resp, f"{{{NS['tds']}}}Model").text = "Enhanced ONVIF Proxy"
    etree.SubElement(resp, f"{{{NS['tds']}}}FirmwareVersion").text = "0.1.0"
    etree.SubElement(resp, f"{{{NS['tds']}}}SerialNumber").text = "PROXY-001"
    etree.SubElement(resp, f"{{{NS['tds']}}}HardwareId").text = "PROXY"
    return _soap_envelope(resp)


def fault_device_error(message: str) -> bytes:
    return _soap_fault("Receiver", "Device error", message)


def get_video_sources() -> bytes:
    """Response for GetVideoSources."""
    resp = etree.Element(f"{{{NS['trt']}}}GetVideoSourcesResponse")
    source = etree.SubElement(resp, f"{{{NS['trt']}}}VideoSources", token=VIDEO_SOURCE_TOKEN)
    etree.SubElement(source, f"{{{NS['tt']}}}Framerate").text = "25"
    res = etree.SubElement(source, f"{{{NS['tt']}}}Resolution")
    etree.SubElement(res, f"{{{NS['tt']}}}Width").text = "3840"
    etree.SubElement(res, f"{{{NS['tt']}}}Height").text = "2160"
    return _soap_envelope(resp)


def _range_element(parent: etree._Element, tag: str, uri: str, x_min: float, x_max: float, y_min: float | None = None, y_max: float | None = None):
    """Build a PTZ space range element."""
    space = etree.SubElement(parent, f"{{{NS['tt']}}}{tag}")
    uri_el = etree.SubElement(space, f"{{{NS['tt']}}}URI")
    uri_el.text = uri
    x_range = etree.SubElement(space, f"{{{NS['tt']}}}XRange")
    etree.SubElement(x_range, f"{{{NS['tt']}}}Min").text = str(x_min)
    etree.SubElement(x_range, f"{{{NS['tt']}}}Max").text = str(x_max)
    if y_min is not None and y_max is not None:
        y_range = etree.SubElement(space, f"{{{NS['tt']}}}YRange")
        etree.SubElement(y_range, f"{{{NS['tt']}}}Min").text = str(y_min)
        etree.SubElement(y_range, f"{{{NS['tt']}}}Max").text = str(y_max)
    return space


def get_system_date_and_time() -> bytes:
    """Response for GetSystemDateAndTime."""
    now = datetime.now(timezone.utc)
    resp = etree.Element(f"{{{NS['tds']}}}GetSystemDateAndTimeResponse")
    sdt = etree.SubElement(resp, f"{{{NS['tds']}}}SystemDateAndTime")
    etree.SubElement(sdt, f"{{{NS['tt']}}}DateTimeType").text = "Manual"
    etree.SubElement(sdt, f"{{{NS['tt']}}}DaylightSavings").text = "false"

    utc_dt = etree.SubElement(sdt, f"{{{NS['tt']}}}UTCDateTime")
    time_el = etree.SubElement(utc_dt, f"{{{NS['tt']}}}Time")
    etree.SubElement(time_el, f"{{{NS['tt']}}}Hour").text = str(now.hour)
    etree.SubElement(time_el, f"{{{NS['tt']}}}Minute").text = str(now.minute)
    etree.SubElement(time_el, f"{{{NS['tt']}}}Second").text = str(now.second)
    date_el = etree.SubElement(utc_dt, f"{{{NS['tt']}}}Date")
    etree.SubElement(date_el, f"{{{NS['tt']}}}Year").text = str(now.year)
    etree.SubElement(date_el, f"{{{NS['tt']}}}Month").text = str(now.month)
    etree.SubElement(date_el, f"{{{NS['tt']}}}Day").text = str(now.day)

    return _soap_envelope(resp)


def _build_ptz_config(parent: etree._Element):
    """Build PTZConfiguration element advertising full capabilities."""
    ptz = etree.SubElement(parent, f"{{{NS['tt']}}}PTZConfiguration", token=PTZ_CONFIG_TOKEN)
    etree.SubElement(ptz, f"{{{NS['tt']}}}Name").text = "PTZConfig"
    etree.SubElement(ptz, f"{{{NS['tt']}}}UseCount").text = "1"
    etree.SubElement(ptz, f"{{{NS['tt']}}}NodeToken").text = PTZ_NODE_TOKEN
    # Order must match XSD: Absolute, Relative, Continuous, Speed, Timeout
    etree.SubElement(ptz, f"{{{NS['tt']}}}DefaultAbsoluteZoomPositionSpace").text = ABSOLUTE_ZOOM_SPACE
    etree.SubElement(ptz, f"{{{NS['tt']}}}DefaultRelativePanTiltTranslationSpace").text = RELATIVE_PT_SPACE
    etree.SubElement(ptz, f"{{{NS['tt']}}}DefaultRelativeZoomTranslationSpace").text = RELATIVE_ZOOM_SPACE
    etree.SubElement(ptz, f"{{{NS['tt']}}}DefaultContinuousPanTiltVelocitySpace").text = CONTINUOUS_PT_SPACE
    etree.SubElement(ptz, f"{{{NS['tt']}}}DefaultContinuousZoomVelocitySpace").text = CONTINUOUS_ZOOM_SPACE
    etree.SubElement(ptz, f"{{{NS['tt']}}}DefaultPTZTimeout").text = "PT10S"

    default_speed = etree.SubElement(ptz, f"{{{NS['tt']}}}DefaultPTZSpeed")
    pt_speed = etree.SubElement(default_speed, f"{{{NS['tt']}}}PanTilt", x="0.5", y="0.5")
    pt_speed.set("space", PT_SPEED_SPACE)
    zoom_speed = etree.SubElement(default_speed, f"{{{NS['tt']}}}Zoom", x="0.5")
    zoom_speed.set("space", ZOOM_SPEED_SPACE)

    return ptz


def get_profiles() -> bytes:
    """Response for GetProfiles — returns a synthetic profile with PTZ capabilities."""
    resp = etree.Element(f"{{{NS['trt']}}}GetProfilesResponse")
    profile = etree.SubElement(resp, f"{{{NS['trt']}}}Profiles", token=PROFILE_TOKEN, fixed="true")
    etree.SubElement(profile, f"{{{NS['tt']}}}Name").text = "MainStream"

    # Video source config (minimal, just enough for Frigate to accept)
    vsc = etree.SubElement(profile, f"{{{NS['tt']}}}VideoSourceConfiguration", token=VIDEO_SOURCE_TOKEN)
    etree.SubElement(vsc, f"{{{NS['tt']}}}Name").text = "VideoSource"
    etree.SubElement(vsc, f"{{{NS['tt']}}}UseCount").text = "1"
    etree.SubElement(vsc, f"{{{NS['tt']}}}SourceToken").text = VIDEO_SOURCE_TOKEN
    bounds = etree.SubElement(vsc, f"{{{NS['tt']}}}Bounds")
    bounds.set("x", "0")
    bounds.set("y", "0")
    bounds.set("width", "3840")
    bounds.set("height", "2160")

    # Video encoder config (minimal)
    vec = etree.SubElement(profile, f"{{{NS['tt']}}}VideoEncoderConfiguration", token=VIDEO_ENCODER_TOKEN)
    etree.SubElement(vec, f"{{{NS['tt']}}}Name").text = "VideoEncoder"
    etree.SubElement(vec, f"{{{NS['tt']}}}UseCount").text = "1"
    etree.SubElement(vec, f"{{{NS['tt']}}}Encoding").text = "H264"
    res = etree.SubElement(vec, f"{{{NS['tt']}}}Resolution")
    etree.SubElement(res, f"{{{NS['tt']}}}Width").text = "3840"
    etree.SubElement(res, f"{{{NS['tt']}}}Height").text = "2160"

    # PTZ configuration — this is the important part
    _build_ptz_config(profile)

    return _soap_envelope(resp)


def get_configuration_options() -> bytes:
    """Response for GetConfigurationOptions — defines PTZ space ranges."""
    resp = etree.Element(f"{{{NS['tptz']}}}GetConfigurationOptionsResponse")
    opts = etree.SubElement(resp, f"{{{NS['tptz']}}}PTZConfigurationOptions")
    spaces = etree.SubElement(opts, f"{{{NS['tt']}}}Spaces")

    # Order MUST match XSD schema for zeep to parse correctly:
    # 1. AbsolutePanTiltPositionSpace, 2. AbsoluteZoomPositionSpace,
    # 3. RelativePanTiltTranslationSpace, 4. RelativeZoomTranslationSpace,
    # 5. ContinuousPanTiltVelocitySpace, 6. ContinuousZoomVelocitySpace,
    # 7. PanTiltSpeedSpace, 8. ZoomSpeedSpace

    # Absolute zoom position space
    _range_element(spaces, "AbsoluteZoomPositionSpace", ABSOLUTE_ZOOM_SPACE, 0.0, 1.0)

    # Relative pan/tilt translation spaces (generic + FOV)
    _range_element(spaces, "RelativePanTiltTranslationSpace", RELATIVE_PT_SPACE, -1.0, 1.0, -1.0, 1.0)
    _range_element(spaces, "RelativePanTiltTranslationSpace", RELATIVE_PT_FOV_SPACE, -1.0, 1.0, -1.0, 1.0)

    # Relative zoom translation space
    _range_element(spaces, "RelativeZoomTranslationSpace", RELATIVE_ZOOM_SPACE, -1.0, 1.0)

    # Continuous pan/tilt velocity space
    _range_element(spaces, "ContinuousPanTiltVelocitySpace", CONTINUOUS_PT_SPACE, -1.0, 1.0, -1.0, 1.0)

    # Continuous zoom velocity space
    _range_element(spaces, "ContinuousZoomVelocitySpace", CONTINUOUS_ZOOM_SPACE, -1.0, 1.0)

    # Speed spaces
    _range_element(spaces, "PanTiltSpeedSpace", PT_SPEED_SPACE, 0.0, 1.0)
    _range_element(spaces, "ZoomSpeedSpace", ZOOM_SPEED_SPACE, 0.0, 1.0)

    # PTZ timeout range
    timeout = etree.SubElement(opts, f"{{{NS['tt']}}}PTZTimeout")
    etree.SubElement(timeout, f"{{{NS['tt']}}}Min").text = "PT1S"
    etree.SubElement(timeout, f"{{{NS['tt']}}}Max").text = "PT300S"

    return _soap_envelope(resp)


def get_service_capabilities() -> bytes:
    """Response for GetServiceCapabilities."""
    resp = etree.Element(f"{{{NS['tptz']}}}GetServiceCapabilitiesResponse")
    caps = etree.SubElement(resp, f"{{{NS['tptz']}}}Capabilities")
    caps.set("EFlip", "false")
    caps.set("Reverse", "false")
    caps.set("GetCompatibleConfigurations", "false")
    caps.set("MoveStatus", "true")
    caps.set("StatusPosition", "true")
    return _soap_envelope(resp)


def get_nodes() -> bytes:
    """Response for GetNodes."""
    resp = etree.Element(f"{{{NS['tptz']}}}GetNodesResponse")
    node = etree.SubElement(resp, f"{{{NS['tptz']}}}PTZNode", token=PTZ_NODE_TOKEN)
    etree.SubElement(node, f"{{{NS['tt']}}}Name").text = "PTZ Node"

    spaces = etree.SubElement(node, f"{{{NS['tt']}}}SupportedPTZSpaces")
    # Order must match XSD schema for zeep compatibility
    _range_element(spaces, "AbsoluteZoomPositionSpace", ABSOLUTE_ZOOM_SPACE, 0.0, 1.0)
    _range_element(spaces, "RelativePanTiltTranslationSpace", RELATIVE_PT_SPACE, -1.0, 1.0, -1.0, 1.0)
    _range_element(spaces, "RelativePanTiltTranslationSpace", RELATIVE_PT_FOV_SPACE, -1.0, 1.0, -1.0, 1.0)
    _range_element(spaces, "RelativeZoomTranslationSpace", RELATIVE_ZOOM_SPACE, -1.0, 1.0)
    _range_element(spaces, "ContinuousPanTiltVelocitySpace", CONTINUOUS_PT_SPACE, -1.0, 1.0, -1.0, 1.0)
    _range_element(spaces, "ContinuousZoomVelocitySpace", CONTINUOUS_ZOOM_SPACE, -1.0, 1.0)
    _range_element(spaces, "PanTiltSpeedSpace", PT_SPEED_SPACE, 0.0, 1.0)
    _range_element(spaces, "ZoomSpeedSpace", ZOOM_SPEED_SPACE, 0.0, 1.0)

    etree.SubElement(node, f"{{{NS['tt']}}}MaximumNumberOfPresets").text = "64"
    etree.SubElement(node, f"{{{NS['tt']}}}HomeSupported").text = "true"

    return _soap_envelope(resp)


def get_presets(presets: list[dict]) -> bytes:
    """Response for GetPresets."""
    resp = etree.Element(f"{{{NS['tptz']}}}GetPresetsResponse")
    for preset in presets:
        p = etree.SubElement(resp, f"{{{NS['tptz']}}}Preset", token=preset["token"])
        etree.SubElement(p, f"{{{NS['tt']}}}Name").text = preset["name"]
    return _soap_envelope(resp)


def get_status(
    pan: int, tilt: int, zoom_pos: int, zoom_max: int, move_status: str
) -> bytes:
    """Response for GetStatus — current position and move status."""
    resp = etree.Element(f"{{{NS['tptz']}}}GetStatusResponse")
    status = etree.SubElement(resp, f"{{{NS['tptz']}}}PTZStatus")

    # Position
    position = etree.SubElement(status, f"{{{NS['tt']}}}Position")
    pt = etree.SubElement(position, f"{{{NS['tt']}}}PanTilt")
    pt.set("x", str(pan))
    pt.set("y", str(tilt))
    pt.set("space", ABSOLUTE_PT_SPACE)
    z = etree.SubElement(position, f"{{{NS['tt']}}}Zoom")
    z.set("x", str(zoom_pos / max(zoom_max, 1)))
    z.set("space", ABSOLUTE_ZOOM_SPACE)

    # Move status
    ms = etree.SubElement(status, f"{{{NS['tt']}}}MoveStatus")
    etree.SubElement(ms, f"{{{NS['tt']}}}PanTilt").text = move_status
    etree.SubElement(ms, f"{{{NS['tt']}}}Zoom").text = move_status

    # UTC time
    utc = etree.SubElement(status, f"{{{NS['tt']}}}UtcTime")
    utc.text = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return _soap_envelope(resp)


def simple_response(namespace: str, operation: str) -> bytes:
    """Build a simple empty response for void operations (Stop, ContinuousMove, etc.)."""
    resp = etree.Element(f"{{{NS[namespace]}}}{operation}Response")
    return _soap_envelope(resp)
