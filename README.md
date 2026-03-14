# Reolink Enhanced ONVIF Proxy

An ONVIF proxy that unlocks advanced PTZ features for Reolink cameras in [Frigate](https://frigate.video) and other ONVIF clients.

## The Problem

Reolink PTZ cameras expose a minimal ONVIF implementation — only basic continuous pan/tilt/zoom. They don't support `RelativeMove`, `AbsoluteMove`, or position reporting via ONVIF, which means:

- **Autotracking doesn't work** — Frigate needs `RelativeMove` with FOV translation
- **Click-to-move doesn't work** — also requires `RelativeMove`
- **Absolute zoom doesn't work** — needs `AbsoluteMove`
- **No position feedback** — camera doesn't report where it's pointing

Reolink cameras *do* support all of this through their proprietary HTTP API — the gap is purely a protocol translation problem.

## The Solution

This proxy sits between Frigate and your Reolink camera. It presents itself as a fully-featured ONVIF PTZ device and translates commands to Reolink's HTTP API behind the scenes.

```
┌─────────┐    ONVIF     ┌─────────────┐   Reolink HTTP   ┌──────────┐
│ Frigate  │ ◄──────────► │   Proxy     │ ◄──────────────► │ Camera   │
│          │  port 8001   │             │    port 80       │          │
└─────────┘               └─────────────┘                  └──────────┘
                     Video (RTSP) goes direct ─────────────────►
```

The proxy handles only PTZ control. Video streams (RTSP) still go directly from the camera to Frigate.

## Features

- **RelativeMove with FOV** → translated to Reolink's `Set3DPos` (3D area zoom)
- **AbsoluteMove zoom** → translated to `StartZoomFocus`
- **ContinuousMove** → passed through to `PtzCtrl`
- **Position reporting** → real pan/tilt/zoom from `GetPtzCurPos` and `GetZoomFocus`
- **Presets** → passed through to camera
- **Multi-camera support** — one proxy instance handles multiple cameras
- **Credential pass-through** — no passwords stored in proxy config; credentials from Frigate's ONVIF config are forwarded to the camera

## Quick Start

### Docker (recommended)

1. Create a `config.yml`:

```yaml
cameras:
  - name: front_yard
    host: 192.168.1.10    # Camera IP
    port: 80              # Camera HTTP port
    listen_port: 8001     # ONVIF port for Frigate to connect to
  - name: back_yard
    host: 192.168.1.11
    port: 80
    listen_port: 8002
```

2. Run with Docker Compose:

```yaml
services:
  reolink-onvif-proxy:
    build: https://github.com/rgregg/reolink-enhanced-onvif-proxy.git
    volumes:
      - ./config.yml:/config.yml:ro
    network_mode: host
    restart: unless-stopped
```

### Without Docker

```bash
pip install .
reolink-onvif-proxy -c config.yml
```

## Frigate Configuration

Point each camera's ONVIF config at the proxy instead of the camera:

```yaml
cameras:
  front_yard:
    ffmpeg:
      inputs:
        - path: rtsp://192.168.1.10/h264Preview_01_main  # Direct to camera
    onvif:
      host: 192.168.1.100   # Proxy IP (or localhost if co-located)
      port: 8001             # Proxy listen_port from config.yml
      user: admin            # Camera credentials — passed through by proxy
      password: your_password
    autotracking:
      enabled: true
```

## How It Works

### RelativeMove Translation

Frigate's autotracker and click-to-move send ONVIF `RelativeMove` commands with normalized FOV offsets (e.g., "move 0.3 right and 0.2 up within the current field of view"). The proxy translates this to Reolink's `Set3DPos` command — a single atomic operation that pans, tilts, and zooms the camera to frame a specified rectangle.

### Position Reporting

The proxy queries the camera's `GetPtzCurPos` and `GetZoomFocus` endpoints when Frigate polls `GetStatus`, providing real-time position and move status feedback needed for autotracking calibration.

### Capabilities Advertised

The proxy advertises these ONVIF capabilities that Frigate detects:

| Capability | Description |
|-----------|-------------|
| `pt-r-fov` | Relative pan/tilt within FOV (autotracking, click-to-move) |
| `zoom-a` | Absolute zoom positioning |
| `zoom-r` | Relative zoom |
| `pt` | Continuous pan/tilt |
| `zoom` | Continuous zoom |
| `focus` | Focus control |

## Supported Cameras

Tested with Reolink PTZ cameras that support the `Set3DPos` HTTP API command. Check if your camera supports it:

```bash
curl -s -X POST "http://CAMERA_IP/api.cgi?cmd=GetAbility&user=USER&password=PASS" \
  -H "Content-Type: application/json" \
  -d '[{"cmd":"GetAbility","action":0,"param":{"User":{"userName":"USER"}}}]' \
  | grep -o "supportPtz3DLocation.*"
```

If you see `supportPtz3DLocation` with a non-zero version, your camera is compatible.

## Configuration Reference

```yaml
cameras:
  - name: string          # Camera name (for logging)
    host: string          # Camera IP address
    port: int             # Camera HTTP API port (default: 80)
    listen_port: int      # Port the proxy listens on for ONVIF connections
```

## License

MIT
