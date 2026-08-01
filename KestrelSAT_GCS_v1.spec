# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['serial_gui.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('KestrelSAT_logo.png', '.'),
        ('splash_screen.png', '.'),
        ('serial_gui_settings.json', '.'),
    ],
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
