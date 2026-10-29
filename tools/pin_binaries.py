"""Checks — or recomputes — the digests pinned for the binaries the app runs.

  .venv\\Scripts\\python.exe tools\\pin_binaries.py               verify every pin
  .venv\\Scripts\\python.exe tools\\pin_binaries.py cloudflared             re-verify one
  .venv\\Scripts\\python.exe tools\\pin_binaries.py cloudflared 2026.8.0    digests for a bump

cloudflared is downloaded and executed by the app, so it is pinned to an
exact release and verified before it runs. Re-verify before cutting a
release; recompute when bumping a version.

Downloads every pinned build for the full check and keeps none of it. Needs network;
exits 0 when every pin still matches.
"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from vrc_parameter_relay import tunnel

TARGETS = {
    "cloudflared": {
        "version": tunnel.CLOUDFLARED_VERSION,
        "url": "https://github.com/cloudflare/cloudflared/releases/download/{v}/{asset}",
        "assets": {"win32": "cloudflared-windows-amd64.exe",
                   "linux": "cloudflared-linux-amd64"},
        "pinned": tunnel.CLOUDFLARED.sha256,
        "const": "CLOUDFLARED in vrc_parameter_relay/tunnel.py",
    },
}


def sha256_url(url: str) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    req = urllib.request.Request(url, headers={"User-Agent": "VRCParameterRelay"})
    with urllib.request.urlopen(req, timeout=300) as res:
        while True:
            chunk = res.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def report(name: str, version: str) -> bool:
    """Print digests for one target. Returns False if a pin is stale."""
    spec = TARGETS[name]
    current = version == spec["version"]
    print(f"{name} {version}{'   (the version the app pins)' if current else ''}")

    ok = True
    for platform, asset_tpl in spec["assets"].items():
        asset = asset_tpl.format(v=version)
        digest, size = sha256_url(spec["url"].format(v=version, asset=asset))
        print(f"  {platform:>6}: {digest}   {asset} ({size / 1e6:.1f} MB)")
        # the app only defines the digest for the platform it is running on
        if current and platform == sys.platform:
            if digest == spec["pinned"]:
                print(f"  {'':>6}  ✓ matches the pinned digest")
            else:
                print(f"  {'':>6}  ✗ DOES NOT MATCH the pinned digest")
                ok = False
    if not current:
        print(f"  paste into {spec['const']}")
    print()
    return ok


def main() -> None:
    names = [sys.argv[1]] if len(sys.argv) > 1 else list(TARGETS)
    if names[0] not in TARGETS:
        print(f"unknown target {names[0]!r} — pick from: {', '.join(TARGETS)}")
        sys.exit(2)
    version = sys.argv[2] if len(sys.argv) > 2 else None

    ok = True
    for name in names:
        ok &= report(name, version or TARGETS[name]["version"])

    if not ok:
        print("A release was re-cut, or a pin is wrong. Do not ship until you "
              "know which.")
        sys.exit(1)


if __name__ == "__main__":
    main()
