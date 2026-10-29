"""Write the fixed link's guest page: docs/remote/ for GitHub Pages.

    .venv\\Scripts\\python.exe tools\\build_pages.py           # copy
    .venv\\Scripts\\python.exe tools\\build_pages.py --check   # exit 1 if stale

The page is the app's own guest page — vrc_parameter_relay/web/index.html,
app.js, style.css, the favicon and the bundled fonts — copied byte for byte,
so the fixed link and the tunnel link can never drift apart. On Pages,
app.js sees the #<endpoint id>.<token> and dials the relay over iroh
instead of a websocket (fixed_link.py). The wasm it loads,
docs/remote/static/web_guest/, comes from tools/web_guest/build.py.

Run it after changing anything in the guest page; the copy reaches people
only once it is pushed to main (Pages serves main:/docs).
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "vrc_parameter_relay" / "web"
FONTS = REPO / "vrc_parameter_relay" / "assets" / "fonts"
OUT = REPO / "docs" / "remote"
WASM = OUT / "static" / "web_guest" / "vrcpr_web_guest_bg.wasm"


def files() -> list[tuple[Path, Path]]:
    """(source, destination) for every file of the page."""
    pairs = [(WEB / "index.html", OUT / "index.html")]
    for name in ("app.js", "style.css", "favicon.png"):
        pairs.append((WEB / name, OUT / "static" / name))
    for font in sorted(FONTS.iterdir()):
        if font.suffix.lower() in (".ttf", ".txt"):
            pairs.append((font, OUT / "static" / "fonts" / font.name))
    return pairs


def stale() -> list[str]:
    out = [str(dst.relative_to(REPO)) for src, dst in files()
           if not dst.exists() or dst.read_bytes() != src.read_bytes()]
    if not WASM.exists():
        out.append(f"{WASM.relative_to(REPO)} (run tools/web_guest/build.py)")
    return out


def main() -> int:
    if "--check" in sys.argv:
        behind = stale()
        for path in behind:
            print(f"stale: {path}")
        return 1 if behind else 0
    for src, dst in files():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    # Pages serves the files as they are: no Jekyll pass over docs/
    (REPO / "docs" / ".nojekyll").write_bytes(b"")
    print(f"docs/remote: {len(files())} files")
    if not WASM.exists():
        print("note: no wasm yet — run tools/web_guest/build.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
