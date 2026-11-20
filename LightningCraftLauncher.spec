# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（主程序）。

不再手动收集 VC++ / UCRT 运行库，直接用系统自带的。
用法：pyinstaller --noconfirm --clean LightningCraftLauncher.spec
"""
import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.getcwd())
ENTRY = os.path.join(ROOT, "Lightning Craft Launcher.py")

binaries = []
datas = [(os.path.join(ROOT, "assets", "icon.png"), "assets")]
hiddenimports = []

# 第三方依赖的二进制、数据文件和隐藏导入全部收集，
# 防止 Pillow/OpenSSL 等动态库被漏掉。
for _pkg in ("minecraft_launcher_lib", "PIL", "requests", "urllib3",
             "certifi", "charset_normalizer", "idna", "send2trash"):
    _datas, _binaries, _hidden = collect_all(_pkg)
    datas += _datas
    binaries += _binaries
    hiddenimports += _hidden

a = Analysis(
    [ENTRY],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["numpy", "pandas", "matplotlib", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="LightningCraftLauncher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon=os.path.join(ROOT, "assets", "icon.ico"),
)
