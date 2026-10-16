"""Configuration loading and validation."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class CameraConfig:
    name: str
    host: str
    port: int = 80
    listen_port: int = 8000
    username: str = ""
    password: str = ""
    # FOV size in camera position units at full zoom-out.
    # These are used for position-feedback RelativeMove.
    # Adjust per camera if click-to-move overshoots or undershoots.
    fov_pan_units: int = 170
    fov_tilt_units: int = 95
    # Movement speed for position-feedback moves (1-64).
    # Lower = more precise but slower.
    move_speed: int = 15
    # Optical magnification at calibrated zoom steps, e.g. {0: 1.0, 20: 2.68}.
    # Used to turn Frigate's drag-to-zoom box into an exact zoom step.
    # Without it, zoom is assumed geometric from 1x to max_zoom_magnification.
    zoom_magnification: dict[int, float] = field(default_factory=dict)
    max_zoom_magnification: float = 4.0


@dataclass
class ProxyConfig:
    cameras: list[CameraConfig] = field(default_factory=list)


def load_config(path: str | Path) -> ProxyConfig:
    """Load proxy configuration from a YAML file."""
    with open(path) as f:
        data = yaml.safe_load(f)

    cameras = []
    for cam in data.get("cameras", []):
        cameras.append(
            CameraConfig(
                name=cam["name"],
                host=cam["host"],
                port=cam.get("port", 80),
                listen_port=cam["listen_port"],
                username=cam.get("username", ""),
                password=cam.get("password", ""),
                fov_pan_units=cam.get("fov_pan_units", 170),
                fov_tilt_units=cam.get("fov_tilt_units", 95),
                move_speed=cam.get("move_speed", 15),
                zoom_magnification={
                    int(k): float(v) for k, v in (cam.get("zoom_magnification") or {}).items()
                },
                max_zoom_magnification=float(cam.get("max_zoom_magnification", 4.0)),
            )
        )

    if not cameras:
        raise ValueError("No cameras configured")

    # Check for duplicate listen ports
    ports = [c.listen_port for c in cameras]
    if len(ports) != len(set(ports)):
        raise ValueError("Duplicate listen_port values in configuration")

    return ProxyConfig(cameras=cameras)
