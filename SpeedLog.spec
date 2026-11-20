# PyInstaller spec: one-file, windowed SpeedLog.exe with the web UI bundled.
# Build:  pyinstaller --noconfirm --clean SpeedLog.spec
a = Analysis(
    ['launcher.py'],
    pathex=['.'],
    datas=[('web', 'web')],
    hiddenimports=['server', 'speed', 'storage', 'winhelp', 'appserver'],
    excludes=['tkinter', 'unittest', 'pydoc', 'test', 'numpy'],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='SpeedLog',
    console=False,
    icon='speedlog.ico',
    upx=False,
    version='version_info.txt',
)
