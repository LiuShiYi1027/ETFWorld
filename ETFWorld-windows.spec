# -*- mode: python ; coding: utf-8 -*-
# Windows 打包配置（pyinstaller ETFWorld-windows.spec）
# 与 macOS spec 的差异：无 BUNDLE（.app 是 macOS 概念），产出 dist/ETFWorld/ 目录（内含 ETFWorld.exe）
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

# onefile：产出单个便携 ETFWorld.exe（首次启动稍慢，会解压到临时目录）
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ETFWorld',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # 窗口应用，不弹控制台
    icon=str(ROOT / 'assets' / 'ETFWorld.ico'),
)
