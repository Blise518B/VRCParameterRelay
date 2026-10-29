r"""Superseded 2026-09-28: the vrc-parameter-relay icon now comes from C:\DIY-Projects\_hub\design\icons518.py
(the 518 green style). That script writes every file that carries this icon (its TARGETS table),
so a changed drawing reaches all of them at once. This stub re-runs it for vrc-parameter-relay; the previous
drawing is in version history and in C:\DIY-Projects\_icon-backup-2026-09-28.
"""
import subprocess
import sys
from pathlib import Path

GEN = Path(r"C:\DIY-Projects\_hub\design\icons518.py")
NAME = "vrc-parameter-relay"
FALLBACK_PY = r"C:\DIY-Projects\Deck518\tray\.venv\Scripts\python.exe"   # any 518 venv with PySide6

if not GEN.exists():
    print(f"{GEN} not found - keeping the icon files as they are")
    sys.exit(0)
try:
    import PySide6.QtSvg  # noqa: F401
    py = sys.executable
except ImportError:
    py = FALLBACK_PY
try:
    sys.exit(subprocess.call([py, str(GEN), NAME]))
except OSError as exc:
    print(f"could not run {GEN} ({exc}) - keeping the icon files as they are")
    sys.exit(0)