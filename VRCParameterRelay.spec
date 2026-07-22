# PyInstaller spec — build with:  .venv\Scripts\pyinstaller VRCParameterRelay.spec
# Produces a single self-contained dist\VRCParameterRelay-<version>.exe
# (onefile, windowed). The version is read from the package so the exe name
# always matches the build.

import re
import pathlib

_init = pathlib.Path("vrc_parameter_relay/__init__.py").read_text(encoding="utf-8")
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', _init).group(1)

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('vrc_parameter_relay/web', 'vrc_parameter_relay/web'),
        ('vrc_parameter_relay/assets', 'vrc_parameter_relay/assets'),
    ],
    hiddenimports=[],
    excludes=['tkinter'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name=f'VRCParameterRelay-{VERSION}',
    icon='vrc_parameter_relay/assets/icon.ico',
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
