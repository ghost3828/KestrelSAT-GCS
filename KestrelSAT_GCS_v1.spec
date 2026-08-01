# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files

# serial_gui.py is the entry point; PyInstaller follows its imports, so this
# spec does not need updating when the source is split across modules.
a = Analysis(
    ['serial_gui.py'],
    pathex=[],
    binaries=[],
    datas=collect_data_files('pyqtgraph'),
    # These were previously passed as --hidden-import flags by build_exe.bat.
    # They live here now so the batch file and a bare
    # "pyinstaller KestrelSAT_GCS_v1.spec" produce the same executable.
    hiddenimports=[
        'PyQt5.QtCore',
        'PyQt5.QtWidgets',
        'pyqtgraph',
        'serial.tools.list_ports',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='KestrelSAT_GCS_v1',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
