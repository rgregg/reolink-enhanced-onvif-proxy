"""Tests for ONVIF SOAP response generation."""

from lxml import etree

from reolink_onvif_proxy.onvif_responses import (
    get_configuration_options,
    get_nodes,
    get_presets,
    get_profiles,
    get_service_capabilities,
    get_status,
    get_system_date_and_time,
    simple_response,
)

NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "tptz": "http://www.onvif.org/ver20/ptz/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
}

FOV_SPACE = "http://www.onvif.org/ver10/tptz/PanTiltSpaces/TranslationSpaceFov"


def _parse(xml_bytes: bytes) -> etree._Element:
    return etree.fromstring(xml_bytes)


class TestGetSystemDateAndTime:
    def test_returns_valid_soap(self):
        root = _parse(get_system_date_and_time())
        body = root.find(f"{{{NS['s']}}}Body")
        assert body is not None
        resp = body[0]
        assert "GetSystemDateAndTimeResponse" in resp.tag


class TestGetProfiles:
    def test_has_ptz_configuration(self):
        root = _parse(get_profiles())
        ptz_config = root.find(f".//{{{NS['tt']}}}PTZConfiguration")
        assert ptz_config is not None
        assert ptz_config.get("token") is not None

    def test_advertises_relative_move(self):
        root = _parse(get_profiles())
        rel_space = root.find(f".//{{{NS['tt']}}}DefaultRelativePanTiltTranslationSpace")
        assert rel_space is not None
        assert rel_space.text is not None

    def test_advertises_absolute_zoom(self):
        root = _parse(get_profiles())
        abs_zoom = root.find(f".//{{{NS['tt']}}}DefaultAbsoluteZoomPositionSpace")
        assert abs_zoom is not None

    def test_has_video_encoder_config(self):
        root = _parse(get_profiles())
        vec = root.find(f".//{{{NS['tt']}}}VideoEncoderConfiguration")
        assert vec is not None


class TestGetConfigurationOptions:
    def test_has_fov_space(self):
        """Must include TranslationSpaceFov for Frigate autotracking."""
        root = _parse(get_configuration_options())
        spaces = root.findall(f".//{{{NS['tt']}}}RelativePanTiltTranslationSpace")
        uris = [s.find(f"{{{NS['tt']}}}URI").text for s in spaces]
        assert FOV_SPACE in uris

    def test_has_continuous_spaces(self):
        root = _parse(get_configuration_options())
        cont = root.findall(f".//{{{NS['tt']}}}ContinuousPanTiltVelocitySpace")
        assert len(cont) >= 1

    def test_has_absolute_zoom_space(self):
        root = _parse(get_configuration_options())
        abs_zoom = root.findall(f".//{{{NS['tt']}}}AbsoluteZoomPositionSpace")
        assert len(abs_zoom) >= 1

    def test_has_relative_zoom_space(self):
        root = _parse(get_configuration_options())
        rel_zoom = root.findall(f".//{{{NS['tt']}}}RelativeZoomTranslationSpace")
        assert len(rel_zoom) >= 1


class TestGetServiceCapabilities:
    def test_move_status_supported(self):
        root = _parse(get_service_capabilities())
        caps = root.find(f".//{{{NS['tptz']}}}Capabilities")
        assert caps.get("MoveStatus") == "true"
        assert caps.get("StatusPosition") == "true"


class TestGetNodes:
    def test_has_fov_space(self):
        root = _parse(get_nodes())
        spaces = root.findall(f".//{{{NS['tt']}}}RelativePanTiltTranslationSpace")
        uris = [s.find(f"{{{NS['tt']}}}URI").text for s in spaces]
        assert FOV_SPACE in uris

    def test_max_presets(self):
        root = _parse(get_nodes())
        max_presets = root.find(f".//{{{NS['tt']}}}MaximumNumberOfPresets")
        assert max_presets is not None
        assert int(max_presets.text) > 0


class TestGetPresets:
    def test_empty_list(self):
        root = _parse(get_presets([]))
        body = root.find(f"{{{NS['s']}}}Body")
        resp = body[0]
        assert "GetPresetsResponse" in resp.tag
        assert len(resp) == 0

    def test_with_presets(self):
        presets = [
            {"token": "1", "name": "Home"},
            {"token": "2", "name": "Gate"},
        ]
        root = _parse(get_presets(presets))
        preset_els = root.findall(f".//{{{NS['tptz']}}}Preset")
        assert len(preset_els) == 2
        assert preset_els[0].get("token") == "1"
        names = [p.find(f"{{{NS['tt']}}}Name").text for p in preset_els]
        assert "Home" in names
        assert "Gate" in names


class TestGetStatus:
    def test_has_position(self):
        root = _parse(get_status(100, 50, 10, 33, "IDLE"))
        pt = root.find(f".//{{{NS['tt']}}}PanTilt")
        assert pt is not None
        assert pt.get("x") == "100"
        assert pt.get("y") == "50"

    def test_has_move_status(self):
        root = _parse(get_status(0, 0, 0, 33, "MOVING"))
        ms = root.find(f".//{{{NS['tt']}}}MoveStatus")
        pt_status = ms.find(f"{{{NS['tt']}}}PanTilt")
        assert pt_status.text == "MOVING"

    def test_has_zoom(self):
        root = _parse(get_status(0, 0, 16, 33, "IDLE"))
        zoom = root.find(f".//{{{NS['tt']}}}Zoom")
        assert zoom is not None
        zoom_val = float(zoom.get("x"))
        assert 0.4 < zoom_val < 0.6  # ~16/33


class TestSimpleResponse:
    def test_generates_valid_soap(self):
        root = _parse(simple_response("tptz", "Stop"))
        body = root.find(f"{{{NS['s']}}}Body")
        assert body is not None
        assert "StopResponse" in body[0].tag
