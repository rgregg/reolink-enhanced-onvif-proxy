# Reolink Enhanced ONVIF Proxy — Design Spec

## Problem

Reolink PTZ cameras expose a minimal ONVIF implementation that only supports `ContinuousMove` for pan/tilt and zoom. They do not report `RelativeMove`, `AbsoluteMove`, position information, or FOV-relative translation spaces. This means ONVIF clients like Frigate cannot use autotracking, click-to-move, or absolute zoom positioning with Reolink cameras.

However, Reolink cameras expose these capabilities through their proprietary HTTP API (`Set3DPos`, `GetPtzCurPos`, `GetZoomFocus`, `StartZoomFocus`, `PtzCtrl`). The gap is purely a protocol translation problem.

## Solution

A standalone Python proxy service that presents Reolink cameras as fully-featured ONVIF PTZ devices. ONVIF clients connect to the proxy instead of the camera's native ONVIF port. The proxy translates ONVIF SOAP requests into Reolink HTTP API calls.

## Architecture

```
┌─────────┐   ONVIF SOAP     ┌──────────────────────┐   Reolink HTTP   ┌────────────┐
│ Frigate  │ ◄──────────────► │ reolink-enhanced-    │ ◄──────────────► │ Reolink    │
│ (cam1)   │   port 8001     │ onvif-proxy          │   port 80        │ Camera 1   │
├──────────┤   port 8002     │                      │   port 80        ├────────────┤
│ Frigate  │ ◄──────────────► │ (single process,     │ ◄──────────────► │ Reolink    │
│ (cam2)   │                  │  multi-port)         │                  │ Camera 2   │
└──────────┘                  └──────────────────────┘                  └────────────┘
```

- Single async Python process serves multiple cameras, each on its own ONVIF listen port.
- Video streams (RTSP) go directly from camera to Frigate — the proxy handles only PTZ control.
- Credentials are not stored in config. The proxy extracts username/password from the ONVIF WS-Security `UsernameToken` header and passes them through to the Reolink HTTP API.

## Configuration

```yaml
cameras:
  - name: front_yard
    host: 192.168.1.10
    port: 80
    listen_port: 8001
  - name: back_yard
    host: 192.168.1.11
    port: 80
    listen_port: 8002
```

In Frigate's config, each camera's `onvif.host` points at the proxy machine with the corresponding `listen_port`. Frigate's `onvif.user` and `onvif.password` are the real camera credentials.

## ONVIF Operations

The proxy implements exactly the ONVIF SOAP operations that Frigate calls:

### Initialization (called once on connect)

| Operation | Behavior |
|-----------|----------|
| `GetSystemDateAndTime` | Returns proxy system time. No auth required. |
| `GetProfiles` | Returns a synthetic media profile with PTZ configuration advertising full capabilities. |
| `GetConfigurationOptions` | Returns PTZ space definitions including `RelativePanTiltTranslationSpace` with `TranslationSpaceFov`, `AbsoluteZoomPositionSpace`, continuous spaces, and speed spaces. |
| `GetServiceCapabilities` | Returns PTZ service capabilities. |
| `GetNodes` | Returns a PTZ node with full supported spaces. |
| `GetPresets` | Forwards to Reolink HTTP API, returns presets. |
| `GetStatus` | Queries `GetPtzCurPos` and `GetZoomFocus` from camera, returns position and move status. |

### Runtime (called during operation)

| Operation | Translation |
|-----------|-------------|
| `ContinuousMove` | → `PtzCtrl` with directional op (`Left`, `Right`, `Up`, `Down`, `ZoomInc`, `ZoomDec`) and speed. |
| `RelativeMove` | → `Set3DPos` with computed target rectangle (see translation logic below). |
| `AbsoluteMove` | → `StartZoomFocus` with `ZoomPos` for zoom. Pan/tilt absolute not supported by Reolink — return fault if requested. |
| `Stop` | → `PtzCtrl` with `Stop` op. |
| `GotoPreset` | → `PtzCtrl` with `ToPos` op and preset ID. |

### Capabilities Advertised

The proxy's `GetProfiles` and `GetConfigurationOptions` responses advertise these features, which Frigate detects and enables:

- `pt` — Continuous pan/tilt
- `pt-r` — Relative pan/tilt
- `pt-r-fov` — Relative pan/tilt within FOV (enables autotracking and click-to-move)
- `zoom` — Continuous zoom
- `zoom-r` — Relative zoom
- `zoom-a` — Absolute zoom positioning
- `focus` — Focus control

## RelativeMove Translation Logic

This is the core of the proxy. Frigate sends normalized FOV offsets; the proxy converts them to a `Set3DPos` rectangle.

### Input (from Frigate)

- `pan`: -1.0 to 1.0 (left to right within current FOV)
- `tilt`: -1.0 to 1.0 (down to up within current FOV)
- `zoom`: optional relative zoom change

### Translation

1. On first request, call `Get3DPos` to obtain stream resolution (`width`, `height` for mainStream). Cache the result.
2. Convert relative offset to pixel position within the stream:
   - `targetX = (streamWidth / 2) + (pan * streamWidth / 2)`
   - `targetY = (streamHeight / 2) - (tilt * streamHeight / 2)`
3. Determine box size:
   - No zoom component: use full stream dimensions (`posWidth = streamWidth`, `posHeight = streamHeight`) — camera repositions without zooming.
   - With zoom component: scale the box size. Smaller box = more zoom. `posWidth = streamWidth * (1 - zoom)`, clamped to reasonable bounds.
4. Send `Set3DPos` with `posX`, `posY`, `posWidth`, `posHeight`, `width`, `height`, `speed=20`.

### Notes

- The translation is approximate. `Set3DPos` is an atomic pan+tilt+zoom operation, but the camera's internal algorithm decides the exact framing.
- Frigate's autotracker is iterative — it sends corrections each frame, so small inaccuracies self-correct.
- If the computed box center falls outside stream bounds, clamp to edges.

## Position and Status Reporting

Frigate polls `GetStatus` during autotracking to know when the motor has stopped and to read the current position for calibration.

- `GetStatus` queries `GetPtzCurPos` (pan/tilt as integer values) and `GetZoomFocus` (zoom position) from the Reolink HTTP API on each call.
- `MoveStatus` is tracked internally: set to `MOVING` when a move command is sent, set to `IDLE` when position stabilizes (two consecutive identical position readings) or after a timeout.
- No background polling — queries are lazy, only when Frigate asks.

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Bad credentials | Reolink returns error → proxy returns ONVIF `NotAuthorized` fault |
| Camera unreachable | HTTP timeout → proxy returns ONVIF `DeviceEntity` fault |
| Unsupported operation | Return ONVIF `ActionNotSupported` fault |
| RelativeMove with out-of-bounds target | Clamp to stream bounds, send anyway |
| Camera already moving | Send new command — camera handles interruption |
| `Get3DPos` not yet cached | Call it on first PTZ request, cache result |

## Project Structure

```
reolink-enhanced-onvif-proxy/
├── pyproject.toml
├── Dockerfile
├── docker-compose.example.yml
├── config.example.yml
├── reolink_onvif_proxy/
│   ├── __init__.py
│   ├── main.py              # Entry point, config loading, starts servers
│   ├── config.py            # YAML config parsing
│   ├── onvif_server.py      # SOAP request routing and auth extraction
│   ├── onvif_responses.py   # SOAP XML response builders for each operation
│   ├── reolink_api.py       # Reolink HTTP API client
│   ├── ptz_translator.py    # RelativeMove → Set3DPos math
│   └── state.py             # Per-camera state: position, zoom, move status
└── tests/
    ├── test_ptz_translator.py
    └── test_onvif_responses.py
```

## Dependencies

- `aiohttp` — async HTTP server (serves ONVIF SOAP) and client (calls Reolink API)
- `lxml` — SOAP XML parsing and building
- `pyyaml` — configuration file parsing

No dependency on `python-onvif-zeep` or `reolink_aio`. The ONVIF surface area is small enough (12 operations) to handle with direct XML construction, and the Reolink API calls are simple JSON POSTs.

## Deployment

Docker container with host networking or explicit port mapping:

```yaml
services:
  reolink-onvif-proxy:
    build: .
    volumes:
      - ./config.yml:/config.yml
    network_mode: host
```

Or with explicit ports:

```yaml
services:
  reolink-onvif-proxy:
    build: .
    volumes:
      - ./config.yml:/config.yml
    ports:
      - "8001:8001"
      - "8002:8002"
```

## Testing Strategy

- **Unit tests for `ptz_translator.py`**: verify RelativeMove-to-Set3DPos math with known inputs/outputs, edge cases (bounds clamping, zoom scaling).
- **Unit tests for `onvif_responses.py`**: verify generated SOAP XML matches what Frigate expects.
- **Integration test**: point Frigate at the proxy connected to a real camera, verify autotracking and click-to-move work.

## Future Work (Out of Scope for v1)

- 3D Zoom UI in Frigate (drag-to-select rectangle)
- WS-Discovery for automatic camera detection
- Baichuan protocol backend as alternative to HTTP
- Support for non-Reolink cameras with similar ONVIF gaps
