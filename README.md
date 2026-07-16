# VRC Parameter Relay

A remote control panel for your VRChat avatar, made by
[Blise518B](https://github.com/Blise518B). Watch your avatar's parameters
live, build per-avatar boards of toggles, sliders and steppers, and share a
link so friends can flip your toggles from their phone — **no port
forwarding, no router setup, no accounts**.

![VRC Parameter Relay](docs/screenshot.png)

## Highlights

- **Plays nice with other OSC apps** — uses OSCQuery/mDNS to receive VRChat
  data on its own random port. It never binds port 9001, so it coexists
  with VRCFaceTracking, OscGoesBrrr, and anything else.
- **Full parameter list instantly** — discovers VRChat's OSCQuery service
  and pulls every avatar parameter with its type and current value; no
  waiting for values to change.
- **Per-avatar boards** — controls are saved per avatar ID and switch
  automatically when you change avatars.
- **Categories** — boards are a grid of named groups. Drag controls between
  them by the ⠿ grip. Each category has a **lock**: guests can watch, but
  the server rejects their writes to locked controls.
- **Control types** — toggle (Bool), hold button (Bool), slider (Float),
  stepper (Int), with min/max clamping. Bool controls can be **inverted**
  (⇄) when the parameter's logic is backwards — ON then sends OFF.
- **One-click sharing** — starts a Cloudflare quick tunnel pointing at the
  built-in guest web page (`cloudflared` downloads itself on first use).
  Guests see only the controls you added — never the full parameter list —
  and every link carries a secret token.
- **Links that stay put** — "Pause sharing" kicks guests but keeps the URL
  reserved; guest pages reconnect automatically when you resume. Only
  "Reset link" (or closing the app) invalidates it.
- **Permanent link (optional)** — plug a free ngrok authtoken + static
  domain into *Link settings* and your link survives app restarts. Combine
  with auto-resume and it just always works.
- **⚡ YOLO mode (off by default)** — one prominent header toggle that gives
  guests the full live parameter list and control over *everything*
  (clamped to VRChat's OSC ranges, ignores category locks — the confirmation
  dialog spells it out).

## Getting started

1. Grab `VRCParameterRelay.exe` from the
   [latest release](https://github.com/Blise518B/VRCParameterRelay/releases)
   — a single file, nothing to install.
2. Enable OSC in VRChat: *Action Menu → Options → OSC → Enabled*.
3. Start the app (order doesn't matter). VRChat shows a HUD notification
   that it's sending data to *VRC Parameter Relay*.
4. Open the parameter panel with the green pull-tab, double-click a
   parameter, pick a control type — that's your first board.
5. Hit **Share → Start sharing** and send the link to a friend.

First launch takes a few seconds (the exe unpacks itself), and Windows
SmartScreen may complain about the unsigned exe — *More info → Run anyway*.

## How it works

The app advertises itself over mDNS (`_oscjson._tcp` + `_osc._udp`) with a
tiny OSCQuery HTTP server exposing `/avatar`, so VRChat streams
`/avatar/change` and `/avatar/parameters/*` straight to a randomly chosen
UDP port. In the other direction it discovers VRChat's own
`VRChat-Client-*` OSCQuery service to fetch the complete parameter tree and
the real OSC input port. Remote guests connect through a Cloudflare/ngrok
tunnel to a local aiohttp server over WebSockets; every write is validated,
whitelisted, and rate-limited server-side.

Boards and settings live in `%APPDATA%\VRCParameterRelay`.

## Running from source

```
git clone https://github.com/Blise518B/VRCParameterRelay
cd VRCParameterRelay
start.bat            # creates .venv, installs deps, launches
```

Requires Python 3.10+. `build.bat` produces `dist\VRCParameterRelay.exe`
via PyInstaller.

### Testing without VRChat

```
.venv\Scripts\python tools\fake_vrchat.py --switch 30   # pretends to be VRChat
.venv\Scripts\python tools\integration_test.py
```

The fake advertises `VRChat-Client-FAKE` over mDNS, streams parameters,
echoes back everything you set, and can cycle avatars with a
deliberately-laggy OSCQuery tree — the whole pipeline runs against it.
`tools\logic_test.py`, `tools\sync_test.py`, `tools\share_gate_test.py` and
`tools\yolo_test.py` are hermetic (no mDNS) and safe to run while VRChat is
open.

## Notes & limitations

- Windows only for now (the OSC/OSCQuery core is portable; packaging and
  paths assume Windows).
- Quick tunnel links change when the app restarts — that's a Cloudflare
  limitation; use the ngrok static domain for a permanent link.
- VRChat on Windows only exposes its OSCQuery service to apps on the same
  PC, so the app must run on the machine that runs VRChat.

## License

[GPL-3.0](LICENSE) © 2026 Blise518B — free to use, modify and share;
derivatives must stay open source under the same license.
