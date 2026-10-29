# PyInstaller spec — build with:  .venv\Scripts\pyinstaller VRCParameterRelay.spec
# Produces a single self-contained dist\VRCParameterRelay-Windows.exe
# (onefile, windowed). The name is version-independent on purpose: the
# README's download button uses GitHub's releases/latest/download/<name>
# URL, which only works when every release ships the same asset name.

from PyInstaller.utils.hooks import collect_dynamic_libs

a = Analysis(
    ['run.py'],
    pathex=[],
    # openvr_api dll for SteamVR autostart; iroh_ffi for the fixed link
    binaries=collect_dynamic_libs('openvr') + collect_dynamic_libs('iroh'),
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
    name='VRCParameterRelay-Windows',
    icon='vrc_parameter_relay/assets/icon.ico',
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
