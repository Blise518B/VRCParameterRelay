"""Hermetic test of the third-party binary guards — no network, no downloads.

Covers what stands between "we found a cloudflared/ngrok" and "we ran it":
the working-directory rejection, and the digest check a download has to pass
before it lands under a name the app executes.

Uses a throwaway data dir, so it never touches real settings. Safe to run
while VRChat is open. Exits 0 on success.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ["VRC_PARAMETER_RELAY_DATA_DIR"] = tempfile.mkdtemp(prefix="vrcpr-bin-")

from vrc_parameter_relay import managed_binary, tunnel
from vrc_parameter_relay.managed_binary import Binary


def check(desc: str, cond: bool) -> None:
    if not cond:
        print(f"FAIL {desc}")
        sys.exit(1)
    print(f"OK   {desc}")


def main() -> None:
    original_cwd = Path.cwd()
    original_path = os.environ.get("PATH", "")

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)

        # -- working-directory rejection ---------------------------------------
        # A fake binary is put on PATH for both halves; only the cwd changes,
        # so this holds whether or not Windows' cwd-first lookup is active.
        name = "cloudflared"
        exe_name = tunnel.CLOUDFLARED.exe_name
        planted = tmpdir / exe_name
        planted.write_bytes(b"not really cloudflared")
        planted.chmod(0o755)
        binary = Binary(name, exe_name, "https://example.invalid/x", sha256=None)
        os.environ["PATH"] = f"{tmp}{os.pathsep}{original_path}"
        try:
            os.chdir(tmp)
            check("a binary in the working directory is refused",
                  binary.system_path() is None)
            os.chdir(original_cwd)
            found = binary.system_path()
            check("the same binary elsewhere on PATH is still accepted",
                  found is not None and found.resolve() == planted.resolve())
        finally:
            os.chdir(original_cwd)
            os.environ["PATH"] = original_path

        # -- digest check -------------------------------------------------------
        blob = tmpdir / "download.part"
        blob.write_bytes(b"pretend this is a 54 MB binary")
        real = managed_binary.sha256_file(blob)

        managed_binary.check_digest(blob, real)
        check("a matching digest passes and keeps the file", blob.exists())

        try:
            managed_binary.check_digest(blob, "0" * 64)
            raised = False
        except RuntimeError:
            raised = True
        check("a mismatching digest raises", raised)
        check("a mismatching digest deletes the file", not blob.exists())

        blob.write_bytes(b"pretend this is a 54 MB binary")
        managed_binary.check_digest(blob, None)
        check("no pin means no check", blob.exists())

        # -- verified(): what we would actually execute -------------------------
        bin_dir = tmpdir / "bin"
        bin_dir.mkdir()
        stale = bin_dir / exe_name
        stale.write_bytes(b"stale copy from an older build")
        unpinned = Binary(name, exe_name, "https://example.invalid/x", sha256=None)
        pinned = Binary(name, exe_name, "https://example.invalid/x",
                        sha256=managed_binary.sha256_file(stale))
        wrongly_pinned = Binary(name, exe_name, "https://example.invalid/x",
                                sha256="0" * 64)
        check("an unpinned binary keeps whatever is on disk",
              unpinned.verified(bin_dir) is True)
        check("a copy matching its pin is kept", pinned.verified(bin_dir) is True)
        check("a copy that isn't the pinned build is rejected",
              wrongly_pinned.verified(bin_dir) is False)
        stale.unlink()
        check("a missing copy is rejected", pinned.verified(bin_dir) is False)

    # -- the real pins ---------------------------------------------------------
    for label, binary, version in (("cloudflared", tunnel.CLOUDFLARED,
                                    tunnel.CLOUDFLARED_VERSION),):
        check(f"{label} pins a real sha256",
              isinstance(binary.sha256, str) and len(binary.sha256) == 64
              and all(c in "0123456789abcdef" for c in binary.sha256))
        check(f"{label} downloads an exact version, not 'latest'",
              version in binary.url and "latest" not in binary.url)
        check(f"{label} is fetched over https", binary.url.startswith("https://"))

    check("ngrok is extracted from its release archive",
          tunnel.NGROK.member is not None)

    print("ALL MANAGED-BINARY TESTS PASSED")


if __name__ == "__main__":
    main()
