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
            )
        )

    if not cameras:
        raise ValueError("No cameras configured")

    # Check for duplicate listen ports
    ports = [c.listen_port for c in cameras]
    if len(ports) != len(set(ports)):
        raise ValueError("Duplicate listen_port values in configuration")

    return ProxyConfig(cameras=cameras)
