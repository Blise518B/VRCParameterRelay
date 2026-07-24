"""VRC Parameter Relay — remote control panel for VRChat avatar parameters."""
import sys
from pathlib import Path

__version__ = "1.3.1"
APP_NAME = "VRC Parameter Relay"
DATA_DIR_NAME = "VRCParameterRelay"
AUTHOR = "Blise518B"
GITHUB_URL = "https://github.com/Blise518B"


def resource_path(rel: str) -> Path:
    """Path to a bundled resource, both in dev and in a PyInstaller exe."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "vrc_parameter_relay" / rel
    return Path(__file__).parent / rel
