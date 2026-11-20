# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（LCL 运行库补全工具）。

把 VC++ / UCRT 运行库一起打进 DLLFix.exe，运行时可释放到启动器目录。
用法：pyinstaller --noconfirm --clean DLLFix.spec
"""
import glob
import os
import sys

ROOT = os.path.abspath(os.getcwd())
ENTRY = os.path.join(ROOT, "dllfix.py")
ICON = os.path.join(ROOT, "assets", "icon.ico")

_PATTERNS = (
    "vcruntime140.dll", "vcruntime140_1.dll", "vcruntime140_2.dll",
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
    "msvcp140_atomic_wait.dll", "concrt140.dll", "ucrtbase.dll",
    "api-ms-win-crt-*.dll",
)

_WIN = os.environ.get("SystemRoot", r"C:\Windows")
_DIRS = (
    sys.base_prefix,
    os.path.join(sys.base_prefix, "DLLs"),
    os.path.join(_WIN, "System32"),
    # Windows SDK 的 UCRT 重分发目录（Win7 缺少 UCRT 时靠它补齐）
    r"C:\Program Files (x86)\Windows Kits\10\Redist\ucrt\DLLs\x64",
    r"C:\Program Files (x86)\Windows Kits\10\Redist\ucrt\DLLs\x86",
)

binaries = []
_seen = set()
for _dir in _DIRS:
    if not os.path.isdir(_dir):
        continue
    for _pat in _PATTERNS:
        for _path in sorted(glob.glob(os.path.join(_dir, _pat))):
            _name = os.path.basename(_path).lower()
            if _name in _seen:
                continue
            _seen.add(_name)
            binaries.append((_path, "."))

# 保持纯 ASCII 输出，避免 Windows 运行器 cp1252 编码报错
if _seen:
    print("[dllfix] bundled runtime DLLs: %d" % len(_seen))
else:
    print("[dllfix] WARNING: no runtime DLLs found (build on Windows).")

a = Analysis(
    [ENTRY],
    pathex=[ROOT],
    binaries=binaries,
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc", "email", "http", "xml",
              "pstats", "pickletools"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="DLLFix",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=ICON if os.path.exists(ICON) else None,
)
