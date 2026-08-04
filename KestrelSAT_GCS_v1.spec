# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files

# serial_gui.py stays the entry point, so PyInstaller follows its imports into
# the kestrelsat package and this spec needs no change when modules move.
a = Analysis(
    ['serial_gui.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('KestrelSAT_logo.png', '.'),
        ('splash_screen.png', '.'),
        # The GPL requires the licence travel with the binary, so ship it
        # inside the exe rather than relying on the source repo.
        ('LICENSE', '.'),
    ] + collect_data_files('pyqtgraph'),
    hiddenimports=[
        'serial.tools.list_ports',
        'PyQt5.QtCore',
        'PyQt5.QtGui',
        'PyQt5.QtWidgets',
        'pyqtgraph',
        'pyqtgraph.graphicsItems',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

splash = Splash(
    'splash_screen.png',
    binaries=a.binaries,
    datas=a.datas,
    text_pos=None,       # no loading text overlay
    text_size=12,
    minify_script=True,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    splash,
    splash.binaries,
    [],
    name='KestrelSAT_GCS',
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
    icon='KestrelSAT_logo.ico',
)
