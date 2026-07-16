# PyInstaller spec — build with:  .venv\Scripts\pyinstaller VRCParameterRelay.spec
# Produces a single self-contained dist\VRCParameterRelay.exe (onefile, windowed).

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
    name='VRCParameterRelay',
    icon='vrc_parameter_relay/assets/icon.ico',
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
