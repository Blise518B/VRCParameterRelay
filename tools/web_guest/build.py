"""Build the fixed link's browser half: tools/web_guest (iroh in
WebAssembly) -> docs/remote/static/web_guest/, with the notices of every
crate compiled into it.

    .venv\\Scripts\\python.exe tools\\web_guest\\build.py

Needs, once: `rustup target add wasm32-unknown-unknown`,
`cargo install wasm-bindgen-cli --version <the wasm-bindgen pinned in
Cargo.toml>`, and a clang that can build WebAssembly -- iroh's crypto
(ring) compiles C. Windows' usual compilers can't; LLVM's own release can
(clang.exe + llvm-ar.exe + lib/clang/*/include from
clang+llvm-*-x86_64-pc-windows-msvc, unpacked anywhere). Point
VRCPR_WASM_CLANG at that clang.exe (llvm-ar.exe beside it) or have it on
PATH.

Cargo's target dir stays outside the repo (VRCPR_WASM_TARGET, or a temp
dir): it grows past a gigabyte.

Rebuild after changing src/lib.rs or the iroh version -- which must stay the
one the Python bindings carry (iroh 1.2.0 on PyPI is iroh 1.4.1), so the
browser and the relay speak the same QUIC. Then run tools/build_pages.py.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
OUT = REPO / "docs" / "remote" / "static" / "web_guest"
NOTICES = OUT / "THIRD_PARTY_NOTICES.txt"
TARGET = "wasm32-unknown-unknown"
CRATE = "vrcpr_web_guest"

_QUIET = 0x08000000 if os.name == "nt" else 0     # CREATE_NO_WINDOW

MIT_TEXT = """\
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE."""


def _run(cmd: list[str], env: dict | None = None, cwd: Path = HERE) -> str:
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                       creationflags=_QUIET)
    if r.returncode:
        sys.stderr.write(r.stdout[-4000:] + r.stderr[-8000:])
        raise SystemExit(f"failed: {' '.join(cmd)}")
    return r.stdout


def _clang() -> Path:
    found = os.environ.get("VRCPR_WASM_CLANG") or shutil.which("clang")
    if not found:
        raise SystemExit("no clang for WebAssembly: set VRCPR_WASM_CLANG (see this file's top)")
    clang = Path(found)
    if not (clang.parent / ("llvm-ar.exe" if os.name == "nt" else "llvm-ar")).exists():
        raise SystemExit(f"no llvm-ar beside {clang}")
    return clang


def crates() -> list[tuple[str, str, str]]:
    """(crate, version, license) for everything compiled into the wasm."""
    out = _run(["cargo", "tree", "--target", TARGET, "-e", "normal", "--prefix", "none",
                "--no-dedupe", "-f", "{p}|{l}"])
    seen = set()
    for line in out.splitlines():
        if "|" not in line:
            continue
        pkg, lic = line.rsplit("|", 1)
        name, _, version = pkg.split(" (")[0].strip().partition(" v")
        if name and name != CRATE:
            seen.add((name, version, lic.strip() or "see crate"))
    return sorted(seen)


def _mit_or_apache(expr: str) -> bool:
    """True when the crate can be taken under plain MIT or Apache-2.0."""
    if " AND " in expr:
        return False
    alts = re.split(r"\s+OR\s+|\s*/\s*", expr.replace("(", "").replace(")", ""))
    return any(a.strip() in ("MIT", "Apache-2.0") for a in alts)


def _crate_dir(name: str, version: str) -> Path | None:
    home = Path(os.environ.get("CARGO_HOME") or Path.home() / ".cargo")
    dirs = sorted((home / "registry" / "src").glob(f"*/{name}-{version}"))
    return dirs[0] if dirs else None


def _crate_license_files(name: str, version: str, expr: str) -> list[str]:
    """The crate's own license/notice files from the local cargo registry,
    MIT's copy preferred when the crate offers MIT or Apache."""
    folder = _crate_dir(name, version)
    if folder is None:
        return []
    files = sorted(p for p in folder.iterdir()
                   if p.is_file() and re.match(r"(licen[cs]e|copying|notice|copyright)", p.name, re.I))
    if _mit_or_apache(expr):
        mit = [p for p in files if "mit" in p.name.lower()]
        notice = [p for p in files if re.match(r"notice", p.name, re.I)]
        if mit:
            files = mit + notice
    return [p.read_text(encoding="utf-8", errors="replace") for p in files]


def _authors(name: str, version: str) -> str:
    folder = _crate_dir(name, version)
    if folder is not None:
        m = re.search(r'^authors\s*=\s*\[(.*?)\]',
                      (folder / "Cargo.toml").read_text(encoding="utf-8"), re.M | re.S)
        if m:
            names = re.findall(r'"([^"<]+?)\s*(?:<[^>]*>)?"', m.group(1))
            if names:
                return ", ".join(names)
    return f"the {name} authors"


def _notice(name: str, version: str, lic: str) -> list[str]:
    """The crate's own license files; for one that ships none, the MIT
    text in its authors' name when it offers MIT (all of iroh's do)."""
    texts = _crate_license_files(name, version, lic)
    if texts:
        return texts
    if lic.strip() == "Unlicense":
        return ["Released into the public domain (Unlicense): no notice required."]
    if _mit_or_apache(lic):
        return [f"Copyright (c) {_authors(name, version)}\n\n{MIT_TEXT}"]
    return [f"(no license file in the crate; {lic})"]


def write_notices() -> int:
    parts = [f"Third-party notices for {CRATE}_bg.wasm, the fixed link's iroh",
             "(VRC Parameter Relay's own part is MIT). Every crate compiled into it,",
             "its license, and the notice it asks us to pass on. Written by",
             "tools/web_guest/build.py -- don't edit by hand.", ""]
    listed = crates()
    for name, version, lic in listed:
        parts.append("=" * 78)
        parts.append(f"{name} {version} -- {lic}")
        parts.append("=" * 78)
        parts.extend(_notice(name, version, lic))
        parts.append("")
    NOTICES.write_text("\n".join(parts), encoding="utf-8")
    return len(listed)


def main() -> int:
    clang = _clang()
    target_dir = Path(os.environ.get("VRCPR_WASM_TARGET")
                      or Path(tempfile.gettempdir()) / "vrcpr_web_guest_target")
    ar = clang.parent / ("llvm-ar.exe" if os.name == "nt" else "llvm-ar")
    env = {**os.environ, "CARGO_TARGET_DIR": str(target_dir),
           "CC_wasm32_unknown_unknown": str(clang),
           "AR_wasm32_unknown_unknown": str(ar)}
    print("building the wasm ...", flush=True)
    _run(["cargo", "build", "--release"], env=env)
    wasm = target_dir / TARGET / "release" / f"{CRATE}.wasm"
    OUT.mkdir(parents=True, exist_ok=True)
    _run(["wasm-bindgen", str(wasm), "--out-dir", str(OUT), "--target", "web",
          "--no-typescript", "--weak-refs"])
    n = write_notices()
    size = (OUT / f"{CRATE}_bg.wasm").stat().st_size
    print(f"docs/remote/static/web_guest: {size / 1e6:.1f} MB wasm, notices for {n} crates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
