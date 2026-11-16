# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

目的：把 VC++ 运行库、通用 C 运行库 (UCRT) 以及第三方依赖自带的 DLL
一起打进 EXE，避免在缺少运行库的电脑上提示 dll 缺失。
用法：pyinstaller --noconfirm --clean LightningCraftLauncher.spec
"""
import glob
import os
import sys

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

# 最容易缺失的微软运行时 DLL。
_DLL_PATTERNS = (
    "vcruntime140.dll", "vcruntime140_1.dll", "vcruntime140_2.dll",
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll",
    "concrt140.dll", "ucrtbase.dll", "api-ms-win-crt-*.dll",
)
_DLL_DIRS = (
    sys.base_prefix,
    os.path.join(sys.base_prefix, "DLLs"),
    os.path.join(sys.base_prefix, "Library", "bin"),
    os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"),
)

_seen = set()
for _dir in _DLL_DIRS:
    if not os.path.isdir(_dir):
        continue
    for _pattern in _DLL_PATTERNS:
        for _path in sorted(glob.glob(os.path.join(_dir, _pattern))):
            _name = os.path.basename(_path)
            if _name.lower() in _seen:
                continue
            _seen.add(_name.lower())
            binaries.append((_path, "."))

if _seen:
    print("[spec] 已打包的运行时 DLL: %s" % ", ".join(sorted(_seen)))
else:
    print("[spec] 警告：未找到任何运行时 DLL，请在 Windows 上执行打包。")

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
