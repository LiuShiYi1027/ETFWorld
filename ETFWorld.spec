# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path


ROOT = Path(SPECPATH)

a = Analysis(
    ['desktop.py'],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / 'frontend'), 'frontend'),
        (str(ROOT / 'data'), 'data'),
    ],
    hiddenimports=['backend.api.main'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ETFWorld',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name='ETFWorld',
)

app = BUNDLE(
    coll,
    name='ETFWorld.app',
    icon=str(ROOT / 'assets' / 'ETFWorld-icon.png'),
    bundle_identifier='com.etfworld.desktop',
    info_plist={
        'CFBundleDisplayName': 'ETFWorld',
        'NSHighResolutionCapable': True,
    },
)
