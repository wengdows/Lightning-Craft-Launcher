#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LCL 运行库补全工具。

把随本工具打包的 VC++ / UCRT 运行库释放到启动器所在目录，
解决部分电脑（尤其是精简版系统、Win7）提示缺少 vcruntime140.dll /
api-ms-win-crt-*.dll 的问题。

用法：
  1. 直接双击：自动补全本工具所在目录；
  2. 把 LightningCraftLauncher.exe 拖到本工具上；
  3. 命令行传目录：DLLFix.exe "D:\\Games\\LCL"
"""
import os
import shutil
import sys

TITLE = "LCL 运行库补全"

# 只释放运行库，不复制 python 自身的 DLL
_PREFIXES = ("vcruntime140", "msvcp140", "concrt140", "ucrtbase",
             "api-ms-win-crt-")
_LAUNCHER = "LightningCraftLauncher.exe"


def _msg(text, icon=0x40):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, TITLE, icon)
    except Exception:
        print(text)


def _resource_dir():
    """onefile 运行时是 _MEIPASS，源码运行时是脚本目录。"""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def _target_dir(argv):
    if len(argv) > 1:
        p = os.path.abspath(argv[1])
        if os.path.isfile(p):
            return os.path.dirname(p)
        if os.path.isdir(p):
            return p
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return base


def _runtime_dlls(src):
    try:
        names = os.listdir(src)
    except OSError:
        return []
    out = []
    for n in names:
        low = n.lower()
        if low.endswith(".dll") and low.startswith(_PREFIXES):
            out.append(n)
    return sorted(out)


def main():
    src = _resource_dir()
    dst = _target_dir(sys.argv)
    dlls = _runtime_dlls(src)

    if not dlls:
        _msg("没有找到可释放的运行库。\n\n"
             "请改为下载 runtime-dlls.zip，解压后把其中的 DLL\n"
             "放到启动器所在目录。", 0x30)
        return 1

    if not os.path.isdir(dst):
        _msg("目标目录不存在：\n%s" % dst, 0x30)
        return 1

    ok, fail = [], []
    for name in dlls:
        try:
            shutil.copyfile(os.path.join(src, name), os.path.join(dst, name))
            ok.append(name)
        except Exception:
            fail.append(name)

    has_launcher = os.path.exists(os.path.join(dst, _LAUNCHER))
    lines = ["目标目录：", dst, "",
             "已补全 %d 个运行库文件。" % len(ok)]
    if fail:
        lines += ["", "失败 %d 个（请右键选择「以管理员身份运行」）：" % len(fail)]
        lines += fail[:8]
    lines += ["", "请重新打开 %s。" % _LAUNCHER if has_launcher
              else "如果启动器不在这个目录，请把它和本工具放在同一目录后重试。"]
    _msg("\n".join(lines), 0x40 if not fail else 0x30)
    return 0 if not fail else 2


if __name__ == "__main__":
    sys.exit(main())
