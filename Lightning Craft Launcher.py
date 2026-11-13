#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lightning Craft Launcher (LCL) V1.0.1 单文件版（标准库 tkinter 界面）"""
import sys, os, json, re, shlex, shutil, subprocess, threading, queue, weakref, time, traceback
import importlib, importlib.util, importlib.machinery, webbrowser, math, urllib.parse
from datetime import datetime

# ============ 隐藏启动器自身的控制台窗口（Windows） ============
def _hide_own_console():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
        hwnd = k32.GetConsoleWindow()
        if not hwnd:
            return
        buf = (ctypes.c_uint32 * 8)()
        # 仅当控制台只附着本进程（双击启动）时才隐藏，
        # 避免把用户自己从 cmd/PowerShell 打开的终端窗口一起藏掉
        if k32.GetConsoleProcessList(buf, 8) <= 1:
            u32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass

_hide_own_console()

# ============ 程序目录（兼容 PyInstaller） ============
if getattr(sys, "frozen", False):
    PROGRAM_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    PROGRAM_DIR = os.path.dirname(os.path.abspath(__file__))

# ============ 数据目录（可回退到用户目录） ============
def _resolve_data_dir():
    candidates = [
        os.path.join(PROGRAM_DIR, ".LCL"),
        os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "LCL", ".LCL"),
        os.path.join(os.path.expanduser("~"), ".LCL"),
    ]
    for c in candidates:
        try:
            os.makedirs(c, exist_ok=True)
            test = os.path.join(c, ".writetest")
            with open(test, "w", encoding="utf-8") as f:
                f.write("1")
            os.remove(test)
            return c
        except Exception:
            continue
    return os.path.join(os.path.expanduser("~"), ".LCL")

DATA_DIR = _resolve_data_dir()
# 所有依赖库（Minecraft 库 + Python 第三方包）统一存放在 data/dependencies
LIBRARIES_DIR = os.path.join(DATA_DIR, "dependencies")
# 兼容旧版本：如果还存在中文命名的 data/依赖，自动迁移到 data/dependencies
_old_lib_dir = os.path.join(DATA_DIR, "依赖")
if os.path.isdir(_old_lib_dir) and not os.path.exists(LIBRARIES_DIR):
    try:
        os.rename(_old_lib_dir, LIBRARIES_DIR)
    except Exception:
        pass
# 依赖只使用程序依赖目录 data/dependencies：检测时不看系统默认库，
# 缺失的包通过 pip 下载到 data/dependencies，导入时也优先使用该目录。
try:
    os.makedirs(LIBRARIES_DIR, exist_ok=True)
except Exception:
    pass
if os.path.isdir(LIBRARIES_DIR) and LIBRARIES_DIR not in sys.path:
    sys.path.insert(0, LIBRARIES_DIR)

# ============ 依赖检测（tkinter） ============
_PKG_MAP = {"requests": "requests",
            "minecraft_launcher_lib": "minecraft-launcher-lib",
            "send2trash": "send2trash"}

def _check_missing():
    mm, mp = [], []
    for mod, pip_name in _PKG_MAP.items():
        try:
            # 只检测程序依赖目录 data/dependencies，不检测系统默认库
            ok = importlib.machinery.PathFinder.find_spec(
                mod, [LIBRARIES_DIR]) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            ok = False
        if not ok:
            mm.append(mod); mp.append(pip_name)
    return mm, mp

def _dep_window_tk(missing_mods, missing_pips):
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        print("缺少依赖：", ", ".join(missing_mods))
        print("请手动运行：pip install " + " ".join(missing_pips))
        return False

    result = {"ok": False, "done": False, "closed": False}
    log_queue = queue.Queue()
    root = tk.Tk()
    root.title(_t("依赖缺失")); root.geometry("520x420")
    root.resizable(False, False); root.configure(bg="#2b2b3a")
    try:
        root.eval("tk::PlaceWindow . center")
    except Exception:
        pass

    tk.Label(root, text=_t("检测到依赖缺失"), font=("Microsoft YaHei", 16, "bold"),
             fg="#e74c3c", bg="#2b2b3a").pack(pady=(18, 8))
    info = tk.Frame(root, bg="#2b2b3a"); info.pack(fill=tk.X, padx=30)
    for m in missing_mods:
        tk.Label(info, text="• " + m, font=("Microsoft YaHei", 11),
                 fg="#cdd6f4", bg="#2b2b3a", anchor="w").pack(fill=tk.X, pady=1)
    # 估算缺失依赖的下载体积（含 pip 依赖链），让用户有心理预期
    _est_mb = {"requests": 1, "minecraft_launcher_lib": 2, "send2trash": 1}
    _total_mb = sum(_est_mb.get(m, 5) for m in missing_mods)
    tk.Label(info, text=_t("预计需要下载约 %d MB") % _total_mb,
             font=("Microsoft YaHei", 10), fg="#89b4fa", bg="#2b2b3a",
             anchor="w").pack(fill=tk.X, pady=(6, 0))
    log_frame = tk.Frame(root, bg="#2b2b3a")
    log_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
    log = tk.Text(log_frame, height=6, bg="#11111b", fg="#a6e3a1",
                  font=("Consolas", 9), relief="flat", bd=0, highlightthickness=0)
    log.pack(fill=tk.BOTH, expand=True)
    log.tag_config("err", foreground="#e74c3c"); log.config(state="disabled")
    # 进度条按缺失依赖数量计：一开始为 0%，每装完一个涨一格，全部装完即 100%
    total_deps = max(1, len(missing_pips))
    progress = ttk.Progressbar(root, mode="determinate", maximum=total_deps,
                               length=460)
    progress["value"] = 0  # 先不 pack，等真正开始安装时再显示

    def log_write(text, tag=None):
        if result["closed"]:
            return
        try:
            log.config(state="normal")
            if tag:
                log.insert(tk.END, text + "\n", tag)
            else:
                log.insert(tk.END, text + "\n")
            log.config(state="disabled"); log.see(tk.END)
        except tk.TclError:
            pass

    def poll_log():
        if result["closed"]:
            return
        try:
            while True:
                text, tag = log_queue.get_nowait()
                if tag == "__progress__":
                    try:
                        progress["value"] = int(text)
                    except (tk.TclError, ValueError):
                        pass
                else:
                    log_write(text, tag)
        except queue.Empty:
            pass
        try:
            root.after(100, poll_log)
        except tk.TclError:
            pass

    def check_done():
        if result["closed"]:
            return
        if result["done"]:
            try: progress.stop()
            except tk.TclError: pass
            if result["ok"]:
                try: root.after(900, _safe_destroy)
                except tk.TclError: _safe_destroy()
            else:
                try:
                    btn_install.config(state="normal")
                    btn_quit.config(state="normal")
                except tk.TclError:
                    pass
            return
        try:
            root.after(200, check_done)
        except tk.TclError:
            pass

    def _safe_destroy():
        result["closed"] = True
        try: root.destroy()
        except tk.TclError: pass

    btn_frame = tk.Frame(root, bg="#2b2b3a"); btn_frame.pack(pady=10)

    def quit_app():
        _safe_destroy()

    def start_install():
        try:
            btn_install.config(state="disabled")
            btn_quit.config(state="disabled")
        except tk.TclError:
            pass
        # 开始安装时才显示进度条（0%），放在按钮区上方
        try:
            progress.pack(pady=(0, 10), before=btn_frame)
            progress["value"] = 0
        except tk.TclError:
            pass
        log_queue.put((_t("开始安装，请稍候 ..."), None))
        log_queue.put((_t("共 %d 个依赖需要安装。") % len(missing_pips), None))

        def worker():
            ok_all = True
            total = max(1, len(missing_pips))
            for i, pip_name in enumerate(missing_pips, 1):
                if result["closed"]:
                    break
                log_queue.put((_t("(%d/%d) 正在安装 %s ...") % (i, total, pip_name), None))
                try:
                    os.makedirs(LIBRARIES_DIR, exist_ok=True)
                    cf = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                    # --target 把包装进 data/dependencies，不写入系统 site-packages；
                    # --upgrade 保证目标目录里已有旧版本时也能覆盖更新
                    proc = subprocess.Popen(
                        [sys.executable, "-m", "pip", "install",
                         "--target", LIBRARIES_DIR, "--upgrade", pip_name],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace",
                        creationflags=cf)
                    for line in proc.stdout:
                        if result["closed"]:
                            try: proc.terminate()
                            except Exception: pass
                            break
                        line = line.strip()
                        if line:
                            log_queue.put((line, None))
                    proc.wait()
                except Exception as e:
                    log_queue.put((_t("× %s 安装出错：%s") % (pip_name, e), "err"))
                    proc = None
                if proc is None or proc.returncode != 0:
                    ok_all = False
                    log_queue.put((_t("× %s 安装失败") % pip_name, "err"))
                else:
                    log_queue.put((_t("√ %s 安装完成") % pip_name, None))
                # 每成功/失败处理完一个依赖都推进一格，最终走满 100%
                log_queue.put((str(i), "__progress__"))
            if result["closed"]:
                return
            importlib.invalidate_caches()
            if ok_all:
                log_queue.put((_t("\n√ 安装成功，即将启动主程序 ..."), None))
                result["ok"] = True
            else:
                log_queue.put((_t("\n× 安装失败！请检查网络或权限。\n"
                               "建议：开启代理，或以管理员身份运行。"), "err"))
            result["done"] = True

        threading.Thread(target=worker, daemon=True).start()

    btn_quit = tk.Button(btn_frame, text=_t("退出"), width=12, height=2,
                         command=quit_app, bg="#45475a", fg="white",
                         relief="flat", bd=0, activebackground="#585b70",
                         font=("Microsoft YaHei", 10))
    btn_install = tk.Button(btn_frame, text=_t("下载依赖"), width=12, height=2,
                            command=start_install, bg="#89b4fa", fg="#1e1e2e",
                            relief="flat", bd=0, activebackground="#a6c8ff",
                            font=("Microsoft YaHei", 10, "bold"))
    btn_quit.pack(side=tk.LEFT, padx=6); btn_install.pack(side=tk.LEFT, padx=6)
    root.protocol("WM_DELETE_WINDOW", quit_app)
    try: root.after(100, poll_log)
    except tk.TclError: pass
    try: root.after(200, check_done)
    except tk.TclError: pass
    root.mainloop()
    result["closed"] = True
    return result["ok"]

def ensure_dependencies():
    mm, mp = _check_missing()
    if not mm:
        return True
    return _dep_window_tk(mm, mp)

THEMES = {
    "deep_blue": {
        "bg": "#1e1e2e", "nav": "#181825", "panel": "#252536",
        "card": "#313244", "border": "#45475a", "border2": "#585b70",
        "text": "#cdd6f4", "text2": "#a6adc8", "muted": "#8b90ad",
        "dim": "#7f849c", "log_bg": "#11111b", "log_fg": "#a6e3a1",
        "log_fg2": "#b8eeb3", "log_fg3": "#94d3a1",
        "teal": "#94e2d5", "teal2": "#a8eddf",
        "accent": "#89b4fa", "accent2": "#74c7ec",
        "accent_hover": "#a6c8ff", "accent_hover2": "#89dceb",
        "accent_press": "#74a0e0", "accent_press2": "#5eb8e0",
        "red1": "#f38ba8", "red1h": "#f5a0b8",
        "red2": "#e74c3c", "red2h": "#f06050", "red2p": "#c0392b",
        "yellow": "#f9e2af",
    },
    "black": {
        "bg": "#000000", "nav": "#0a0a0a", "panel": "#111114",
        "card": "#1c1c20", "border": "#30363d", "border2": "#484f58",
        "text": "#e6edf3", "text2": "#b1bac4", "muted": "#8b949e",
        "dim": "#6e7681", "log_bg": "#050608", "log_fg": "#7ee787",
        "log_fg2": "#a5f3a5", "log_fg3": "#56d364",
        "teal": "#56d4dd", "teal2": "#76e3ea",
        "accent": "#58a6ff", "accent2": "#39c5cf",
        "accent_hover": "#79bbff", "accent_hover2": "#67e8f9",
        "accent_press": "#388bfd", "accent_press2": "#1f9fb0",
        "red1": "#ff7b72", "red1h": "#ff9b94",
        "red2": "#f85149", "red2h": "#ff6b63", "red2p": "#c93c37",
        "yellow": "#e3b341",
    },
    "white": {
        "bg": "#f6f7f9", "nav": "#e9ebef", "panel": "#ffffff",
        "card": "#e5e7eb", "border": "#cfd3da", "border2": "#b6bbc4",
        "text": "#1f2328", "text2": "#4b5563", "muted": "#6b7280",
        "dim": "#8c959f", "log_bg": "#ffffff", "log_fg": "#1a7f37",
        "log_fg2": "#2f9e44", "log_fg3": "#116329",
        "teal": "#0e7490", "teal2": "#0891b2",
        "accent": "#60a5fa", "accent2": "#7dd3fc",
        "accent_hover": "#93c5fd", "accent_hover2": "#a5defc",
        "accent_press": "#3b82f6", "accent_press2": "#38bdf8",
        "red1": "#dc2626", "red1h": "#ef4444",
        "red2": "#b91c1c", "red2h": "#dc2626", "red2p": "#991b1b",
        "yellow": "#b45309",
    },
}
THEME_ORDER = ["deep_blue", "black", "white"]
THEME_NAMES = {"deep_blue": "深蓝主题", "black": "黑色主题", "white": "白色主题"}
_THEME_NAME = "deep_blue"

# 深蓝主题（基准）色值 -> 语义角色
_BASE_BG_ROLES = {
    "#1e1e2e": "bg", "#181825": "nav", "#252536": "panel", "#252537": "panel",
    "#313244": "card", "#45475a": "border", "#585b70": "border2",
    "#11111b": "log_bg",
    "#89b4fa": "accent", "#74c7ec": "accent2",
    "#a6c8ff": "accent_hover", "#89dceb": "accent_hover2",
    "#74a0e0": "accent_press", "#5eb8e0": "accent_press2",
    "#a6e3a1": "log_fg", "#b8eeb3": "log_fg2", "#94d3a1": "log_fg3",
    "#94e2d5": "teal", "#a8eddf": "teal2", "#f9e2af": "yellow",
    "#f38ba8": "red1", "#f5a0b8": "red1h",
    "#e74c3c": "red2", "#f06050": "red2h", "#c0392b": "red2p",
}
_BASE_TEXT_ROLES = {
    "#cdd6f4": "text", "#a6adc8": "text2", "#8b90ad": "muted", "#7f849c": "dim",
    "#45475a": "muted", "#585b70": "dim",
    "#89b4fa": "accent", "#74c7ec": "accent2",
    "#a6e3a1": "log_fg", "#b8eeb3": "log_fg2", "#94d3a1": "log_fg3",
    "#94e2d5": "teal", "#a8eddf": "teal2", "#f9e2af": "yellow",
    "#f38ba8": "red1", "#f5a0b8": "red1h",
    "#e74c3c": "red2", "#f06050": "red2h", "#c0392b": "red2p",
}
_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")
_QSS_PROP_RE = re.compile(r"([A-Za-z][A-Za-z-]*)\s*:\s*([^;{}]+)")

# ============ 高级参数：数据模型（作用域 + 三态） ============
# 每个分组在“版本独立”作用域下都有三态：
#   follow  跟随全局（默认）
#   custom  启用并自定义（使用 value）
#   off     禁用（明确不附加该参数/功能，区别于“留空”）
ADV_STATE_FOLLOW, ADV_STATE_CUSTOM, ADV_STATE_OFF = "follow", "custom", "off"
ADV_DEFAULTS = {
    "memory": {"mode": "auto", "xms": "", "xmx": ""},  # auto | custom
    "gc": {"mode": "auto", "tuning": ""},              # auto | g1 | zgc
    "jvm_args": "",                                    # java 与主类之间
    "game_args": "",                                   # 主类之后
    "pre_launch": {"text": "", "wait": False},
    "priority": "normal",                              # normal | high
    "gpu": "off",                                      # off | high
    "window": {"width": "", "height": ""},
}
ADV_GROUPS = ("memory", "gc", "jvm_args", "game_args", "pre_launch",
              "priority", "gpu", "window")


def _adv_clone(d):
    return json.loads(json.dumps(d))


DEFAULT_CONFIG = {
    "java_path": "", "jvm_args": "-Xmx4G -Xms1G",
    "extra_game_args": "", "set_chinese_on_first_launch": True,
    "launcher_language": "zh_CN", "title": "Lightning Craft Launcher",
    "theme": "deep_blue",
    # 启动后的游戏窗口标题模板：留空表示不改动，支持 {player} 等占位符
    "game_window_title": "",
    "minecraft_directory": os.path.join(PROGRAM_DIR, ".minecraft"),
    "selected_account": "", "selected_version": "",
    "api_config": {"base_url": "", "is_littleskin": False},
    "authlib_injector_path": "",
    "microsoft_client_id": "",
    "download_source": "mirror",  # "official" | "mirror"
    # CurseForge 官方 API Key（可选）：不填时走免 Key 公共通道
    # （api.curse.tools 社区代理 / mod.mcimirror.top 国内镜像，自动切换）
    "curseforge_api_key": "",
    "enable_animations": True,  # 界面切换动画
    "java_runtime_paths": {},
    # 高级参数默认关闭：未启用时启动逻辑与旧版本完全一致
    "advanced_enabled": False,
    "advanced": _adv_clone(ADV_DEFAULTS),
    # 版本独立设置：{version: {group: {"state": ..., "value": ...}}}
    "version_overrides": {},
}

DEFAULT_MICROSOFT_CLIENT_ID = "c36a9fb6-4f2a-41ff-90bd-ae7cc92031eb"

_LANG = "zh_CN"
_CURRENT_LANG = "zh_CN"

# ============ 启动器自身版本与更新源 ============
APP_VERSION = "1.0.1"
GITHUB_REPO = "wengdows/Lightning-Craft-Launcher"
GITHUB_RELEASES_API = "https://api.github.com/repos/" + GITHUB_REPO + "/releases/latest"
GITHUB_RELEASES_PAGE = "https://github.com/" + GITHUB_REPO + "/releases"

# ============ 陶瓦联机 ============
TERRA_VERSION = "0.4.2"
TERRA_REPO_URL = "https://github.com/burningtnt/Terracotta"
TERRA_LICENSE_URL = TERRA_REPO_URL + "/blob/master/LICENSE"
TERRA_DIR = os.path.join(DATA_DIR, "terracotta")


def _terra_platform_arch():
    """当前系统 / 架构对应的发行包标识（windows / macos / linux / freebsd）。"""
    import platform
    if sys.platform == "win32":
        plat = "windows"
    elif sys.platform == "darwin":
        plat = "macos"
    elif "freebsd" in sys.platform:
        plat = "freebsd"
    else:
        plat = "linux"
    m = platform.machine().lower()
    arch = {"amd64": "x86_64", "x86_64": "x86_64", "x64": "x86_64",
            "arm64": "arm64", "aarch64": "arm64",
            "loongarch64": "loongarch64", "riscv64": "riscv64"}.get(m, m)
    return plat, arch


def _terra_urls():
    """按当前平台/架构生成发行包下载地址（GitHub 官方源 + Gitee 镜像）。"""
    plat, arch = _terra_platform_arch()
    name = "terracotta-%s-%s-%s-pkg.tar.gz" % (TERRA_VERSION, plat, arch)
    return ["%s/releases/download/v%s/%s" % (TERRA_REPO_URL, TERRA_VERSION, name),
            "https://gitee.com/burningtnt/Terracotta/releases/download/v%s/%s"
            % (TERRA_VERSION, name)]


_ROOM_CHARS = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"

# ============ 恒星内网穿透 ============
STELLAR_API = "https://api.stellarfrp.top"
STELLAR_WEB = "https://console.stellarfrp.top"
STELLAR_REGISTER = "https://console.stellarfrp.top/login"
STELLAR_TUNNELS = "https://console.stellarfrp.top/tunnels"
STELLAR_FRPC_LIST_URL = "https://resources.stellarfrp.top/api/fs/list?path=StellarCore/HalalCloud/frpc"
STELLAR_FRPC_DL_BASE = "https://resources.stellarfrp.top/d/StellarCore/HalalCloud/frpc"
STELLAR_FRPC_DIR = os.path.join(DATA_DIR, "stellarfrpc")

# ---------- 业务常量 ----------
# ============ Java 检测（带缓存） ============
_JAVA_CACHE = {}


# Mojang 运行时组件名 -> Java 大版本（用于无法执行 java -version 时推断）
_JAVA_COMPONENT_MAJOR = {
    "jre-legacy": 8, "java-runtime-alpha": 16, "java-runtime-beta": 17,
    "java-runtime-gamma": 17, "java-runtime-delta": 21, "java-runtime-epsilon": 25,
}



_JAVA_DIRS = [r"C:\Program Files\Java", r"C:\Program Files\Eclipse Adoptium",
              r"C:\Program Files\Microsoft\jdk", r"C:\Program Files (x86)\Java",
              os.path.expanduser("~/.jdks")]

_JAVA_EXE = "java.exe" if sys.platform == "win32" else "java"


# 下载 Java 时临时覆盖下载源。Java 运行时固定走官方源（不走 BMCL 镜像），
# 所以这里始终置为 "official"，让 _rewrite_mirror_url 跳过镜像重写。
_JAVA_SOURCE_OVERRIDE = None



# ============ 版本名合法性 ============
_WIN_RESERVED = ({"CON", "PRN", "AUX", "NUL"}
                 | {f"COM{i}" for i in range(1, 10)}
                 | {f"LPT{i}" for i in range(1, 10)})



_LOADER_MAIN_CLASSES = {
    "fabricmc": "net.fabricmc.loader.impl.launch.knot.KnotClient",
    "quiltmc": "org.quiltmc.loader.impl.launch.knot.KnotClient",
}


# 愚人节版本在清单里同样是 snapshot 类型，靠发布日期与已知版本号识别
_APRIL_FOOLS_IDS = {
    "15w14a", "1.rv-pre1", "3d shareware v1.34", "20w14infinite",
    "22w13oneblockatatime", "23w13a_or_b", "24w14potato",
    "25w14craftmine", "26w14a",
}


# ============ Mod 加载器可用性检测（带缓存与超时） ============
_LOADER_CACHE = {}
_LOADER_CACHE_LOCK = threading.Lock()
_LOADER_CACHE_TTL = 1800  # 30 分钟

# ============ 下载源（镜像重写） ============
_DOWNLOAD_SOURCE = "mirror"  # "official" | "mirror"
# 原版游戏资源走 BMCLAPI 镜像时需要重写的官方域名
# 注意：BMCLAPI 的 libraries 与 assets 路径分别带 /maven、/assets 前缀，
# Fabric/Quilt 的 meta 路径带 /fabric-meta、/quilt-meta 前缀，必须按实测规则拼接。
_MIRROR_HOSTS = {
    "https://launchermeta.mojang.com": "https://bmclapi2.bangbang93.com",
    "https://piston-meta.mojang.com": "https://bmclapi2.bangbang93.com",
    "https://piston-data.mojang.com": "https://bmclapi2.bangbang93.com",
    "https://launcher.mojang.com": "https://bmclapi2.bangbang93.com",
    "https://libraries.minecraft.net": "https://bmclapi2.bangbang93.com/maven",
    "https://resources.download.minecraft.net": "https://bmclapi2.bangbang93.com/assets",
    "https://meta.fabricmc.net": "https://bmclapi2.bangbang93.com/fabric-meta",
    "https://meta.quiltmc.org": "https://bmclapi2.bangbang93.com/quilt-meta",
    # NeoForge 官方 Maven 在国内基本不可达，BMCLAPI 的 /maven 可直接代理
    "https://maven.neoforged.net/releases": "https://bmclapi2.bangbang93.com/maven",
}
# BMCLAPI 会 302 到高校镜像节点，这些节点会直接 403 掉 python-requests 的
# 默认 UA，导致下载「静默失败」（返回 False 而不报错），必须带正常 UA。
_HTTP_UA = "LCL/" + APP_VERSION
_orig_requests_get = None
_orig_session_get = None
# 正在进行的流式下载响应：取消时直接关闭，让阻塞中的读取立即断开
_ACTIVE_STREAMS = weakref.WeakSet()
_ACTIVE_LOCK = threading.Lock()
_download_cancelled = threading.Event()


# Java 安装器下载时不会经过 requests 补丁，所以必须改写安装器内嵌的地址。
_NEOFORGE_OFFICIAL = "https://maven.neoforged.net/releases"
_NEOFORGE_MIRROR = "https://bmclapi2.bangbang93.com/maven"
# 安装器还会从 Mojang 的库站下载依赖，镜像模式下同样改走 BMCLAPI /maven
_NEOFORGE_LIBS_HOST = "https://libraries.minecraft.net"
_NEOFORGE_META_URLS = [
    _NEOFORGE_MIRROR + "/net/neoforged/neoforge/maven-metadata.xml",
    _NEOFORGE_OFFICIAL + "/net/neoforged/neoforge/maven-metadata.xml",
    "https://repo1.maven.org/maven2/net/neoforged/neoforge/maven-metadata.xml",
]
_NEOFORGE_VERSIONS = {"time": 0.0, "versions": []}
_NEOFORGE_VERSIONS_LOCK = threading.Lock()

_EN = {
    # 导航
    "关于": "About",
    "返回": "Back",
    "部分加载器检测失败": "Some loaders failed to check",
    # 启动页
    "账号": "Accounts",
    "版本": "Versions",
    "启动游戏": "Launch",
    "账号：未选择": "Account: Not selected",
    "游戏版本：未选择": "Version: Not selected",
    "账号：": "Account: ",
    "游戏版本：": "Version: ",
    "日志": "Log",
    "清空": "Clear",
    "添加正版账号": "Add Microsoft account",
    "第三方登录": "Third-party login",
    "选择玩家角色": "Select player",
    "账号“%s”下有 %d 个玩家角色，请选择要添加的玩家：":
        "Account \"%s\" has %d player characters; choose the one to add:",
    "该账号下还没有玩家角色。\n请先到 LittleSkin 网站创建角色后再添加。":
        "This account has no player characters yet.\nCreate one on the LittleSkin website first.",
    "添加离线账号": "Add offline account",
    "· 使用中": "· In use",
    "离线": "Offline",
    "正版": "Microsoft",
    "正版账号": "Microsoft",
    "第三方": "Third-party",
    "账号“%s”下有多个玩家角色，可多选，每个角色会单独添加为一张卡片：":
        "The account \"%s\" has multiple player characters. Select one or more; "
        "each is added as a separate card:",
    "请至少选择一个玩家角色。": "Please select at least one player character.",
    "这些玩家角色已在列表中：%s": "These player characters are already in the list: %s",
    "已添加 %d 个玩家角色：%s": "Added %d player character(s): %s",
    "已存在，已跳过：%s": "Already exist, skipped: %s",
    "正在启动游戏...\n[再次按下取消此次启动]":
        "Starting game...\n[Press again to cancel this launch]",
    # 下载页
    "版本类型：": "Version type:",
    "正式版": "Release",
    "远古版本": "Old versions",
    "快照版": "Snapshot",
    "测试版": "Snapshots",
    "愚人节版": "April Fools",
    "Mod 加载器：": "Mod loader:",
    "版本名：": "Version name:",
    "加入下载队列": "Add to queue",
    "下载队列：": "Download queue:",
    "就绪": "Ready",
    "开始下载": "Start download",
    "取消下载": "Cancel download",
    "原版": "Vanilla",
    "正在获取版本列表...": "Fetching version list...",
    "获取版本列表失败，切换页面可重试": "Failed to fetch versions, switch page to retry",
    "版本列表加载失败": "Failed to load version list",
    "加载器检测失败：": "Loader check failed: ",
    "（可挂代理后重试）": " (retry with a proxy)",
    "该版本未检测到支持的 Mod 加载器": "No supported mod loader for this version",
    "检测中...": "Detecting...",
    "检测失败": "Detection failed",
    "部分 Mod 加载器检测失败：\n": "Some mod loaders could not be checked:\n",
    "可能是网络问题，可挂代理后重试。\n原版下载不受影响。":
        "This may be a network problem; retry with a proxy.\nVanilla download is not affected.",
    "连接超时": "connection timed out",
    "网络连接失败": "network unreachable",
    "服务器返回错误": "server returned an error",
    "证书/SSL 错误": "SSL error",
    "数据格式异常": "unexpected response format",
    "网络错误": "network error",
    "同一游戏实例只能安装一个 Mod 加载器；需要多个加载器请分别加入下载队列。":
        "One mod loader per game instance; add separate queue entries for multiple loaders.",
    "等待中": "Waiting",
    "等待下载": "Waiting",
    "下载中": "Downloading",
    "任务列表：": "Tasks:",
    "暂无下载任务": "No download tasks",
    "没有正在进行的下载。": "No download in progress.",
    "已取消，正在停止后台任务 ...": "Cancelled. Stopping background tasks ...",
    "可用": "OK",
    "JSON损坏": "JSON corrupted",
    "库文件缺失": "Libraries missing",
    "真实版本：": "Real version: ",
    "加载器：": "Loader: ",
    "取消该任务": "Cancel this task",
    "删除该项任务": "Delete this task",
    "删除所有任务": "Delete all tasks",
    "确定要删除所有下载任务吗？\n正在下载的任务会被取消，下载到一半的版本文件夹会被删除。":
        "Delete all download tasks?\n"
        "Running tasks will be cancelled and half-downloaded version folders will be removed.",
    "已追加到下载队列": "Added to the download queue",
    "正在获取...": "Fetching...",
    "获取失败，可稍后手动下载": "Fetch failed; you can download it manually later",
    "下载 Fabric API": "Download Fabric API",
    "许多 Fabric 模组都需要此前置": "Required by many Fabric mods",
    "正在下载 Fabric API ...": "Downloading Fabric API ...",
    "下载索引文件": "Downloading index files",
    "下载库文件": "Downloading libraries",
    "下载资源文件": "Downloading assets",
    "下载原生库": "Downloading natives",
    "下载 Java 运行时": "Downloading Java runtime",
    "安装 Fabric 加载器": "Installing Fabric loader",
    "安装 NeoForge 加载器": "Installing NeoForge loader",
    "安装 Forge 加载器": "Installing Forge loader",
    "安装 Quilt 加载器": "Installing Quilt loader",
    "解压文件": "Extracting files",
    "下载中...": "Downloading...",
    "正在取消...": "Cancelling...",
    "正在下载": "Downloading",
    "全部完成": "All done",
    "已取消": "Cancelled",
    # 管理版本
    "管理版本": "Manage versions",
    "打开版本文件夹": "Open version folder",
    "打开MOD文件夹": "Open mods folder",
    "重命名": "Rename",
    "删除": "Delete",
    # 个性化
    "个性化": "Appearance",
    "背景颜色：": "Background color:",
    "背景图片：": "Background image:",
    "浏览...": "Browse...",
    "清除": "Clear",
    "启动器标题：": "Launcher title:",
    "恢复默认配置": "Reset to defaults",
    "应用": "Apply",
    "调整界面主题、动画效果与游戏窗口标题":
        "Customize theme, animations and game window title",
    "游戏窗口标题": "Game window title",
    "游戏窗口标题：": "Game window title: ",
    "留空则沿用游戏默认标题；可用占位符："
    "{player} 玩家名 · {version} 实例名称 · {real_version} 实际游戏版本 · "
    "{account_type} 账号类型 · {launcher} 启动器名称":
        "Leave empty to keep the game's default title. "
        "Placeholders: {player} player name, {version} instance name, "
        "{real_version} actual game version, {account_type} account type, "
        "{launcher} launcher name",
    # 设置
    "设置": "Settings",
    "JAVA 虚拟机参数（如 -Xmx4G）：": "JVM arguments (e.g. -Xmx4G):",
    "启动额外配置参数：": "Extra game arguments:",
    "Java 路径（留空则自动检测）：": "Java path (empty = auto detect):",
    "Microsoft Client ID（留空使用内置公共 ID）：":
        "Microsoft Client ID (leave empty to use the built-in public ID):",
    "如何获取？": "How to get?",
    "第一次进入游戏时将语言设为中文": "Set game language to Chinese on first launch",
    "启用界面动画效果": "Enable UI animations",
    "切换页面时使用缩放淡入动画；关闭后立即切换，低配电脑更流畅":
        "Animated page transitions (zoom + fade). Disable for instant, "
        "lighter switching on low-end PCs.",
    "启动器语言：": "Launcher language:",
    "下载源：": "Download source:",
    "使用官方源": "Official source",
    "使用镜像源（BMCLAPI）": "Mirror (BMCLAPI)",
    "镜像源可加速原版资源下载与 Mod 加载器版本检测；Mod 加载器安装包与 Java 运行时仍走官方源。":
        "The mirror speeds up vanilla downloads and loader detection; loader installers and Java runtime still use the official source.",
    "浏览": "Browse",
    "可在启动器内直接下载并安装，完成后会继续启动游戏。":
        "The launcher can download and install it, then continue starting the game.",
    "已检测到的 Java：": "Detected Java:",
    "留空（按版本自动选择）": "Empty (auto select by version)",
    "重新检测": "Rescan",
    "Java 下载源：": "Java download source:",
    "启动器会自动下载并安装到游戏目录的 runtime 中，完成后可直接启动。":
        "The launcher will download and install it into the runtime folder, then you can start the game.",
    "正在下载 Java ": "Downloading Java ",
    "正在安装 Java ": "Installing Java ",
    "Java 安装完成，但未找到可执行文件。":
        "Java installed, but the executable was not found.",
    "当前系统没有可下载的 Java ": "No downloadable Java for this system: ",
    "镜像源下载失败，正在改用官方源重试 ...":
        "Mirror download failed, retrying with the official source ...",
    "准备中...": "Preparing...",
    "可以点击「关闭」后重试，或在设置中手动指定 Java 路径。":
        "Click \"Close\" to retry, or set the Java path manually in Settings.",
    "关闭": "Close",
    # 关于
    "检查更新（密码:1234）": "Check for updates (password: 1234)",
    "作者：wengdows": "Author: wengdows",
    "作者B站主页": "Author's Bilibili",
    # 联机页
    "联机": "Multiplayer",
    "陶瓦联机": "Terracotta",
    "恒星内网穿透（预留）": "Stellar tunneling (reserved)",
    "恒星内网穿透": "Stellar tunneling",
    # 恒星内网穿透
    "登录恒星映射账号以查看流量与签到": "Log in to your StellarFRP account to view traffic and check-in",
    "用户名": "Username",
    "密码": "Password",
    "登录": "Log in",
    "登录中...": "Logging in...",
    "请输入账号和密码": "Please enter username and password",
    "用户名或密码错误": "Incorrect username or password",
    "无法连接（网络问题）": "Cannot connect (network issue)",
    "服务器返回数据异常": "Unexpected data from server",
    "没有账号？先注册": "No account? Register first",
    "官网 console.stellarfrp.top": "console.stellarfrp.top",
    "用户：": "User: ",
    "退出登录": "Log out",
    "流量信息": "Traffic",
    "已使用": "Used",
    "剩余": "Remaining",
    "隧道": "Tunnels",
    "签到状态": "Check-in status",
    "已签到": "Checked in",
    "未签到": "Not checked in",
    "连续签到": "Continuous",
    "奖励": "Reward",
    "刷新间隔：": "Refresh interval: ",
    "立即刷新": "Refresh now",
    "刷新": "Refresh",
    "刷新中...": "Refreshing...",
    "刷新失败：": "Refresh failed: ",
    "基于 StellarFRP 把本地游戏映射到公网，好友可直接加入。":
        "Map your local game to the public Internet via StellarFRP so friends can join directly.",
    "流量": "Traffic",
    "流量信息不可用": "Traffic info unavailable",
    "自动签到成功": "Auto check-in succeeded",
    "签到失败": "Check-in failed",
    "签到失败（网络错误）": "Check-in failed (network error)",
    "获取用户信息失败": "Failed to get user info",
    "登录已过期，请重新登录": "Session expired, please log in again",
    # 恒星联机（隧道）
    "前往联机": "Go online",
    "请先登录": "Please log in first",
    "检测客户端...": "Checking client...",
    "客户端已就绪，正在加载隧道...": "Client ready, loading tunnels...",
    "未检测到客户端，正在自动下载...": "Client not found, downloading...",
    "客户端下载完成，正在加载隧道...": "Client downloaded, loading tunnels...",
    "客户端下载失败：": "Client download failed: ",
    "加载隧道中...": "Loading tunnels...",
    "暂无隧道，请先在控制台创建隧道。": "No tunnels yet. Please create one in the console.",
    "共 %d 条隧道，点击即可联机": "%d tunnel(s), click to connect",
    "未命名": "Unnamed",
    "节点": "Node",
    "本地地址": "Local",
    "远程地址": "Remote",
    "在线": "Online",
    "加载隧道失败：": "Failed to load tunnels: ",
    "客户端不存在，请先下载。": "Client not found, please download it first.",
    "FRPC 客户端缺失": "FRPC client missing",
    "未检测到 FRPC 客户端": "FRPC client not found",
    "恒星内网穿透需要 FRPC 客户端才能启动隧道。":
        "Stellar FRP needs the FRPC client to start tunnels.",
    "点击「立即下载」后，下载进度会显示在启动器内。":
        "After clicking “Download now”, progress will be shown in the launcher.",
    "立即下载": "Download now",
    "稍后": "Later",
    "下载客户端": "Download client",
    "正在下载客户端...": "Downloading client...",
    "客户端下载完成。": "Client downloaded.",
    "未检测到客户端，点击「下载客户端」进行下载。":
        "Client not found. Click “Download client” to download.",
    "正在启动隧道：": "Starting tunnel: ",
    "客户端已退出。": "Client exited.",
    "客户端异常退出（退出码 %d）：": "Client exited abnormally (code %d): ",
    "最后输出": "Last output",
    "正在停止...": "Stopping...",
    "登录已过期，正在重新登录...": "Session expired, re-logging in...",
    "获取客户端列表失败": "Failed to get client list",
    "未找到适合当前系统的客户端": "No client for your system",
    "客户端解压失败": "Failed to extract client",
    "获取隧道列表失败": "Failed to get tunnel list",
    "启动此隧道": "Start this tunnel",
    "用户数据刷新间隔": "User data refresh interval",
    "隧道卡片刷新间隔": "Tunnel list refresh interval",
    "打开启动器立即尝试签到": "Auto check-in on launcher start",
    "退出时关闭内网穿透进程": "Close tunneling process on exit",
    "下载游戏": "Download game",
    "游戏下载": "Game download",
    "导入整合包": "Import modpack",
    "选择整合包文件": "Select modpack file",
    "支持的格式：Modrinth (.mrpack) 与 CurseForge (.zip)": "Supported: Modrinth (.mrpack) and CurseForge (.zip)",
    "实例名称": "Instance name",
    "可选文件": "Optional files",
    "可选文件（默认不勾选，勾选后一并安装）": "Optional files (unchecked by default; check to install)",
    "开始导入": "Start import",
    "取消导入": "Cancel import",
    "正在解析整合包...": "Parsing modpack...",
    "整合包解析失败": "Failed to parse modpack",
    "导入完成": "Import complete",
    "导入失败": "Import failed",
    "已取消导入": "Import cancelled",
    "请先选择整合包文件": "Please select a modpack file first",
    "仅支持 .mrpack 或 .zip 整合包": "Only .mrpack or .zip modpacks are supported",
    "CurseForge API Key（可选）": "CurseForge API Key (optional)",
    "缺失 Mod": "Missing mods",
    "正在安装整合包...": "Installing modpack...",
    "正在部署": "Deploying",
    "正在安装": "Installing",
    "正在下载 Mod": "Downloading mod",
    "未检测到 Java，整合包加载器安装需要 Java": "Java not found; mod loader installation requires Java",
    "名称": "Name",
    "摘要": "Summary",
    "启动版本": "Launch version",
    "Mod 加载器": "Mod loader",
    "Mod 文件": "Mod files",
    "作者": "Author",
    "仅 CurseForge 整合包需要，可留空": "Only needed for CurseForge modpacks; may be left blank",
    "管理隧道": "Manage tunnels",
    "暂无隧道": "No tunnels",
    "当前账号下还没有隧道，前往控制台创建一条，再回到这里刷新。":
        "No tunnels under this account yet. Create one in the console, then come back and refresh.",
    "启动中...": "Starting...",
    "已启动": "Running",
    "停止": "Stop",
    "恒星设置": "Stellar settings",
    "返回仪表盘": "Back to dashboard",
    "在游戏中打开「对局域网开放」（或启动服务端），再创建房间；好友输入房间码即可加入。":
        "Open your world to LAN (or run a server), then create a room; friends can join with the room code.",
    "未安装陶瓦联机组件，请先下载": "Terracotta is not installed; download it first",
    "下载陶瓦联机组件（约 8 MB）": "Download Terracotta (~8 MB)",
    "正在下载陶瓦联机组件...": "Downloading Terracotta...",
    "正在下载 NeoForge 安装器 ...": "Downloading NeoForge installer...",
    "正在运行 NeoForge 安装器 ...": "Running NeoForge installer...",
    "安装器下载中断，正在重试（%d/3）...": "Installer download interrupted, retrying (%d/3)...",
    "NeoForge 安装器运行超时（15 分钟）": "NeoForge installer timed out (15 minutes)",
    "NeoForge 安装器失败（退出码 %d）：\n%s": "NeoForge installer failed (exit code %d):\n%s",
    "陶瓦联机组件下载完成": "Terracotta downloaded",
    "下载失败：": "Download failed: ",
    "下载失败": "Download failed",
    "正在启动陶瓦联机组件...": "Starting Terracotta...",
    "陶瓦联机组件已就绪": "Terracotta is ready",
    "启动失败：": "Failed to start: ",
    "陶瓦联机仅支持 Windows": "Terracotta is supported on Windows only",
    "陶瓦联机启动超时": "Terracotta failed to start (timeout)",
    "请先下载陶瓦联机组件。": "Please download Terracotta first.",
    "客户端状态": "Client status",
    "房间码": "Room code",
    "尚未联机": "Not connected",
    "当前玩家：%s": "Players: %s",
    "把房间码发给好友，对方在陶瓦联机中填入即可加入。":
        "Send the room code to a friend; they join by entering it in Terracotta.",
    "已刷新客户端状态。": "Client status refreshed.",
    "陶瓦联机运行中": "Terracotta is running",
    "客户端已就绪：": "Client ready: ",
    "正在下载陶瓦客户端 ...": "Downloading Terracotta client ...",
    "正在检查客户端 ...": "Checking client ...",
    "尚未下载陶瓦客户端。": "Terracotta client is not installed yet.",
    "陶瓦联机运行中（端口 %s）": "Terracotta running (port %s)",
    "已停止陶瓦联机。": "Terracotta stopped.",
    "启动联机": "Start",
    "复制房间码": "Copy room code",
    "正在创建房间 ...": "Creating room ...",
    "正在加入房间 ...": "Joining room ...",
    "已加入房间": "Joined the room",
    "未知异常": "Unknown error",
    "请先启动陶瓦联机。": "Please start Terracotta first.",
    "陶瓦接口返回错误：HTTP %s": "Terracotta API error: HTTP %s",
    "房间码无效，或当前不处于待机状态。":
        "Invalid room code, or Terracotta is not idle.",
    "待机中：可创建房间或输入房间码加入。":
        "Idle: create a room or enter a room code to join.",
    "房间已就绪：把房间码发给好友即可加入。":
        "Room ready: send the code to friends to join.",
    "已启动，正在等待创建或加入房间 ...":
        "Started. Create a room or join one.",
    "房间码格式不正确，应形如 U/XXXX-XXXX-XXXX-XXXX。":
        "Invalid room code. Expected format: U/XXXX-XXXX-XXXX-XXXX.",
    "正在扫描本机 Minecraft 端口：请进入存档并「对局域网开放」，然后等待房间创建 ...":
        "Scanning local Minecraft ports: open a world to LAN, then wait for the room.",
    "；多人游戏列表中会出现「陶瓦联机大厅」。":
        "; the \"Terracotta Lobby\" will appear in the multiplayer list.",
    "联机异常：": "Connection error: ",
    "。可点击「停止联机」后重试。": ". Click Stop and try again.",
    "无法连接房主（房间已关闭或网络不稳定）":
        "Cannot reach the host (room closed or unstable network)",
    "与房主的连接中断": "Connection to the host was lost",
    "加入方组网组件崩溃": "Guest networking component crashed",
    "房主组网组件崩溃": "Host networking component crashed",
    "房主已退出存档，房间自动关闭": "Host left the world; the room was closed",
    "协议错误，请更新陶瓦客户端": "Protocol error, please update Terracotta",
    "陶瓦进程已退出。": "Terracotta process exited.",
    "陶瓦进程已退出，可重新启动联机。":
        "Terracotta exited. You can start it again.",
    "创建房间": "Create room",
    "加入房间": "Join room",
    "加入房间：": "Join room: ",
    "断开连接": "Disconnect",
    "房间码：": "Room code:",
    "复制": "Copy",
    "已复制": "Copied",
    "连接地址：": "Server address:",
    "或加入好友的房间：": "Or join a friend's room:",
    "房间内玩家：": "Players: ",
    "正在扫描本地 Minecraft 服务器...": "Scanning for a local Minecraft server...",
    "正在创建房间...": "Creating room...",
    "房间已创建，等待好友加入": "Room created, waiting for friends",
    "正在连接房主...": "Connecting to the host...",
    "正在建立联机隧道，NAT 难度：": "Building the tunnel, NAT difficulty: ",
    "已连接，在游戏中添加服务器：": "Connected. Add this server in Minecraft: ",
    "已断开": "Disconnected",
    "未知": "unknown",
    "简单": "easy",
    "较简单": "simple",
    "中等": "medium",
    "困难": "hard",
    "房间码格式不正确，应为 U/XXXX-XXXX-XXXX-XXXX": "Invalid room code; expected U/XXXX-XXXX-XXXX-XXXX",
    "联机组件连接失败：": "Cannot reach the multiplayer component: ",
    "无法连接到房主：请确认房间码正确，且房主仍保持联机":
        "Cannot reach the host: check the room code and make sure the host is still online",
    "房主拒绝了连接，请确认房间码是否正确": "The host refused the connection; check the room code",
    "本机联机组件崩溃（EasyTier），请断开后重试":
        "The local networking component (EasyTier) crashed; disconnect and retry",
    "房主的联机组件已崩溃": "The host's networking component crashed",
    "未检测到本地 Minecraft 服务器：请先在游戏中「对局域网开放」或启动服务端":
        "No local Minecraft server found: open your world to LAN or start a server first",
    "联机协议异常，请断开后重试": "Multiplayer protocol error; disconnect and retry",
    "Powered by Terracotta | 陶瓦联机 · © burningtnt · AGPL-3.0-or-later":
        "Powered by Terracotta | 陶瓦联机 · © burningtnt · AGPL-3.0-or-later",
    "查看许可": "View license",
    # 登录/对话框
    "Microsoft 登录": "Microsoft Login",
    "等待授权...": "Waiting for authorization...",
    "设备码已复制到剪贴板，正在等待授权...":
        "Device code copied to clipboard, waiting for authorization...",
    "留空即可正常登录正版账号；也可填入自己的 Azure Client ID":
        "Leave empty to sign in with the built-in public ID, or enter your own Azure Client ID",
    "登录成功！": "Login successful!",
    "登录失败：": "Login failed: ",
    "需要 Java ": "Requires Java ",
    "下载 Java ": "Download Java ",
    "正在请求设备码，请稍候...": "Requesting device code, please wait...",
    "请在浏览器中访问以下地址，并输入设备码：":
        "Open the following URL in your browser and enter the code:",
    "已在浏览器中打开授权页面。如未打开，请点击下方按钮。":
        "The authorization page was opened in your browser. If not, use the button below.",
    "打开验证页面": "Open verification page",
    "取消": "Cancel",
    "登录失败": "Login failed",
    "已取消登录": "Login cancelled",
    "登录成功": "Login successful",
    "API 地址：": "API URL:",
    "设为 LittleSkin，自动填写信息": "Use LittleSkin and fill in automatically",
    "authlib-injector JAR 路径（可选）：": "authlib-injector JAR path (optional):",
    "选择 authlib-injector.jar，留空则仅保存账号":
        "Choose authlib-injector.jar; leave empty to save the account only",
    "用户名：": "Username:",
    "密码（仅用于本次认证，不保存）：": "Password (used once, not saved):",
    "保存": "Save",
    "已应用": "Applied",
    "设置已保存。\n\n· JVM 参数、Java 路径将在下次启动游戏时生效\n· 下载源切换立即生效\n· 启动器语言切换将在重启启动器后生效":
        "Settings saved.\n\n"
        "· JVM arguments and Java path take effect the next time you launch the game\n"
        "· Download source switches immediately\n"
        "· Launcher language takes effect after restarting the launcher",
    "重启启动器": "Restart launcher",
    "启动器语言已更改，需要重启才能生效。\n现在重启吗？":
        "The launcher language has changed and requires a restart to take effect.\nRestart now?",
    "确定将本页设置恢复为默认值吗？\n（不影响账号、版本、个性化等其他配置）":
        "Restore this page to default values?\n"
        "(Accounts, versions, appearance and other settings are not affected)",
    "如何获取 Microsoft Client ID": "How to get a Microsoft Client ID",
    "任务进行中": "Task in progress",
    "Java环境缺失": "Java not found",
    "但未在系统中检测到合适的 Java 环境。": "No suitable Java runtime was found on this system.",
    # 多进程输出区
    "请选择进程": "Select process",
    "清除已关闭": "Clear closed",
    "停止全部": "Stop all",
    "尚未启动游戏": "No game started",
    "清空当前输出区": "Clear current output",
    "关闭已关闭游戏进程的输出区": "Close the output areas of closed game processes",
    "停止全部 Minecraft 进程": "Stop all Minecraft processes",
    "是否确定关闭所有Minecraft？": "Close all Minecraft instances?",
    "同时关闭所有输出区": "Also close all output areas",
    "正常退出": "exited normally",
    "异常退出! 错误码: ": "crashed! Error code: ",
    "当前没有已关闭的游戏进程。": "There are no closed game processes.",
    "当前没有正在运行的游戏进程。": "There are no running game processes.",
    "检测到游戏异常退出": "The game exited abnormally",
    "检测到游戏异常退出（错误码 %s）": "The game exited abnormally (error code %s)",
    "打开日志": "Open log",
    "智能分析": "AI analysis",
    "日志文件不存在。": "The log file does not exist.",
    "去设置": "Open settings",
    "正在读取日志并请求分析，请稍候 ...":
        "Reading the log and requesting analysis, please wait ...",
    "分析完成": "Analysis complete",
    "分析失败：": "Analysis failed: ",
    "启动页“智能分析”使用 LogShare 免费接口："
    "日志会自动脱敏并生成分享链接，无需任何 Key。":
        "“AI analysis” on the Launch page uses the free LogShare API: logs are "
        "sanitized and a share link is generated, no API key required.",
    "分享链接：": "Share link: ",
    "（点击打开）": " (click to open)",
    "分享链接（已脱敏，7 天内有效）：":
        "Share link (sanitized, valid for 7 days):",
    "分析类型：": "Analysis type: ",
    "识别版本：": "Detected version: ",
    "检测到的问题：": "Detected problems:",
    "相关信息：": "Related information:",
    "日志分析": "Log analysis",
    "LogShare 未识别到明确的崩溃问题，可在分享页查看完整日志与高亮。":
        "LogShare found no clear crash cause; open the share page for the "
        "full highlighted log.",
    "日志已上传，但未能获取自动分析结果。":
        "The log was uploaded, but no automatic analysis was returned.",
    "建议：": "Suggestion: ",
    "启动器": "Launcher",
    "...正在启动游戏...\n[再次按下取消此次启动]":
        "Starting game...\n[Press again to cancel this launch]",
    # 智能参数推荐
    "智能推荐": "Smart recommendation",
    "智能参数推荐": "Smart JVM recommendation",
    "根据电脑硬件自动推荐 JVM 启动参数": "Recommend JVM arguments based on your hardware",
    "节能模式": "Eco mode",
    "普通模式": "Normal mode",
    "性能模式": "Performance mode",
    "进阶自定义": "Advanced customization",
    "填入参数": "Fill in",
    "检测到内存：": "Detected memory: ",
    "检测到内存：未知": "Detected memory: Unknown",
    "当前电脑不宜使用性能模式。若需要调节，请手动修改参数":
        "This computer is not suitable for Performance mode. "
        "Adjust the parameters manually if needed.",
    "已生成参数": "Arguments generated",
    "已按电脑配置生成 JVM 参数并填入，点击「应用」保存后下次启动生效。":
        "JVM arguments were generated from your hardware and filled in. "
        "Click Apply to save; they take effect on the next launch.",
    "已填入推荐参数，可直接在「JAVA 虚拟机参数」框中微调，完成后点击「应用」保存。":
        "Suggested arguments were filled in. You can fine-tune them in the "
        "JVM arguments field and click Apply to save.",
    # ===== 主导航 / 通用 =====
    "游戏": "Game",
    "微软登录": "Microsoft Sign-in",
    "下载": "Download",
    "版本列表": "Versions",
    "Mod 加载器查询": "Mod Loader Check",
    "Java 检测": "Java Check",
    "Java 列表检测": "Java List Check",
    "选择 java": "Select Java",
    "闪电启动器": "Lightning Launcher",
    "请只选择一个版本进行重命名。": "Please select exactly one version to rename.",
    "确认删除": "Confirm deletion",
    "天": "days",
    "<a href='%s' style='color:#89b4fa'>没有账号？先注册</a>":
        "<a href='%s' style='color:#89b4fa'>No account? Sign up first</a>",
    "补全失败：%s": "Repair failed: %s",
    "：manifest.json 缺少 minecraft.version":
        ": manifest.json is missing minecraft.version",
    "父版本链损坏（存在循环引用或名称非法）":
        "Parent version chain is corrupted (circular reference or invalid name)",
    "父版本「%s」缺少 JSON 文件": "Parent version “%s” is missing its JSON file",
    "版本 JSON 文件为空：%s.json": "Version JSON is empty: %s.json",
    "版本 JSON 无法解析（%s.json）：%s": "Cannot parse version JSON (%s.json): %s",
    "版本 JSON 内容不是对象：%s.json": "Version JSON content is not an object: %s.json",
    "版本 JSON 缺少有效 id 字段：%s.json": "Version JSON has no valid id field: %s.json",
    "版本 JSON 缺少 mainClass：%s.json": "Version JSON is missing mainClass: %s.json",
    "版本 JSON 缺少 libraries 列表：%s.json":
        "Version JSON is missing the libraries list: %s.json",
    "版本 JSON 的 inheritsFrom 层级过深": "inheritsFrom chain in version JSON is too deep",
    "发行包中未找到可执行文件": "No executable found in the release package",
    "用户不存在": "User does not exist",
    "目录创建失败": "Failed to create directory",
    "等待超时": "Timed out",
    "任务正在运行，是否终止并关闭？": "A task is running. Terminate and close?",
    "部分后台线程未能在 12 秒内结束。\n\n· 选「继续等待」：不退出，稍后可再试关闭\n· 选「强制退出」：立即终止未结束的任务并退出（可能中断未完成的下载）":
        "Some background threads did not finish within 12 seconds.\n\n"
        "· Keep waiting: stay open and try closing again later\n"
        "· Force quit: terminate unfinished tasks and exit "
        "(unfinished downloads may be interrupted)",
    # ===== 启动 / 版本 =====
    "启动中 —— *Minecraft ": "Launching — *Minecraft ",
    " Java Edition（启动中）": " Java Edition (launching)",
    "（启动中）": " (launching)",
    "（已关闭）": " (closed)",
    "警告：未找到 Java ": "Warning: Java not found: ",
    "，将使用 Java ": ", using Java ",
    "，部分旧版模组可能不兼容。": "; some older mods may be incompatible.",
    "开始启动游戏。": "Starting the game.",
    "版本: ": "Version: ",
    "账号: ": "Account: ",
    "模式: ": "Mode: ",
    "JVM 参数引号不匹配，已回退为简单分割。":
        "Unmatched quotes in JVM arguments; fell back to simple splitting.",
    "已注入 authlib-injector：": "authlib-injector injected: ",
    "未找到 authlib-injector JAR 或 API 地址，第三方认证可能失败。":
        "authlib-injector JAR or API URL not found; third-party authentication may fail.",
    "[错误] ": "[Error] ",
    "已将游戏语言设为中文。": "Set the game language to Chinese.",
    "设置游戏语言失败：": "Failed to set the game language: ",
    "保存新登录令牌失败：": "Failed to save the new login token: ",
    "正版登录令牌已自动续期。": "Microsoft login token refreshed automatically.",
    "的Minecraft已被关闭。状态：": "'s Minecraft has been closed. Status: ",
    "进程PID为": "Process PID ",
    "启动已取消或失败（错误码 ": "Launch cancelled or failed (error code ",
    "已请求取消此次启动 ...": "Cancellation requested ...",
    "已请求游戏正常退出，等待游戏保存并关闭窗口 ...":
        "Requested a graceful game exit; waiting for the game to save and close ...",
    "未找到游戏窗口，无法发送正常关闭请求，请手动关闭游戏窗口。":
        "Game window not found; cannot request a graceful close. Please close it manually.",
    "用户取消下载": "Download cancelled by user",
    "下载已取消": "Download cancelled",
    "文件为空": "File is empty",
    "已取消下载": "Download cancelled",
    "已安装并重命名为 ": "Installed and renamed to ",
    " 失败：": " failed: ",
    "已安装为 ": "Installed as ",
    "，重命名为 ": ", renamed to ",
    "版本文件补全完成。": "Version files repaired.",
    "正在补全版本文件 ...": "Repairing version files ...",
    "已取消启动。": "Launch cancelled.",
    "库文件补全完成。": "Library files repaired.",
    "登录令牌刷新失败，将使用已保存的令牌：":
        "Failed to refresh the login token; using the saved token: ",
    "版本补全失败：": "Version repair failed: ",
    "检查加载器主类失败：": "Failed to check the loader main class: ",
    "已修复版本「": "Fixed the loader main class of version “",
    "」的加载器主类。": "”.",
    "缺少依赖：": "Missing dependency: ",
    "缺少依赖：%s": "Missing dependency: %s",
    "无法创建游戏目录：": "Cannot create the game directory: ",
    "设备码登录失败：": "Device-code sign-in failed: ",
    "设备码登录超时，请重试。": "Device-code sign-in timed out. Please try again.",
    "该 Microsoft 账户未拥有 Minecraft，或应用权限不足。":
        "This Microsoft account does not own Minecraft, or the app lacks permission.",
    "该 Microsoft 账户未购买 Minecraft。": "This Microsoft account has not purchased Minecraft.",
    "正版验证失败：": "Microsoft verification failed: ",
    "登录返回数据不完整": "Incomplete login response",
    "未获取到授权码（超时或用户取消）": "No authorization code received (timeout or cancelled)",
    "浏览器登录失败：": "Browser sign-in failed: ",
    "当前 minecraft-launcher-lib 版本不支持微软登录，\n请升级：pip install -U minecraft-launcher-lib":
        "The current minecraft-launcher-lib version does not support Microsoft sign-in.\n"
        "Please upgrade: pip install -U minecraft-launcher-lib",
    "minecraft-launcher-lib 版本过低（需 8.0+），请先升级":
        "minecraft-launcher-lib is too old (8.0+ required). Please upgrade first.",
    "未检测到 Java，Forge/NeoForge 安装器需要 Java，请在设置中指定 Java 路径":
        "Java not found. The Forge/NeoForge installer requires Java; "
        "please set the Java path in Settings.",
    "NeoForge 安装器下载失败：%s": "Failed to download the NeoForge installer: %s",
    "检测到 %d 个库文件缺失（如 %s），正在自动补全 ...":
        "%d library file(s) missing (e.g. %s); repairing automatically ...",
    "仍有 %d 个库文件缺失（如 %s）。": "%d library file(s) still missing (e.g. %s).",
    "库文件补全失败（": "Library repair failed (",
    "库文件补全失败，请检查网络连接后重试，或在「下载游戏」中重新安装该版本。":
        "Library repair failed. Check your network and retry, "
        "or reinstall this version from the Download page.",
    "加载器核心库缺失：%s\n自动补全未能修复，请检查网络后在「下载游戏」中用同一加载器版本重新安装一次。":
        "Loader core library missing: %s\nAutomatic repair failed. Check your network, "
        "then reinstall with the same loader version from the Download page.",
    "版本文件损坏，无法补全或启动：\n%s\n请在「下载游戏」中重新安装该版本。":
        "Version files are corrupted and cannot be repaired or launched:\n%s\n"
        "Please reinstall this version from the Download page.",
    "版本 JSON 文件异常，无法补全或启动：\n%s\n\n请在「下载游戏」中重新安装该版本。":
        "Version JSON is invalid and cannot be repaired or launched:\n%s\n\n"
        "Please reinstall this version from the Download page.",
    "版本 JSON 文件异常，无法补全：\n%s\n\n请在「下载游戏」中重新安装该版本。":
        "Version JSON is invalid and cannot be repaired:\n%s\n\n"
        "Please reinstall this version from the Download page.",
    "版本 JSON 异常，无法补全：\n%s\n\n请在「下载游戏」中重新安装该版本。":
        "Version JSON is invalid and cannot be repaired:\n%s\n\n"
        "Please reinstall this version from the Download page.",
    "版本 JSON 异常，无法补全或启动：\n":
        "Version JSON is invalid and cannot be repaired or launched:\n",
    # ===== 账号 / 通用弹窗 =====
    "提示": "Notice",
    "请输入用户名。": "Please enter a username.",
    "认证": "Authentication",
    "认证返回数据不完整。": "Incomplete authentication response.",
    "认证失败": "Authentication failed",
    "无法完成认证：\n": "Authentication failed:\n",
    "\n\n是否保存为离线账号（认证可能失败）？":
        "\n\nSave as an offline account? (authentication may fail)",
    "账号“": "Account “",
    "”已存在。": "” already exists.",
    "\n请在左侧列表中点击选中它。": "\nSelect it in the list on the left.",
    "已添加正版账号：": "Microsoft account added: ",
    "删除账号": "Delete account",
    "确定删除账号“": "Delete account “",
    "”吗？": "”?",
    "重命名版本": "Rename version",
    "新名称：": "New name: ",
    "重命名失败": "Rename failed",
    "删除版本": "Delete version",
    "确定将版本“": "Move version “",
    "”移至回收站吗？": "” to the recycle bin?",
    "删除失败": "Delete failed",
    "版本名称非法，已阻止删除。": "Invalid version name; deletion blocked.",
    "版本名称非法，已阻止启动。": "Invalid version name; launch blocked.",
    "请先选择一个账号。": "Please select an account first.",
    "请先选择一个游戏版本。": "Please select a game version first.",
    "请先选择一个版本。": "Please select a version first.",
    "无法启动": "Unable to launch",
    "无法补全": "Cannot repair",
    "补全完成": "Repair complete",
    "补全失败": "Repair failed",
    "确定删除": "Confirm deletion",
    "部分未删除": "Some were not deleted",
    "以下版本名称非法，已阻止删除：\n":
        "The following version names are invalid; deletion blocked:\n",
    " 个版本移至回收站？": " version(s) to the recycle bin?",
    "将选中的 ": "Move the selected ",
    "打开失败": "Open failed",
    "无法创建 Mod 文件夹：\n": "Cannot create the mods folder:\n",
    "失败": "Failed",
    "名称非法": "Invalid name",
    "名称首尾不能有空格": "Name cannot start or end with spaces",
    "名称含非法字符": "Name contains invalid characters",
    "名称不能以点或空格结尾": "Name cannot end with a dot or space",
    "名称为系统保留名": "Name is a reserved system name",
    "源版本名非法": "Invalid source version name",
    "源版本不存在": "Source version does not exist",
    "目标名称已存在": "Target name already exists",
    # ===== 下载 =====
    "补全文件": "Repair files",
    "已完成": "Completed",
    "下载完成": "Download complete",
    "正在检查并补全版本文件 ...": "Checking and repairing version files ...",
    "请先选择一个有效的游戏版本。": "Please select a valid game version first.",
    "版本名非法：": "Invalid version name: ",
    "队列中已存在版本名“": "Version name already in queue: “",
    "队列中没有待下载任务。": "There are no pending download tasks.",
    "网络有问题，下载失败！\n原因：": "Network error, download failed!\nReason: ",
    " 个任务下载成功。": " task(s) downloaded successfully.",
    "共 ": "Total ",
    " 个任务。": " task(s).",
    "已取消 ": "Cancelled ",
    " 个任务均失败。": " task(s) failed.",
    "本次 ": "This time ",
    "（损坏）": " (corrupted)",
    "未选择文件": "No file selected",
    "实例名称：": "Instance name: ",
    "CurseForge API Key（可选）：": "CurseForge API Key (optional): ",
    "整合包 (*.mrpack *.zip);;Modrinth (*.mrpack);;CurseForge (*.zip)":
        "Modpacks (*.mrpack *.zip);;Modrinth (*.mrpack);;CurseForge (*.zip)",
    "缺少 manifest.json，不是有效的 CurseForge 整合包":
        "manifest.json is missing; this is not a valid CurseForge modpack",
    "实例已存在": "Instance already exists",
    "”已存在，是否覆盖安装？": "” already exists. Overwrite?",
    "实例“": "Instance “",
    "\n%s %d 个（可稍后手动放入 mods 文件夹）：\n":
        "\n%s %d (you can put them into the mods folder later):\n",
    "选择背景图片": "Choose background image",
    "个性化设置已重新应用。": "Appearance settings reapplied.",
    "确定将个性化设置恢复为默认吗？\n（背景颜色、背景图片、标题）":
        "Restore appearance settings to default?\n"
        "(background color, background image, title)",
    "个性化设置已恢复为默认并应用。": "Appearance settings restored to default and applied.",
    "确定": "OK",
    "是": "Yes",
    "否": "No",
    "主题：": "Theme: ",
    "深蓝主题": "Deep Blue",
    "黑色主题": "Black",
    "白色主题": "White",
    "修改后需点击「应用」才会生效。": "Changes take effect after clicking Apply.",
    "个性化设置已保存。": "Appearance settings saved.",
    "已恢复默认": "Restored to default",
    # ===== 恒星内网穿透 / 联机 =====
    "隧道列表": "Tunnel list",
    "刷新：": "Refresh: ",
    "停止联机": "Stop multiplayer",
    "用户数据刷新间隔：": "User data refresh interval: ",
    "隧道卡片刷新间隔：": "Tunnel card refresh interval: ",
    " 分钟": " min",
    " 分": " min",
    "未登录": "Not signed in",
    "共 %d 条隧道": "%d tunnel(s)",
    "客户端无法在本机运行。": "The client cannot run on this machine.",
    "客户端启动失败": "Client failed to start",
    "FRPC 客户端无法在本机运行，通常是客户端文件不完整、或被杀毒软件拦截。\n\n"
    "请点击界面上的「下载客户端」重新下载，并把客户端加入杀毒软件白名单。":
        "The FRPC client cannot run on this machine. This is usually caused by an "
        "incomplete client file or by antivirus blocking.\n\n"
        "Click \"Download client\" in the app to download it again, "
        "and add the client to your antivirus whitelist.",
    "FRPC 客户端启动后异常崩溃（退出码 2）。\n\n"
    "常见原因：客户端文件损坏、被杀毒软件拦截，或隧道配置异常。\n"
    "建议：点击「下载客户端」重新下载后重试，并把客户端加入杀毒软件白名单。":
        "The FRPC client crashed right after starting (exit code 2).\n\n"
        "Common causes: a damaged client file, antivirus blocking, or an invalid tunnel config.\n"
        "Suggestion: click \"Download client\" to reinstall it, then retry, "
        "and add the client to your antivirus whitelist.",
    # ===== 设置页 =====
    "正版登录使用微软 OAuth2「设备码」流程。\n\n"
    "本启动器内置了一个开源社区公用的公共 Client ID，\n"
    "设置页留空即可直接登录，无需注册 Azure 应用。\n\n"
    "如果内置 ID 失效（例如被微软撤销），可以免费注册自己的应用：\n"
    "1. 访问 https://portal.azure.com 并登录\n"
    "2. 搜索并进入「应用注册」→ 点击「新注册」\n"
    "3. 名称任意；支持的账户类型选择「仅个人 Microsoft 账户」\n"
    "4. 重定向 URI 选择「公共客户端/本机(移动和桌面)」，填 http://localhost\n"
    "5. 注册完成后复制「应用程序(客户端) ID」\n"
    "6. 粘贴到本页输入框并点击「应用」\n\n"
    "之后点击左侧「添加正版账号」即可开始登录。":
        "Microsoft sign-in uses the OAuth2 device-code flow.\n\n"
        "This launcher ships with a public Client ID shared by the open-source community, "
        "so you can sign in directly with this field left empty — no Azure app registration needed.\n\n"
        "If the built-in ID stops working (e.g. revoked by Microsoft), you can register your own app for free:\n"
        "1. Open https://portal.azure.com and sign in\n"
        "2. Search for and open \"App registrations\" → click \"New registration\"\n"
        "3. Any name; choose \"Personal Microsoft accounts only\" for supported account types\n"
        "4. Redirect URI: \"Public client/native (mobile & desktop)\", value http://localhost\n"
        "5. Copy the \"Application (client) ID\" after registering\n"
        "6. Paste it into the field on this page and click Apply\n\n"
        "Then click \"Add Microsoft account\" on the left to start signing in.",
    # 后续功能补充翻译
    "%s 需要 Java %d 及以上，当前版本为 Java %d，该 GC 参数可能无法生效。":
        "%s requires Java %d or newer; current version is Java %d. This GC option may not take effect.",
    "当前平台不支持自动设置显卡偏好，请在系统显卡设置中手动指定 Java 使用独立显卡。":
        "GPU preference cannot be set automatically on this platform. In your system graphics settings, manually assign Java to the dedicated GPU.",
    "尚未找到 Java 可执行文件。": "No Java executable has been found.",
    "请先在账号列表中选择一个账号。": "Please select an account from the list first.",
    "确定删除账号“%s”吗？": "Delete account \"%s\"?",
    "账号“%s”已存在。": "Account \"%s\" already exists.",
    "已添加正版账号：%s\n请在左侧列表中点击选中它。":
        "Microsoft account added: %s\nClick it in the list on the left to select it.",
    "认证失败：": "Authentication failed: ",
    "[提示] ": "[Notice] ",
    "已应用高性能显卡偏好：": "High-performance GPU preference applied: ",
    "高性能显卡设置未生效：": "High-performance GPU setting did not take effect: ",
    "进程PID为%s的Minecraft已被关闭。状态：":
        "Minecraft with PID %s was closed. Status: ",
    "启动已取消或失败（错误码 %s）。": "Launch cancelled or failed (error code %s).",
    "正在向微软申请设备码 ...": "Requesting a device code from Microsoft ...",
    "打开浏览器": "Open browser",
    "请访问 %s 并输入上方代码完成登录。": "Visit %s and enter the code above to sign in.",
    "日志内容为空，无法分析。": "The log is empty; there is nothing to analyze.",
    "Minecraft 版本": "Minecraft version",
    "加载器": "Loader",
    "LogShare 返回异常（HTTP %s）": "LogShare returned an error (HTTP %s)",
    "LogShare 上传失败：": "LogShare upload failed: ",
    "正在检查并补全版本：": "Checking and completing version: ",
    "准备中 ...": "Preparing ...",
    "管理模组": "Manage mods",
    "暂无已安装的版本。": "No versions are installed.",
    "无": "None",
    "确定删除版本“%s”吗？版本文件夹会被移入回收站。":
        "Delete version \"%s\"? Its folder will be moved to the Recycle Bin.",
    "打开模组管理失败": "Failed to open mod manager",
    "搜索": "Search",
    "加载更多": "Load more",
    "正在获取列表 ...": "Fetching list ...",
    "找到": "Found",
    "（CurseForge 公共代理不可用，可在设置页填写 CurseForge API Key 后重试）":
        "(The CurseForge public proxy is unavailable. Enter a CurseForge API key in Settings and retry.)",
    "获取失败：": "Failed to fetch: ",
    "光影": "Shaders",
    "整合包": "Modpack",
    "通用": "General",
    "作者：": "Author: ",
    "正在加载项目信息 ...": "Loading project information ...",
    "返回列表": "Back to list",
    "加载失败：": "Failed to load: ",
    "下载量：": "Downloads: ",
    "转到 CurseForge": "Open CurseForge",
    "转到 Modrinth": "Open Modrinth",
    "支持的游戏版本（点击查看对应版本列表）":
        "Supported game versions (click to view files)",
    "暂无可用版本": "No files available",
    "可用版本": "Available files",
    "该游戏版本下暂无可用版本。": "No files are available for this game version.",
    "前置：": "Dependencies: ",
    "无前置模组": "No dependencies",
    "选择下载位置": "Choose download location",
    "文件：": "File: ",
    "检测到以下实例，点击即可下载到对应文件夹：":
        "Instances detected. Click one to download into its folder:",
    "没有检测到 %s %s 客户端。": "No %s %s client was detected.",
    "对应加载器": "corresponding loader",
    "没有检测到 %s 的客户端。": "No %s client was detected.",
    "可以点击下方按钮浏览并选择要下载到的文件夹。":
        "Use the button below to browse for a destination folder.",
    "浏览其他位置...": "Browse another location...",
    "下载完成后会自动识别整合包类型，并进入导入流程。":
        "After downloading, the modpack type is detected automatically and the import flow opens.",
    "下载并导入": "Download and import",
    "选择下载到的文件夹": "Choose destination folder",
    "该版本没有可下载文件。": "This version has no downloadable file.",
    "该文件没有可用下载地址（作者可能禁止了第三方下载）。":
        "No download URL is available for this file (the author may have disabled third-party downloads).",
    "正在下载：": "Downloading: ",
    "已下载到：": "Downloaded to: ",
    "返回版本列表": "Back to versions",
    "模组管理：": "Mod manager: ",
    "检查更新": "Check for updates",
    "一键更新": "Update all",
    "禁用所选模组": "Disable selected mods",
    "启用所选模组": "Enable selected mods",
    "左键选择模组，右键前往该模组的下载页":
        "Left-click to select a mod; right-click to open its download page",
    "该版本暂未安装模组。": "No mods are installed for this version.",
    "已禁用": "Disabled",
    "可更新 → ": "Update available → ",
    "正在检查更新 ...": "Checking for updates ...",
    "发现 %d 个可更新模组": "Found updates for %d mods",
    "全部模组均为最新版本": "All mods are up to date",
    "检查失败：": "Check failed: ",
    "正在更新模组 ...": "Updating mods ...",
    "正在更新 %d/%d：%s": "Updating %d/%d: %s",
    "已更新 %d 个模组": "Updated %d mods",
    "更新失败：": "Update failed: ",
    "操作失败": "Operation failed",
    "目标位置已存在同名文件。": "A file with the same name already exists at the destination.",
    "下载整合包": "Download modpacks",
    "模组下载": "Download mods",
    "光影下载": "Download shaders",
    "点击此区域选择整合包，或把文件拖到这里":
        "Click here to choose a modpack, or drop the file here",
    "按文件内容自动识别 Modrinth / CurseForge 整合包，不依赖扩展名":
        "Detects Modrinth / CurseForge modpacks by file content, not extension",
    "可选文件（默认不勾选，选中后一并安装）":
        "Optional files (unselected by default; selected files are installed too)",
    "队列中已存在版本名“%s”。":
        "A version named \"%s\" is already in the queue.",
    "共 %d/%d 个任务下载成功。": "%d/%d tasks downloaded successfully.",
    "已取消 %d 个任务。": "Cancelled %d tasks.",
    "本次 %d 个任务均失败。": "All %d tasks failed.",
    "支持 Modrinth 与 CurseForge 整合包":
        "Supports Modrinth and CurseForge modpacks",
    "已下载整合包，请确认信息后点击“开始导入”。":
        "Modpack downloaded. Review its information and click \"Start Import\".",
    "所有文件": "All files",
    "文件不存在。": "The file does not exist.",
    "未选择整合包": "No modpack selected",
    "无法识别整合包": "Unrecognized modpack",
    "这不是有效的 Modrinth 或 CurseForge 整合包。\n已检查压缩包内容：未找到 modrinth.index.json 或 manifest.json。":
        "This is not a valid Modrinth or CurseForge modpack.\nThe archive was checked: neither modrinth.index.json nor manifest.json was found.",
    "已选择：%s（%s 整合包）": "Selected: %s (%s modpack)",
    "缺少有效的 manifest.json": "A valid manifest.json is missing",
    "实例“%s”已存在，是否覆盖安装？":
        "Instance \"%s\" already exists. Overwrite it?",
    "主题": "Theme",
    "启用界面切换动画": "Enable page transition animations",
    "语言": "Language",
    "个性化设置已应用。\n语言变更需重启启动器后生效。":
        "Personalization applied.\nRestart the launcher for the language change to take effect.",
    "应用失败": "Failed to apply settings",
    "恢复默认": "Restore defaults",
    "确定把所有个性化设置恢复为默认值吗？":
        "Reset all personalization settings to their defaults?",
    "Java 路径": "Java path",
    "留空则自动检测；Forge/NeoForge 安装也需要 Java":
        "Leave blank to auto-detect; Java is also required to install Forge/NeoForge",
    "JVM 参数": "JVM arguments",
    "根据内存生成推荐 JVM 参数": "Generate recommended JVM arguments based on memory",
    "检测到系统内存约 %d GB": "Detected approximately %d GB of system memory",
    "额外游戏参数": "Additional game arguments",
    "以上为旧版参数，仅在未启用高级参数时生效。":
        "The legacy arguments above apply only when advanced options are disabled.",
    "游戏目录": "Game directory",
    "微软 Client ID": "Microsoft Client ID",
    "留空则使用内置 Client ID": "Leave blank to use the built-in Client ID",
    "authlib-injector 路径": "authlib-injector path",
    "留空则使用公共代理": "Leave blank to use the public proxy",
    "下载源": "Download source",
    "国内镜像（推荐）": "China mirror (recommended)",
    "官方源": "Official",
    "首次启动游戏时把游戏语言设为中文":
        "Set the game language to Chinese on first launch",
    "启用高级参数（内存 / GC / 参数分区 / 启动前命令）":
        "Enable advanced options (memory / GC / argument sections / pre-launch command)",
    "保存设置": "Save settings",
    "打开游戏目录": "Open game directory",
    "作用域": "Scope",
    "全局设置": "Global settings",
    "版本独立设置": "Version-specific settings",
    "（未安装版本）": "(Version not installed)",
    "内存管理": "Memory management",
    "模式": "Mode",
    "自动配置（推荐）": "Automatic (recommended)",
    "自定义配置": "Custom",
    "恢复自动配置": "Restore automatic configuration",
    "垃圾回收器": "Garbage collector",
    "自动（推荐）": "Automatic (recommended)",
    "高级 GC 微调 ▸": "Advanced GC tuning ▸",
    "高级 GC 微调 ▾": "Advanced GC tuning ▾",
    "示例：-XX:MaxGCPauseMillis=200 -XX:G1NewSizePercent=30":
        "Example: -XX:MaxGCPauseMillis=200 -XX:G1NewSizePercent=30",
    "JVM 参数（java 与主类之间）":
        "JVM arguments (between java and the main class)",
    "游戏参数（--demo、--width、--server 等）不能填在这里。":
        "Game arguments (--demo, --width, --server, etc.) cannot be entered here.",
    "游戏参数（主类之后）": "Game arguments (after the main class)",
    "--demo 是直接传给 Minecraft 主程序的游戏参数，不是 JVM 参数；--server / --port 也属于游戏参数。":
        "--demo is a game argument passed directly to Minecraft, not a JVM argument; --server / --port are also game arguments.",
    "窗口尺寸": "Window size",
    "留空则不生成 --width / --height，仅接受正整数。":
        "Leave blank to omit --width / --height; positive integers only.",
    "启动前命令": "Pre-launch command",
    "等待命令执行完成后再继续启动":
        "Wait for the command to finish before launching",
    "命令按原样执行，不做隐藏截断；版本独立设置下默认“跟随全局”或“禁用”，不会静默继承全局命令。":
        "The command runs verbatim without hiding or truncation. Version-specific settings default to \"Follow global\" or \"Disabled\" and never silently inherit the global command.",
    "进程优先级": "Process priority",
    "正常": "Normal",
    "高": "High",
    "“高”仅在系统资源紧张时提供有限帮助，不建议默认开启。":
        "\"High\" offers limited benefit only under resource pressure and is not recommended by default.",
    "显卡调度": "GPU scheduling",
    "高性能显卡 / 强制使用独立显卡": "High-performance GPU / force dedicated GPU",
    "Windows 下写入显卡偏好；其他平台会提示手动配置。":
        "Sets the GPU preference on Windows; other platforms show manual setup instructions.",
    "当前平台不支持自动设置，仅提供配置提示。":
        "Automatic configuration is unsupported on this platform; instructions only.",
    "渲染器 / 图形 API": "Renderer / graphics API",
    "渲染后端由游戏与已安装模组决定，原生 Minecraft 没有可在启动器侧切换的 OpenGL / Vulkan / DirectX 开关，因此不提供无法真正生效的选项。KryptonWrapper、DirectX 12 等属于实验性方案，本启动器不注入。":
        "The game and installed mods determine the rendering backend. Vanilla Minecraft has no launcher-side OpenGL / Vulkan / DirectX switch, so no ineffective option is offered. Experimental solutions such as KryptonWrapper and DirectX 12 are not injected by this launcher.",
    "命令行预览": "Command-line preview",
    "刷新预览": "Refresh preview",
    "跟随全局": "Follow global",
    "启用并自定义": "Enable and customize",
    "禁用": "Disabled",
    "模组/整合包，%d 个模组": "Mods/modpack, %d mods",
    "自动建议：-Xms%s -Xmx%s（%s）": "Suggested: -Xms%s -Xmx%s (%s)",
    "最大堆超过物理内存（%d GB），无法保存。":
        "Maximum heap exceeds physical memory (%d GB); cannot save.",
    "最大堆已超过物理内存的 70%%，可能触发系统页面文件，导致比内存不足更严重的卡顿。":
        "Maximum heap exceeds 70%% of physical memory. This may trigger system paging and cause worse stuttering than insufficient memory.",
    "将自动使用 %s（Java %d）": "%s (Java %d) will be used automatically",
    "%s 需要 Java %d 及以上，当前为 Java %d":
        "%s requires Java %d or newer; current version is Java %d",
    "%s 需要 Java %d 及以上": "%s requires Java %d or newer",
    "检测到游戏参数 %s 被填进了 JVM 参数区，请移动到下方“游戏参数”区。":
        "Game argument %s was entered in the JVM arguments section. Move it to \"Game arguments\" below.",
    "（无）": "(None)",
    "选择游戏目录": "Choose game directory",
    "推荐 JVM 参数": "Recommended JVM arguments",
    "已填入推荐参数：\n": "Recommended arguments inserted:\n",
    "游戏目录不存在。": "The game directory does not exist.",
    "宽度": "Width",
    "高度": "Height",
    "参数无效": "Invalid arguments",
    "%s只能是正整数，或留空不生成对应参数。":
        "%s must be a positive integer, or leave it blank to omit the argument.",
    "内存设置过高": "Memory setting too high",
    "内存安全警告": "Memory safety warning",
    "仍要保存吗？": "Save anyway?",
    "参数分区提示": "Argument section notice",
    "检测到游戏参数 %s 被填进了 JVM 参数区。\n--demo / --width / --height / --server / --port 属于游戏参数，应填到“游戏参数”区。":
        "Game argument %s was entered in the JVM arguments section.\n--demo / --width / --height / --server / --port are game arguments and belong in \"Game arguments\".",
    "GC 兼容性提示": "GC compatibility notice",
    "%s 需要 Java %d 及以上，当前为 Java %d，该参数可能无法生效。仍要保存吗？":
        "%s requires Java %d or newer; current version is Java %d. This option may not take effect. Save anyway?",
    "已保存": "Saved",
    "设置已保存。": "Settings saved.",
    "保存失败": "Failed to save settings",
    "恒星映射": "Stellar tunnel",
    "与好友组成虚拟局域网，无需公网 IP。\n首次使用会下载约 30 MB 的陶瓦客户端。":
        "Create a virtual LAN with friends without a public IP.\nAbout 30 MB of the Terracotta client is downloaded on first use.",
    "查看许可协议": "View license",
    "客户端下载完成：": "Client downloaded: ",
    "请先下载陶瓦客户端。": "Please download the Terracotta client first.",
    "正在启动陶瓦联机 ...": "Starting Terracotta ...",
    "陶瓦联机已启动，端口：": "Terracotta started, port: ",
    "已复制房间码：": "Room code copied: ",
    "登录恒星账号": "Sign in to Stellar account",
    "打开控制台": "Open console",
    "尚未获取隧道": "No tunnels loaded",
    "新建隧道": "New tunnel",
    "连接隧道": "Connect tunnel",
    "（未知账号）": "(Unknown account)",
    "密码：": "Password: ",
    "请输入账号和密码。": "Please enter your username and password.",
    "正在登录 ...": "Signing in ...",
    "登录成功：": "Signed in: ",
    "获取信息失败：": "Failed to get account information: ",
    "今日已签到": "Checked in today",
    "尚未签到": "Not checked in",
    "获取隧道失败：": "Failed to get tunnels: ",
    "没有可用隧道，请先在控制台创建":
        "No tunnels are available; create one in the console first",
    "已获取 %d 个隧道。": "Loaded %d tunnels.",
    "请先登录恒星账号。": "Please sign in to your Stellar account first.",
    "请先创建并刷新隧道。": "Please create and refresh a tunnel first.",
    "正在连接隧道 ...": "Connecting tunnel ...",
    "客户端无法运行：": "The client could not run: ",
    "FRPC 已退出（错误码 %d）": "FRPC exited (error code %d)",
    "已断开隧道连接。": "Tunnel disconnected.",
    "确定退出恒星账号登录吗？": "Sign out of your Stellar account?",
    "版本 1.0.1  ·  Python + tkinter": "Version 1.0.1  ·  Python + tkinter",
    "一个简洁的 Minecraft 启动器\n支持多账号、模组加载器与联机":
        "A clean and simple Minecraft launcher\nMultiple accounts, mod loaders, and multiplayer",
    "缺少 Java": "Java missing",
    "启动该版本需要 Java %d，但本机没有找到可用的 Java。\n可以让启动器自动下载，或手动指定一个 Java 路径。":
        "Java %d is required to launch this version, but no usable Java was found.\nLet the launcher download it automatically, or specify a Java path manually.",
    "正在下载并安装 Java %d ...": "Downloading and installing Java %d ...",
    "选择 Java 可执行文件": "Choose Java executable",
    "已选择：": "Selected: ",
    "手动选择": "Choose manually",
    "自动下载": "Download automatically",
    "Java 安装完成：": "Java installed: ",
    "安装失败：": "Installation failed: ",
    "关闭启动器": "Close launcher",
    "检测到 Minecraft 正在运行，关闭启动器会一并结束游戏进程。\n确定要关闭吗？":
        "Minecraft is running. Closing the launcher will also terminate the game.\nClose anyway?",
    # 依赖安装窗口
    "依赖缺失": "Missing dependencies",
    "检测到依赖缺失": "Missing dependencies detected",
    "预计需要下载约 %d MB": "Approximately %d MB needs to be downloaded",
    "开始安装，请稍候 ...": "Starting installation, please wait ...",
    "共 %d 个依赖需要安装。": "%d dependencies need to be installed.",
    "(%d/%d) 正在安装 %s ...": "(%d/%d) Installing %s ...",
    "× %s 安装出错：%s": "× Error installing %s: %s",
    "× %s 安装失败": "× Failed to install %s",
    "√ %s 安装完成": "√ Installed %s",
    "\n√ 安装成功，即将启动主程序 ...":
        "\n√ Installation succeeded. Starting the launcher ...",
    "\n× 安装失败！请检查网络或权限。\n建议：开启代理，或以管理员身份运行。":
        "\n× Installation failed! Check your network or permissions.\nTip: enable a proxy or run as administrator.",
    "退出": "Quit",
    "下载依赖": "Download dependencies",
    "[JVM 参数]  ": "[JVM arguments]  ",
    "[启动器固定] ": "[Launcher fixed] ",
    "[游戏参数]  ": "[Game arguments]  ",
    "设备码登录失败：%s": "Device code sign-in failed: %s",
    "正版验证失败：%s": "Official account verification failed: %s",
    "浏览器登录失败：%s": "Browser sign-in failed: %s",
    "登录令牌刷新失败，将使用已保存的令牌：%s":
        "Token refresh failed; the saved token will be used: %s",
    "版本补全失败：%s": "Version completion failed: %s",
    "已修复版本「%s」的加载器主类。":
        "Repaired the loader main class of version \"%s\".",
    "检查加载器主类失败：%s": "Failed to check the loader main class: %s",
    "已按高优先级启动游戏进程。": "Game process started with high priority.",
    "执行启动前命令：": "Running pre-launch command: ",
    "[前置] ": "[Pre-launch] ",
    "启动前命令已结束（返回码 %s）。":
        "Pre-launch command finished (exit code %s).",
    "启动前命令已在后台启动，不等待其结束。":
        "Pre-launch command started in the background; not waiting for it.",
    "启动前命令执行失败：%s": "Pre-launch command failed: %s",
    "库文件补全失败（%s）：%s": "Library completion failed (%s): %s",
    "已安装并重命名为 %s": "Installed and renamed to %s",
    "已安装为 %s，重命名为 %s 失败：%s":
        "Installed as %s; renaming to %s failed: %s",
    # ---- 启动器自身更新 ----
    "版本 %s  ·  Python + tkinter": "Version %s  ·  Python + tkinter",
    "此版本已是最新（v%s）": "This is the latest version (v%s).",
    "发现新版本：v%s": "New version available: v%s",
    "检查更新失败：%s": "Update check failed: %s",
    "发现新版本": "New version available",
    "当前版本：v%s  →  最新版本：v%s": "Current: v%s  →  Latest: v%s",
    "（该版本没有填写更新说明）": "(This release has no notes.)",
    "立即更新": "Update now",
    "稍后": "Later",
    "正在下载更新 ...": "Downloading update ...",
    "正在下载更新 ... %d%%（%.1f / %.1f MB）":
        "Downloading update ... %d%% (%.1f / %.1f MB)",
    "正在下载更新 ... %.1f MB": "Downloading update ... %.1f MB",
    "下载完成，正在准备重启 ...": "Download complete, preparing to restart ...",
    "下载失败：%s": "Download failed: %s",
    "更新已下载": "Update downloaded",
    "检测到 Minecraft 正在启动或运行，更新将在游戏结束后自动应用并重启启动器。":
        "Minecraft is starting or running. The update will be applied and the "
        "launcher restarted automatically after the game exits.",
    "更新失败": "Update failed",
    "未找到 GitHub 仓库或还没有发布任何版本。":
        "GitHub repository not found or no release has been published yet.",
    "GitHub 访问频率受限，请稍后再试。":
        "GitHub rate limit reached. Please try again later.",
    "发布信息中没有版本号。": "The release has no version tag.",
    "新版本没有提供可用的更新文件。":
        "The new release provides no usable update file.",
    "该版本只提供 .py 文件，请手动下载替换。":
        "This release only ships a .py file; please download and replace it manually.",
}


def theme_name():
    return _THEME_NAME if _THEME_NAME in THEMES else "deep_blue"


def set_theme_name(name):
    global _THEME_NAME
    _THEME_NAME = name if name in THEMES else "deep_blue"


def theme_palette(name=None):
    return THEMES.get(name or theme_name(), THEMES["deep_blue"])


def _role_color(v):
    """调色板角色名 → 色值；#rrggbb 原样返回。"""
    return theme_palette().get(v, v)


def set_launcher_language(lang):
    global _LANG
    _LANG = "en_US" if str(lang).lower().startswith("en") else "zh_CN"


def lang_combo_index(lang):
    """配置里的语言值 -> 下拉框索引（0 中文 / 1 English）。

    历史版本可能写入 "zh-CN"、"zh"、"" 等非标准值；只要不是英文一律按中文，
    与 set_launcher_language() 的判定保持一致，避免界面是中文但下拉框显示英文。
    """
    return 1 if str(lang or "").strip().lower().startswith("en") else 0


def _t(text):
    """界面语言为英文时返回对应翻译，否则原样返回。"""
    return _EN.get(text, text) if _LANG == "en_US" else text


def restart_launcher():
    """原地重启启动器，用于让界面语言切换生效。"""
    try:
        if getattr(sys, "frozen", False):
            os.execl(sys.executable, sys.executable, *sys.argv[1:])
        else:
            os.execl(sys.executable, sys.executable, os.path.abspath(__file__))
    except Exception:
        pass


def _dp(name): return os.path.join(DATA_DIR, name)


def _deep_merge(base, override):
    result = {}
    for k, v in base.items():
        if k in override:
            if isinstance(v, dict) and isinstance(override[k], dict):
                result[k] = _deep_merge(v, override[k])
            else:
                result[k] = override[k]
        else:
            result[k] = v
    for k, v in override.items():
        if k not in result:
            result[k] = v
    return result


def _load_json(name, default):
    p = _dp(name)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(default, dict):
                return _deep_merge(default, data)
            return data
        except Exception:
            pass
    return json.loads(json.dumps(default))


def _save_json(name, data):
    p = _dp(name)
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except Exception:
        try: os.remove(tmp)
        except Exception: pass
        raise


def load_config():
    cfg_missing = not os.path.exists(_dp("config.json"))
    c = _load_json("config.json", DEFAULT_CONFIG)
    if c.get("title") in ("闪电启动器", "LCL"):
        c["title"] = "Lightning Craft Launcher"
        cfg_missing = True
    if cfg_missing:
        # 首次运行（或迁移后）立即落盘默认配置，保证 config.json 存在
        try:
            _save_json("config.json", c)
        except Exception:
            pass
    return c


def save_config(c):
    _save_json("config.json", c)


def update_config(**fields):
    c = load_config()
    c.update(fields)
    save_config(c)
    return c


def effective_microsoft_client_id(cfg=None):
    """实际使用的微软 Client ID：设置页填写优先，留空则用内置公共 ID。"""
    cfg = cfg if cfg is not None else load_config()
    return (cfg.get("microsoft_client_id") or "").strip() or DEFAULT_MICROSOFT_CLIENT_ID


def load_accounts(): return _load_json("accounts.json", {"accounts": []})


def save_accounts(d): _save_json("accounts.json", d)


def load_versions(): return _load_json("versions.json", {"versions": []})


def save_versions(d): _save_json("versions.json", d)


def _merge_tree(src, dst):
    """把 src 目录内容合并进 dst（同名文件保留 dst 已有版本）。"""
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(src):
        s, d = os.path.join(src, name), os.path.join(dst, name)
        if os.path.isdir(s) and not os.path.islink(s):
            _merge_tree(s, d)
        elif not os.path.exists(d):
            shutil.move(s, d)


def ensure_libraries_dir(mc_dir):
    """把 <mc>/libraries 统一指向 data/dependencies，让依赖库实际下载到 data/dependencies。

    minecraft-launcher-lib 的所有路径（安装与 -cp 拼接）都写死了
    <mc>/libraries，直接改路径会让生成的启动命令找不到 jar。
    因此这里用「目录链接」把两者绑定：下载写入的是 data/dependencies，
    库的代码看到的仍是 <mc>/libraries，两边都不需要改逻辑。
    """
    link = os.path.join(mc_dir, "libraries")
    try:
        os.makedirs(LIBRARIES_DIR, exist_ok=True)
    except Exception:
        return link
    try:
        if os.path.islink(link):
            if os.path.realpath(link) == os.path.realpath(LIBRARIES_DIR):
                return link
            os.unlink(link)
        elif os.path.isdir(link):
            # Windows 的目录联接（junction）在部分 Python 版本里不算 islink，
            # 先按真实路径判断，避免把自己合并到自己身上
            if os.path.realpath(link) == os.path.realpath(LIBRARIES_DIR):
                return link
            # 旧版本已下载到真实目录：先合并到 data/dependencies，成功后再替换成链接
            try:
                _merge_tree(link, LIBRARIES_DIR)
                shutil.rmtree(link)
            except Exception:
                return link
        elif os.path.exists(link):
            return link
        if sys.platform == "win32":
            r = subprocess.run(
                ["cmd", "/c", "mklink", "/J", link, LIBRARIES_DIR],
                capture_output=True, text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode != 0:
                os.symlink(LIBRARIES_DIR, link, target_is_directory=True)
        else:
            os.symlink(LIBRARIES_DIR, link, target_is_directory=True)
    except Exception:
        pass
    return link


def version_mods_dir(mc_dir, version):
    """某个版本的 Mod 目录：<mc>/versions/<version>/mods。"""
    return os.path.join(mc_dir, "versions", version, "mods")


def ensure_minecraft_dir():
    mc_dir = load_config().get("minecraft_directory") or DEFAULT_CONFIG["minecraft_directory"]
    try:
        os.makedirs(os.path.join(mc_dir, "versions"), exist_ok=True)
        ensure_libraries_dir(mc_dir)
        return True, ""
    except Exception as e:
        return False, f"无法创建游戏目录：{mc_dir}\n{e}"


def _version_java_info(v):
    """读取已安装版本 JSON 里的 javaVersion（majorVersion, component）。"""
    try:
        if not v or os.path.basename(v) != v:
            return None, ""
        mc = load_config().get("minecraft_directory") or DEFAULT_CONFIG["minecraft_directory"]
        p = os.path.join(mc, "versions", v, v + ".json")
        if not os.path.isfile(p):
            return None, ""
        with open(p, "r", encoding="utf-8") as f:
            jv = json.load(f).get("javaVersion") or {}
        maj = jv.get("majorVersion")
        if isinstance(maj, int) and maj > 0:
            return maj, (jv.get("component") or "")
    except Exception:
        pass
    return None, ""


def required_java_major(v):
    # 优先用 Mojang 版本 JSON 里声明的 javaVersion，最准确（26.x 起需要 Java 25）
    maj, _comp = _version_java_info(v)
    if maj:
        return maj
    try:
        s = (v or "").strip()
        # 老式快照命名（15w14a / 24w14potato 等）按年份判断
        m = re.match(r"^(\d{2})w\d{2}", s)
        if m:
            year = int(m.group(1))
            if year <= 20: return 8
            if year <= 23: return 17
            if year <= 25: return 21
            return 25
        parts = re.findall(r"\d+", s)
        if not parts: return 17
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
        # 新版本号（26.1、26.4-snapshot-5）需要 Java 25
        if 26 <= major <= 99: return 25
        # 远古 alpha 快照（如 inf-20100618）与其它非 1.x 版本
        if major != 1: return 8
        if minor <= 16: return 8
        if minor <= 20 and not (minor == 20 and patch >= 5): return 17
        return 21
    except Exception:
        return 17


def _runtime_major_from_path(p):
    """从 <游戏目录>/runtime/<组件>/... 的路径里推断 Java 大版本。"""
    try:
        m = re.search(r"[\\/]runtime[\\/]([^\\/]+)[\\/]", p or "")
        if m:
            return _JAVA_COMPONENT_MAJOR.get(m.group(1))
    except Exception:
        pass
    return None


def java_major_from_path(p):
    if not p or not os.path.isfile(p):
        return None
    try:
        mtime = os.path.getmtime(p)
    except Exception:
        mtime = 0
    key = (p, mtime)
    if key in _JAVA_CACHE:
        return _JAVA_CACHE[key]
    res = None
    text = ""
    try:
        cf = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.run([p, "-version"], capture_output=True, text=True,
                              errors="replace", timeout=8, creationflags=cf)
        text = (proc.stderr or "") + (proc.stdout or "")
    except Exception:
        pass
    m = re.search(r'version "(\d+)(?:\.(\d+))?', text) if text else None
    if m:
        res = 8 if int(m.group(1)) == 1 else int(m.group(1))
    if res is None:
        # 执行失败（权限/杀毒拦截等）时，按 Mojang 运行时目录名兜底识别
        res = _runtime_major_from_path(p)
    _JAVA_CACHE[key] = res
    return res


def find_java_candidates():
    import shutil
    res, seen = [], set()
    w = shutil.which(_JAVA_EXE)
    if w:
        res.append(w); seen.add(w)
    jh = os.environ.get("JAVA_HOME", "")
    if jh:
        exe = os.path.join(jh, "bin", _JAVA_EXE)
        if os.path.isfile(exe): res.append(exe); seen.add(exe)
    # 启动器内下载的 Mojang 运行时（<游戏目录>/runtime/<组件>/<平台>/<组件>/bin/java）
    try:
        mc = load_config().get("minecraft_directory") or DEFAULT_CONFIG["minecraft_directory"]
        rt_base = os.path.join(mc, "runtime")
        if os.path.isdir(rt_base):
            for root, _dirs, files in os.walk(rt_base):
                if _JAVA_EXE in files and os.path.basename(root) == "bin":
                    exe = os.path.join(root, _JAVA_EXE)
                    if exe not in seen:
                        res.append(exe); seen.add(exe)
    except Exception:
        pass
    for base in _JAVA_DIRS:
        if not os.path.isdir(base): continue
        try:
            for name in os.listdir(base):
                exe = os.path.join(base, name, "bin", _JAVA_EXE)
                if os.path.isfile(exe) and exe not in seen:
                    res.append(exe); seen.add(exe)
        except Exception:
            pass
        try:
            for root, dirs, files in os.walk(base):
                depth = root[len(base):].count(os.sep)
                if depth > 3: dirs[:] = []; continue
                if _JAVA_EXE in files and os.path.basename(root) == "bin":
                    exe = os.path.join(root, _JAVA_EXE)
                    if exe not in seen: res.append(exe); seen.add(exe)
                if len(res) >= 30: break
        except Exception:
            pass
        if len(res) >= 30: break
    return res


def resolve_java(v, cfg_path=""):
    need = required_java_major(v)
    if cfg_path and os.path.isfile(cfg_path):
        actual = java_major_from_path(cfg_path)
        if actual is not None and actual >= need: return cfg_path, need
    # 启动器内下载过的运行时按大版本记录在配置里，重启后优先复用
    try:
        saved = (load_config().get("java_runtime_paths") or {}).get(str(need), "")
    except Exception:
        saved = ""
    if saved and os.path.isfile(saved):
        return saved, need
    cands = find_java_candidates()
    for exe in cands:
        if java_major_from_path(exe) == need: return exe, need
    for exe in cands:
        a = java_major_from_path(exe)
        if a is not None and a >= need: return exe, need
    return "", need


def remember_java_runtime(major, path):
    """记录启动器内下载的 Java 路径，供后续启动直接复用。"""
    if not path or not os.path.isfile(path):
        return
    try:
        mapping = dict(load_config().get("java_runtime_paths") or {})
        mapping[str(int(major))] = path
        update_config(java_runtime_paths=mapping)
    except Exception:
        pass


def _java_runtime_component(major):
    """Java 大版本 -> Mojang 运行时组件名。"""
    return {8: "jre-legacy", 16: "java-runtime-alpha", 17: "java-runtime-gamma",
            21: "java-runtime-delta", 25: "java-runtime-epsilon"}.get(int(major), "")


def _java_runtime_component_for_version(version, need, mc_dir):
    """优先使用版本 JSON 声明的组件，缺失时按 Java 大版本推断。"""
    try:
        p = os.path.join(mc_dir, "versions", str(version), str(version) + ".json")
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                jv = json.load(f).get("javaVersion") or {}
            comp = jv.get("component") or ""
            if comp and int(jv.get("majorVersion") or 0) == int(need):
                return comp
    except Exception:
        pass
    return _java_runtime_component(need)


def _natural_version_key(name):
    parts = re.split(r'[.\-_]', name)
    result = []
    for p in parts:
        if not p: continue
        if p.isdigit():
            result.append((0, int(p)))
        else:
            result.append((1, p.lower()))
    return result


def _validate_version_name(name):
    if not name or name in (".", ".."):
        return False, "名称非法"
    if name != name.strip():
        return False, "名称首尾不能有空格"
    if os.path.basename(name) != name:
        return False, "名称非法"
    if any(c in name for c in r'\/:*?"<>|'):
        return False, "名称含非法字符"
    if any(ord(c) < 32 for c in name):
        return False, "名称含非法字符"
    # Windows 下目录名不能以点或空格结尾
    if sys.platform == "win32" and name[-1] in ". ":
        return False, "名称不能以点或空格结尾"
    if sys.platform == "win32" and name.upper().split(".")[0] in _WIN_RESERVED:
        return False, "名称为系统保留名"
    return True, ""


def safe_version_dir(mc_dir, name):
    """校验版本名后返回版本目录路径；非法名称返回 None。"""
    ok, _ = _validate_version_name(name)
    if not ok:
        return None
    return os.path.join(mc_dir, "versions", name)


def offline_uuid(name):
    import hashlib, uuid as _uuid
    data = hashlib.md5(("OfflinePlayer:" + name).encode("utf-8")).digest()
    b = bytearray(data)
    b[6] = (b[6] & 0x0F) | 0x30
    b[8] = (b[8] & 0x3F) | 0x80
    return str(_uuid.UUID(bytes=bytes(b)))


def rename_version_dir(mc_dir, old_name, new_name):
    ok, err = _validate_version_name(new_name)
    if not ok:
        return False, err
    # 旧名称同样要校验，避免被篡改的 versions.json 做路径穿越
    if not _validate_version_name(old_name)[0]:
        return False, "源版本名非法"
    vr = os.path.join(mc_dir, "versions")
    old_dir = os.path.join(vr, old_name)
    new_dir = os.path.join(vr, new_name)
    if os.path.dirname(os.path.abspath(new_dir)) != os.path.abspath(vr):
        return False, "名称非法"
    if not os.path.isdir(old_dir): return False, "源版本不存在"
    if os.path.exists(new_dir): return False, "目标名称已存在"
    try:
        os.rename(old_dir, new_dir)
        for suf in (".json", ".jar"):
            of = os.path.join(new_dir, old_name + suf)
            nf = os.path.join(new_dir, new_name + suf)
            if os.path.exists(of):
                os.rename(of, nf)
        new_json = os.path.join(new_dir, f"{new_name}.json")
        if os.path.exists(new_json):
            with open(new_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["id"] = new_name
            if data.get("jar") == old_name:
                data["jar"] = new_name
            # 原子写入，避免中途失败导致版本 JSON 被截断
            tmp_json = new_json + ".tmp"
            with open(tmp_json, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush(); os.fsync(f.fileno())
            os.replace(tmp_json, new_json)
        for vn in os.listdir(vr):
            vd = os.path.join(vr, vn)
            if not os.path.isdir(vd) or vn == new_name: continue
            vj = os.path.join(vd, f"{vn}.json")
            if not os.path.exists(vj): continue
            try:
                with open(vj, "r", encoding="utf-8") as f:
                    vdata = json.load(f)
                changed = False
                if vdata.get("inheritsFrom") == old_name:
                    vdata["inheritsFrom"] = new_name; changed = True
                if vdata.get("jar") == old_name:
                    vdata["jar"] = new_name; changed = True
                if changed:
                    tmp_vj = vj + ".tmp"
                    with open(tmp_vj, "w", encoding="utf-8") as f:
                        json.dump(vdata, f, ensure_ascii=False, indent=2)
                        f.flush(); os.fsync(f.fileno())
                    os.replace(tmp_vj, vj)
            except Exception:
                continue
        return True, ""
    except Exception as e:
        try:
            if os.path.isdir(new_dir) and not os.path.isdir(old_dir):
                for suf in (".json", ".jar"):
                    nf = os.path.join(new_dir, new_name + suf)
                    of = os.path.join(new_dir, old_name + suf)
                    if os.path.exists(nf):
                        os.rename(nf, of)
                os.rename(new_dir, old_dir)
        except Exception:
            pass
        return False, str(e)


def graceful_close_process(pid):
    """请求游戏正常退出：Windows 给游戏窗口发 WM_CLOSE，POSIX 发 SIGTERM。

    不走 taskkill /F，让 Minecraft 自己走保存进度、正常关闭的流程。
    返回 True 表示关闭请求已发出。
    """
    try:
        pid = int(pid)
    except Exception:
        return False
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            WM_CLOSE = 0x0010
            handles = []
            enum_proc = ctypes.WINFUNCTYPE(
                wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def _collect(hwnd, _lparam):
                p = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
                # 只关游戏自己的可见窗口，避免误发到其它进程
                if p.value == pid and user32.IsWindowVisible(hwnd):
                    handles.append(hwnd)
                return True

            user32.EnumWindows(enum_proc(_collect), 0)
            for hwnd in handles:
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            return bool(handles)
        import signal
        os.kill(pid, signal.SIGTERM)
        return True
    except Exception:
        return False


def _canonical_loader_main_class(name):
    """返回加载器应有的主类；不是已知加载器主类则返回 None。"""
    s = (name or "").strip()
    if not s.endswith(".launch.knot.KnotClient"):
        return None
    low = s.lower()
    for key, canon in _LOADER_MAIN_CLASSES.items():
        if key in low:
            return canon
    return None


def repair_loader_main_class(mc_dir, version):
    """启动前修复被写坏的加载器主类（例如 Fabric 的 KnotClient 类名出现乱码）。

    返回被修复的版本名列表。
    """
    vr = os.path.join(mc_dir, "versions")
    fixed, name = [], version
    for _ in range(5):
        p = os.path.join(vr, name, name + ".json")
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            break
        mc = data.get("mainClass", "")
        canon = _canonical_loader_main_class(mc)
        if canon and canon != mc:
            data["mainClass"] = canon
            tmp = p + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.flush(); os.fsync(f.fileno())
                os.replace(tmp, p)
                fixed.append(name)
            except Exception:
                try:
                    os.remove(tmp)
                except Exception:
                    pass
        name = data.get("inheritsFrom")
        if not name:
            break
    return fixed


def _version_library_entries(mc_dir, version):
    """按 inheritsFrom 链收集版本用到的库条目（用于检查文件是否齐全）。"""
    vr = os.path.join(mc_dir, "versions")
    entries, name = [], version
    for _ in range(5):
        try:
            with open(os.path.join(vr, name, name + ".json"), "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            break
        libs = data.get("libraries")
        if isinstance(libs, list):
            entries.extend(libs)
        name = data.get("inheritsFrom")
        if not name:
            break
    return entries


def _maven_rel_path(name):
    """从 Maven 坐标（group:artifact:version[:classifier][@ext]）推导相对 libraries 的路径。

    Fabric/Quilt/Forge 等加载器库在版本 JSON 里只有 ``name`` + ``url``，
    没有 ``downloads.artifact``，需要按 Maven 规则把坐标转成相对路径，
    否则缺失检查（missing_version_libraries / missing_loader_library）
    会直接跳过这些库，导致加载器 JAR 丢了也照常启动，Java 报「找不到主类」。
    """
    if not isinstance(name, str):
        return None
    if "@" in name:
        name, suffix = name.split("@", 1)
    else:
        suffix = "jar"
    parts = name.split(":")
    if len(parts) < 3:
        return None
    base, libname, version = parts[0], parts[1], parts[2]
    extra = parts[3:]
    group_path = base.replace(".", "/")
    filename = f"{libname}-{version}" + "".join(f"-{e}" for e in extra) + f".{suffix}"
    return f"{group_path}/{libname}/{version}/{filename}"


def _lib_artifact(lib):
    """返回 (相对路径, 下载地址)，不适用于当前系统或无下载地址则返回 None。"""
    if not isinstance(lib, dict):
        return None
    if "rules" in lib:
        try:
            from minecraft_launcher_lib._helper import parse_rule_list
            if not parse_rule_list(lib["rules"], {}):
                return None
        except Exception:
            pass
    # 1. Mojang 风格：downloads.artifact 直接给出 path 与 url
    art = (lib.get("downloads") or {}).get("artifact") or {}
    rel, url = art.get("path") or "", art.get("url") or ""
    if rel and url:
        return rel, url
    # 2. Maven 风格（Fabric/Quilt/Forge 等加载器库）：只有 name + url，path 需从坐标推导
    name = lib.get("name")
    if name:
        rel = _maven_rel_path(name)
        if rel:
            base_url = lib.get("url") or "https://libraries.minecraft.net"
            if not base_url.endswith("/"):
                base_url += "/"
            url = base_url + rel
            return rel, url
    return None


def _lib_native_artifact(lib):
    """返回当前系统原生库的 (相对路径, 下载地址)，无原生库则返回 None。"""
    if not isinstance(lib, dict):
        return None
    native = None
    try:
        from minecraft_launcher_lib.natives import get_natives
        native = get_natives(lib)
    except Exception:
        native = None
    classifiers = (lib.get("downloads") or {}).get("classifiers") or {}
    # 个别旧版本库没有 natives 字段，按当前系统从 classifiers 里猜一个
    if not native and classifiers:
        if sys.platform == "win32":
            cand = [k for k in classifiers if "windows" in k]
        elif sys.platform == "darwin":
            cand = [k for k in classifiers if "osx" in k or "macos" in k]
        else:
            cand = [k for k in classifiers if "linux" in k]
        if cand:
            native = cand[0]
    if not native:
        return None
    cl = classifiers.get(native) or {}
    rel, url = cl.get("path") or "", cl.get("url") or ""
    if rel and url:
        return rel, url
    # 没有 downloads.classifiers 时按 Maven 坐标推导（部分旧版本写法）
    name = lib.get("name")
    if name:
        rel = _maven_rel_path(f"{name}:{native}")
        if rel:
            base_url = lib.get("url") or "https://libraries.minecraft.net"
            if not base_url.endswith("/"):
                base_url += "/"
            return rel, base_url + rel
    return None


def _lib_missing(mc_dir, rel):
    try:
        full = os.path.join(mc_dir, "libraries", rel)
        return not os.path.isfile(full) or os.path.getsize(full) <= 0
    except Exception:
        return True


def missing_version_libraries(mc_dir, version):
    """返回缺失或为空文件的库（相对 libraries 的路径），可交给安装流程补全。"""
    missing = []
    for lib in _version_library_entries(mc_dir, version):
        for art in (_lib_artifact(lib), _lib_native_artifact(lib)):
            if not art:
                continue
            rel = art[0]
            if _lib_missing(mc_dir, rel) and rel not in missing:
                missing.append(rel)
    return missing


def missing_loader_library(mc_dir, version):
    """只检查 Fabric/Quilt 核心加载器库，缺失时启动必定报找不到主类。"""
    out = []
    for lib in _version_library_entries(mc_dir, version):
        name = str((lib or {}).get("name", ""))
        if not (name.startswith("net.fabricmc:fabric-loader")
                or name.startswith("org.quiltmc:quilt-loader")):
            continue
        art = _lib_artifact(lib)
        if art and _lib_missing(mc_dir, art[0]) and art[0] not in out:
            out.append(art[0])
    return out


def version_json_issue(mc_dir, version):
    """检测版本 JSON 是否异常（损坏、空文件、父版本断链）。

    返回问题描述字符串；正常时返回 None。版本 JSON 本身缺失时返回 None，
    因为这种情况可以由安装流程重新下载生成。
    """
    vr = os.path.join(mc_dir, "versions")
    name, seen = version, set()
    for _ in range(6):
        if not isinstance(name, str) or not name or name in seen:
            return "父版本链损坏（存在循环引用或名称非法）"
        seen.add(name)
        p = os.path.join(vr, name, name + ".json")
        if not os.path.isfile(p):
            if name == version:
                # 当前版本 JSON 缺失：交给安装流程补全，不算「损坏」
                return None
            return "父版本「%s」缺少 JSON 文件" % name
        try:
            if os.path.getsize(p) <= 0:
                return "版本 JSON 文件为空：%s.json" % name
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            return "版本 JSON 无法解析（%s.json）：%s" % (name, e)
        if not isinstance(data, dict):
            return "版本 JSON 内容不是对象：%s.json" % name
        if not isinstance(data.get("id"), str) or not data.get("id", "").strip():
            return "版本 JSON 缺少有效 id 字段：%s.json" % name
        parent = data.get("inheritsFrom")
        if parent:
            name = parent
            continue
        # 没有父版本时必须是完整可启动的配置
        mc = data.get("mainClass")
        if not isinstance(mc, str) or not mc.strip():
            return "版本 JSON 缺少 mainClass：%s.json" % name
        if not isinstance(data.get("libraries"), list):
            return "版本 JSON 缺少 libraries 列表：%s.json" % name
        return None
    return "版本 JSON 的 inheritsFrom 层级过深"


def describe_version(mc_dir, name):
    """返回 (真实版本显示名, 加载器版本, 状态)。
    状态：ok 可用 / json_broken JSON损坏 / libs_missing 库文件缺失。"""
    if version_json_issue(mc_dir, name):
        return name, "", "json_broken"
    jf = os.path.join(mc_dir, "versions", name, name + ".json")
    try:
        with open(jf, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    # 真实游戏版本：优先 inheritsFrom（加载器/整合包 JSON），否则用 id
    mcver = data.get("inheritsFrom") or str(data.get("id") or name)
    # 从 libraries 的 Maven 坐标识别加载器及其版本（比重命名过的 id 更可靠）
    loader, lver = "", ""
    for lib in data.get("libraries") or []:
        if not isinstance(lib, dict):
            continue
        parts = str(lib.get("name", "")).split(":")
        if len(parts) < 3:
            continue
        g, a, v = parts[0], parts[1], parts[2]
        if g == "net.fabricmc" and a == "fabric-loader":
            loader, lver = "Fabric", v
        elif g == "org.quiltmc" and a == "quilt-loader":
            loader, lver = "Quilt", v
        elif g == "net.minecraftforge" and a in ("forge", "fmlloader"):
            loader, lver = "Forge", v.split("-", 1)[-1]
        elif g == "net.neoforged" and a in ("neoforge", "loader"):
            loader, lver = "NeoForge", v
    if not loader:
        # 兜底：从 JSON 的 id 猜（未经重命名的官方安装目录名）
        nid = str(data.get("id") or "")
        m = re.match(r"fabric-loader-([\w.]+)-(.+)$", nid)
        if m:
            loader, lver, mcver = "Fabric", m.group(1), m.group(2)
        else:
            m = re.match(r"quilt-loader-([\w.]+)-(.+)$", nid)
            if m:
                loader, lver, mcver = "Quilt", m.group(1), m.group(2)
            else:
                m = re.match(r"(.+)-forge-([\w.]+)$", nid)
                if m:
                    loader, lver, mcver = "Forge", m.group(2), m.group(1)
                else:
                    m = re.match(r"(?:neoforge-([\w.]+)|(.+)-neoforge-([\w.]+))$", nid)
                    if m:
                        loader = "NeoForge"
                        lver = m.group(1) or m.group(3)
                        mcver = m.group(2) or mcver
    real = ("%s %s" % (loader, mcver)) if loader else ("%s" % mcver)
    try:
        status = "libs_missing" if missing_version_libraries(mc_dir, name) else "ok"
    except Exception:
        status = "ok"
    return real, lver, status


def _dl_step_text(t):
    """把 mll 的英文下载步骤转换为中文显示。"""
    s = str(t or "")
    if _LANG == "en_US" or not s:
        return s
    low = s.lower()
    for k, zh in (("json", "下载索引文件"), ("meta", "下载索引文件"),
                  ("index", "下载索引文件"), ("librar", "下载库文件"),
                  ("asset", "下载资源文件"), ("client", "下载客户端"),
                  ("native", "下载原生库"), ("java", "下载 Java 运行时"),
                  ("fabric", "安装 Fabric 加载器"), ("neoforge", "安装 NeoForge 加载器"),
                  ("forge", "安装 Forge 加载器"), ("quilt", "安装 Quilt 加载器"),
                  ("extract", "解压文件")):
        if k in low:
            return zh
    return s


def _is_inside(base, path):
    """路径穿越防护：确认 path 落在 base 目录内。"""
    try:
        base = os.path.abspath(base)
        path = os.path.abspath(path)
        return os.path.commonpath([base, path]) == base
    except Exception:
        return False


def _cf_mod_download_url(project_id, file_id, api_key=""):
    """获取 CurseForge 模组下载直链：先试社区代理，再试 MCIM 镜像，最后试官方 API（需 Key）。"""
    try:
        import requests
    except ImportError:
        return ""
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    getter = _orig_requests_get or requests.get
    sources = [("https://api.curse.tools/v1/cf/mods/%s/files/%s/download-url"
                % (project_id, file_id), {"User-Agent": ua}),
               ("https://mod.mcimirror.top/curseforge/v1/mods/%s/files/%s/download-url"
                % (project_id, file_id), {"User-Agent": ua})]
    if api_key:
        sources.append(("https://api.curseforge.com/v1/mods/%s/files/%s/download-url"
                        % (project_id, file_id),
                        {"User-Agent": ua, "x-api-key": api_key}))
    for url, headers in sources:
        try:
            r = getter(url, headers=headers, timeout=12)
            if r.ok:
                data = r.json().get("data")
                if isinstance(data, str) and data.startswith("http"):
                    return data
        except Exception:
            continue
    return ""


def _is_april_fools_version(v):
    """判断清单条目是否为愚人节版本。

    清单里它们也是 snapshot 类型；用已知版本号 + 4 月 1 日发布日期识别。
    注意 3 月 31 日也有普通快照（22w13a 等），不能只看日期。
    """
    try:
        vid = str(v.get("id") or "")
        if vid.lower() in _APRIL_FOOLS_IDS:
            return True
        if v.get("type") != "snapshot":
            return False
        return str(v.get("releaseTime") or "")[5:10] == "04-01"
    except Exception:
        return False


def _track_stream(resp):
    with _ACTIVE_LOCK:
        _ACTIVE_STREAMS.add(resp)


def interrupt_active_downloads():
    """取消下载：关闭正在读取的响应并阻止新请求，避免卡在「正在取消」。

    r.close() 在极端情况下可能等待套接字收尾，放到后台线程执行，
    保证点「取消下载」时界面线程不会被网络操作阻塞。
    """
    _download_cancelled.set()
    with _ACTIVE_LOCK:
        responses = list(_ACTIVE_STREAMS)
        _ACTIVE_STREAMS.clear()

    def _close_all(items):
        for r in items:
            try:
                r.close()
            except Exception:
                pass
    if responses:
        threading.Thread(target=_close_all, args=(responses,), daemon=True).start()


def reset_download_cancel():
    _download_cancelled.clear()


def set_download_source(src):
    global _DOWNLOAD_SOURCE
    _DOWNLOAD_SOURCE = src if src in ("official", "mirror") else "mirror"


def _rewrite_mirror_url(url):
    """镜像模式下把原版相关官方域名替换为 BMCLAPI；其它地址原样返回。"""
    # Java 运行时下载固定走官方源：_JAVA_SOURCE_OVERRIDE 为 "official" 时跳过镜像重写
    src = _JAVA_SOURCE_OVERRIDE or _DOWNLOAD_SOURCE
    if src != "mirror":
        return url
    for src_host, dst in _MIRROR_HOSTS.items():
        if url.startswith(src_host):
            return dst + url[len(src_host):]
    return url


def patch_requests_mirror():
    """一次性接管 requests，让所有原版资源下载按当前源走。"""
    global _orig_requests_get, _orig_session_get
    if _orig_requests_get is not None:
        return
    import requests
    _orig_requests_get = requests.get
    _orig_session_get = requests.Session.get

    def _prep(url, kwargs):
        if _download_cancelled.is_set():
            raise RuntimeError("下载已取消")
        # mll 的下载请求没有超时，断网时会永久挂起，这里统一补上
        kwargs.setdefault("timeout", (10, 30))
        headers = kwargs.get("headers")
        if not headers:
            kwargs["headers"] = {"User-Agent": _HTTP_UA}
        else:
            headers.setdefault("User-Agent", _HTTP_UA)
        return _rewrite_mirror_url(url)

    def _send(func, args, kwargs):
        """网络抖动/镜像节点偶发 5xx 时自动重试几次，避免下载静默失败。"""
        import requests as rq
        last_err = None
        delay = 0.6
        for attempt in range(3):
            if _download_cancelled.is_set():
                raise RuntimeError("下载已取消")
            try:
                r = func(*args, **kwargs)
            except rq.exceptions.RequestException as e:
                last_err = e
            else:
                if r.status_code < 500 and r.status_code != 429:
                    return r
                try:
                    r.close()
                except Exception:
                    pass
                last_err = None
            if attempt < 2:
                time.sleep(delay); delay *= 2
        if last_err is not None:
            raise last_err
        raise RuntimeError("服务器返回错误")

    def _patched_get(url, *args, **kwargs):
        r = _send(_orig_requests_get, (_prep(url, kwargs),) + args, kwargs)
        if kwargs.get("stream"):
            _track_stream(r)
        return r

    def _patched_session_get(self, url, *args, **kwargs):
        r = _send(_orig_session_get, (self, _prep(url, kwargs)) + args, kwargs)
        if kwargs.get("stream"):
            _track_stream(r)
        return r

    requests.get = _patched_get
    # minecraft-launcher-lib 下载 libraries/assets 用的是 Session.get，
    # 只 patch requests.get 的话镜像重写对实际文件下载不生效
    requests.Session.get = _patched_session_get


def patch_java_runtime_source():
    """让补全游戏版本时自动安装的 Java 运行时固定走官方源。

    minecraft-launcher-lib 的 install_minecraft_version 内部会用全局下载源
    安装运行时，若游戏源为 BMCLAPI 则 Java 也会被串到镜像，这里强制走官方。
    """
    global _JAVA_SOURCE_OVERRIDE
    try:
        import minecraft_launcher_lib.install as mll_install
    except Exception:
        return
    orig = mll_install.install_jvm_runtime
    if getattr(orig, "_lcl_source_patched", False):
        return

    def _wrapped(jvm_version, minecraft_directory, callback=None, max_workers=None):
        global _JAVA_SOURCE_OVERRIDE
        prev = _JAVA_SOURCE_OVERRIDE
        try:
            _JAVA_SOURCE_OVERRIDE = "official"
            return orig(jvm_version, minecraft_directory, callback, max_workers)
        finally:
            _JAVA_SOURCE_OVERRIDE = prev

    _wrapped._lcl_source_patched = True
    mll_install.install_jvm_runtime = _wrapped


def _neoforge_versions():
    """从 BMCLAPI 镜像的 maven-metadata.xml 取 NeoForge 版本列表（带缓存）。"""
    import requests
    with _NEOFORGE_VERSIONS_LOCK:
        if (_NEOFORGE_VERSIONS["versions"]
                and time.time() - _NEOFORGE_VERSIONS["time"] < 1800):
            return list(_NEOFORGE_VERSIONS["versions"])
        for url in _NEOFORGE_META_URLS:
            try:
                r = requests.get(url, headers={"User-Agent": _HTTP_UA},
                                 timeout=(8, 20))
                r.raise_for_status()
                versions = re.findall(r"<version>(.*?)</version>", r.text)
                if versions:
                    _NEOFORGE_VERSIONS["time"] = time.time()
                    _NEOFORGE_VERSIONS["versions"] = versions
                    return list(versions)
            except Exception:
                continue
        return []


def _download_neo_installer(urls, dest):
    import requests
    last = None
    for url in urls:
        try:
            r = requests.get(url, stream=True,
                             headers={"User-Agent": _HTTP_UA},
                             timeout=(10, 60))
            try:
                if r.status_code != 200:
                    last = RuntimeError("HTTP %d" % r.status_code)
                    continue
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(1 << 15):
                        if _download_cancelled.is_set():
                            raise RuntimeError("下载已取消")
                        if chunk:
                            f.write(chunk)
                if os.path.getsize(dest) > 0:
                    return
                last = RuntimeError("文件为空")
            finally:
                try:
                    r.close()
                except Exception:
                    pass
        except Exception as e:
            last = e
    raise RuntimeError("NeoForge 安装器下载失败：%s" % last)


def _patch_neo_installer_jar(jar_path):
    """把安装器内嵌 JSON 里的官方 Maven 地址替换成 BMCLAPI 镜像地址。

    返回 {镜像地址: 原官方地址} 的映射，安装完成后据此把版本 JSON 还原。
    """
    import zipfile
    changed = 0
    mapping = {}
    url_re = re.compile(r"https://(?:%s|libraries\.minecraft\.net)/[^\"\s,]+"
                        % re.escape(_NEOFORGE_OFFICIAL.split("://", 1)[1]))
    tmp = jar_path + ".patched"
    with zipfile.ZipFile(jar_path) as zin, \
            zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename in ("version.json", "install_profile.json"):
                text = data.decode("utf-8", "replace")
                for orig in url_re.findall(text):
                    mapping[orig.replace(_NEOFORGE_OFFICIAL, _NEOFORGE_MIRROR)
                            .replace(_NEOFORGE_LIBS_HOST, _NEOFORGE_MIRROR)] = orig
                new = (text.replace(_NEOFORGE_OFFICIAL, _NEOFORGE_MIRROR)
                           .replace(_NEOFORGE_LIBS_HOST, _NEOFORGE_MIRROR))
                if new != text:
                    changed += 1
                data = new.encode("utf-8")
            zout.writestr(item, data)
    if changed:
        os.replace(tmp, jar_path)
    else:
        try:
            os.remove(tmp)
        except Exception:
            pass
    return mapping


def _run_neo_installer(jar_path, java, mc_dir):
    """运行安装器并把它输出的最后几行带进异常，方便用户看到真实原因。"""
    try:
        from minecraft_launcher_lib._helper import SUBPROCESS_STARTUP_INFO as si
    except Exception:
        si = None
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run(
            [java, "-jar", jar_path, "--install-client", mc_dir],
            cwd=os.path.dirname(jar_path), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", startupinfo=si, timeout=900, **kwargs)
    except subprocess.TimeoutExpired:
        raise RuntimeError("NeoForge 安装器运行超时（15 分钟）")
    out = proc.stdout or ""
    if proc.returncode != 0:
        tail = "\n".join(out.strip().splitlines()[-12:])
        raise RuntimeError("NeoForge 安装器失败（退出码 %d）：\n%s"
                           % (proc.returncode, tail))
    return out


def _restore_neo_version_urls(mc_dir, loader_version, mapping=None):
    """安装完成后把版本 JSON 里的镜像地址换回官方地址，避免其它工具不认。"""
    vid = "neoforge-%s" % loader_version
    p = os.path.join(mc_dir, "versions", vid, vid + ".json")
    try:
        with open(p, "r", encoding="utf-8") as f:
            text = f.read()
        if mapping:
            for mirror_url, orig_url in mapping.items():
                text = text.replace(mirror_url, orig_url)
        else:
            text = text.replace(_NEOFORGE_MIRROR, _NEOFORGE_OFFICIAL)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, p)
    except Exception:
        pass


def _install_neoforge_mirrored(mc_version, mc_dir, java, loader_version, callback):
    import tempfile
    from minecraft_launcher_lib.vanilla_launcher import (
        do_vanilla_launcher_profiles_exists,
        create_empty_vanilla_launcher_profiles_file)
    if not do_vanilla_launcher_profiles_exists(mc_dir):
        create_empty_vanilla_launcher_profiles_file(mc_dir)
    jar_name = "neoforge-%s-installer.jar" % loader_version
    mirror_mode = (load_config().get("download_source", "mirror")
                   != "official")
    urls = [
        _NEOFORGE_MIRROR + "/net/neoforged/neoforge/%s/%s"
        % (loader_version, jar_name),
        _NEOFORGE_OFFICIAL + "/net/neoforged/neoforge/%s/%s"
        % (loader_version, jar_name),
    ]
    if not mirror_mode:
        urls.reverse()
    status = callback.get("setStatus", lambda *_: None)
    mapping = {}
    with tempfile.TemporaryDirectory(prefix="lcl-neoforge-") as td:
        jar = os.path.join(td, jar_name)
        status(_t("正在下载 NeoForge 安装器 ..."))
        _download_neo_installer(urls, jar)
        status(_t("正在运行 NeoForge 安装器 ..."))
        # 官方源下不篡改安装器下载地址，尊重用户选择
        if mirror_mode:
            mapping = _patch_neo_installer_jar(jar)
        last_err = None
        for attempt in range(1, 4):
            try:
                _run_neo_installer(jar, str(java), str(mc_dir))
                last_err = None
                break
            except RuntimeError as e:
                last_err = e
                # BMCLAPI 偶尔会把请求 302 到不稳定的节点，个别库会下载失败；
                # 已下好的库下次运行会跳过，所以原样重跑一般就能补齐。
                if ("failed to download" not in str(e)
                        and "error during" not in str(e)):
                    raise
                if attempt < 3:
                    status(_t("安装器下载中断，正在重试（%d/3）...") % attempt)
                    time.sleep(1.5)
        if last_err is not None:
            raise last_err
    _restore_neo_version_urls(str(mc_dir), loader_version, mapping)


def patch_neoforge_mirror():
    """接管 NeoForge 安装：安装器与版本检测都改走 BMCLAPI，绕开被墙的官方 Maven。"""
    try:
        import minecraft_launcher_lib.mod_loader._neoforge as nf
    except Exception:
        return

    if not getattr(nf.Neoforge.install, "_lcl_neo_patched", False):
        def install(self, minecraft_version, minecraft_directory, callback, java,
                    loader_version):
            _install_neoforge_mirrored(minecraft_version, minecraft_directory,
                                       java, loader_version, callback or {})

        install._lcl_neo_patched = True
        nf.Neoforge.install = install

    if not getattr(nf.Neoforge.get_loader_versions, "_lcl_neo_patched", False):
        orig_loader_versions = nf.Neoforge.get_loader_versions

        def get_loader_versions(self, minecraft_version, stable_only):
            versions = _neoforge_versions()
            if versions:
                out = []
                for v in versions:
                    if "beta" in v and stable_only:
                        continue
                    m = nf._VERSION_REG_EX.match(v)
                    if not m:
                        continue
                    if self._normalize_minecraft_version(m.group()) == minecraft_version:
                        out.append(v)
                out.reverse()
                if out:
                    return out
            return orig_loader_versions(self, minecraft_version, stable_only)

        get_loader_versions._lcl_neo_patched = True
        nf.Neoforge.get_loader_versions = get_loader_versions

    if not getattr(nf.Neoforge.get_minecraft_versions, "_lcl_neo_patched", False):
        orig_mc_versions = nf.Neoforge.get_minecraft_versions

        def get_minecraft_versions(self, stable_only):
            versions = _neoforge_versions()
            if versions:
                found = []
                for v in versions:
                    if not v.startswith("0."):
                        m = nf._VERSION_REG_EX.match(v)
                        if m and self._normalize_minecraft_version(m.group()) not in found:
                            found.append(self._normalize_minecraft_version(m.group()))
                if found:
                    return found
            return orig_mc_versions(self, stable_only)

        get_minecraft_versions._lcl_neo_patched = True
        nf.Neoforge.get_minecraft_versions = get_minecraft_versions


def _err_text(e):
    """把网络异常转换成用户能看懂的提示，避免直接把异常类名抛给用户。"""
    name = type(e).__name__
    if "Timeout" in name:
        return _t("连接超时")
    if "SSL" in name or "Certificate" in name:
        return _t("证书/SSL 错误")
    if "JSON" in name or "Value" in name:
        return _t("数据格式异常")
    if "HTTP" in name:
        return _t("服务器返回错误")
    if "Connection" in name:
        return _t("网络连接失败")
    return _t("网络错误")


def _retry_http_get(get, url, timeout=10, attempts=3):
    """带退避重试的 GET，避免单次网络抖动就让加载器检测失败。"""
    import time
    delay = 0.7
    last = None
    for i in range(attempts):
        try:
            r = get(url, timeout=timeout, headers={"User-Agent": _HTTP_UA})
            if r.status_code < 500 and r.status_code != 429:
                return r
            last = r
        except Exception as e:
            last = e
        if i < attempts - 1:
            time.sleep(delay)
            delay *= 2
    if isinstance(last, Exception):
        raise last
    return last


def _fetch_loader_versions(lid, source):
    """返回某个加载器支持的 Minecraft 版本集合；按所选源优先，失败后回退另一源。"""
    import requests
    # 检测时已显式指定源，绕开全局镜像重写，保证回退到官方源真实生效
    get = _orig_requests_get or requests.get
    last_err = None
    if lid in ("fabric", "quilt"):
        mirror = ("https://bmclapi2.bangbang93.com/fabric-meta/v2/versions/game"
                  if lid == "fabric" else
                  "https://bmclapi2.bangbang93.com/quilt-meta/v3/versions/game")
        official = ("https://meta.fabricmc.net/v2/versions/game"
                    if lid == "fabric" else
                    "https://meta.quiltmc.org/v3/versions/game")
        urls = (mirror, official) if source == "mirror" else (official, mirror)
        result = set()
        for url in urls:
            try:
                r = _retry_http_get(get, url, timeout=6)
                r.raise_for_status()
                result |= {v["version"] for v in r.json() if isinstance(v, dict)}
            except Exception as e:
                last_err = e
        # 镜像元数据可能滞后（如 Quilt），两源结果取并集避免误判
        if result:
            return result
        if last_err is not None:
            raise last_err
        return set()

    if lid == "forge":
        mirror = "https://bmclapi2.bangbang93.com/forge/minecraft"
        official = "https://maven.minecraftforge.net/net/minecraftforge/forge/maven-metadata.xml"
        urls = (mirror, official) if source == "mirror" else (official, mirror)
        result = set()
        for url in urls:
            try:
                r = _retry_http_get(get, url, timeout=8)
                r.raise_for_status()
                if url.endswith(".xml"):
                    result |= {v.split("-", 1)[0]
                               for v in re.findall(r"<version>(.*?)</version>", r.text)}
                else:
                    result |= {str(v) for v in r.json()}
            except Exception as e:
                last_err = e
        if result:
            return result
        if last_err is not None:
            raise last_err
        return set()

    if lid == "neoforge":
        # NeoForge 官方 Maven 在国内经常超时/断连，这里多端点 + 重试兜底。
        # 按用户选择的源决定探测顺序：官方源优先官方端点，镜像源优先 BMCLAPI。
        mirror_url = ("https://bmclapi2.bangbang93.com/maven/"
                      "net/neoforged/neoforge/maven-metadata.xml")
        official_urls = [
            "https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge",
            "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml",
            "https://repo1.maven.org/maven2/net/neoforged/neoforge/maven-metadata.xml",
        ]
        urls = ([mirror_url] + official_urls if source == "mirror"
                else official_urls + [mirror_url])
        result = set()
        for url in urls:
            try:
                r = _retry_http_get(get, url, timeout=12)
                r.raise_for_status()
                if r.headers.get("content-type", "").startswith("application/xml") \
                        or url.endswith(".xml") or r.text.lstrip().startswith("<"):
                    versions = re.findall(r"<version>(.*?)</version>", r.text)
                else:
                    versions = r.json().get("versions", [])
                for s in versions:
                    if not isinstance(s, str) or s.startswith("0."):
                        continue
                    m = re.match(r"^\d+\.\d+", s)
                    if m:
                        # 20.4.237 -> 1.20.4，21.1.72 -> 1.21.1，21.0.x -> 1.21
                        result.add("1." + m.group(0).removesuffix(".0"))
                if result:
                    return result
            except Exception as e:
                last_err = e
        if result:
            return result
        if last_err is not None:
            raise last_err
        return set()

    return set()


def _loader_supports(lid, version, source):
    now = datetime.now().timestamp()
    key = (lid, source)
    # 双检锁：先无锁读缓存（dict 单操作在 GIL 下原子），未命中再加锁
    cached = _LOADER_CACHE.get(key)
    if cached and now - cached[0] < _LOADER_CACHE_TTL:
        return version in cached[1]
    with _LOADER_CACHE_LOCK:
        cached = _LOADER_CACHE.get(key)
        if cached and now - cached[0] < _LOADER_CACHE_TTL:
            return version in cached[1]
        versions = _fetch_loader_versions(lid, source)
        _LOADER_CACHE[key] = (now, versions)
        return version in versions


def total_memory_gb():
    """返回物理内存大小（GB，四舍五入）；无法获取时返回 0。"""
    try:
        import psutil
        return int(round(psutil.virtual_memory().total / (1024 ** 3)))
    except Exception:
        pass
    try:
        if sys.platform == "win32":
            import ctypes

            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_uint32),
                    ("dwMemoryLoad", ctypes.c_uint32),
                    ("ullTotalPhys", ctypes.c_uint64),
                    ("ullAvailPhys", ctypes.c_uint64),
                    ("ullTotalPageFile", ctypes.c_uint64),
                    ("ullAvailPageFile", ctypes.c_uint64),
                    ("ullTotalVirtual", ctypes.c_uint64),
                    ("ullAvailVirtual", ctypes.c_uint64),
                    ("ullAvailExtendedVirtual", ctypes.c_uint64),
                ]

            st = _MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return int(round(st.ullTotalPhys / (1024 ** 3)))
        else:
            return int(round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
                              / (1024 ** 3)))
    except Exception:
        pass
    return 0


def recommend_jvm_args(mode="default", gb=0):
    """按模式与物理内存生成 JVM 参数。"""
    gb = gb if gb > 0 else 8
    if mode == "eco":
        xmx = max(2, min(4, gb // 4))
        xms = max(1, xmx // 2)
        flags = "-XX:+UseG1GC -XX:MaxGCPauseMillis=200"
    elif mode == "perf":
        xmx = max(4, min(16, gb - 2))
        xms = max(2, xmx // 2)
        flags = ("-XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=50 "
                 "-XX:G1NewSizePercent=30 -XX:G1MaxNewSizePercent=40 -XX:G1HeapRegionSize=8M")
    else:
        xmx = max(2, min(8, gb // 2))
        xms = max(1, xmx // 2)
        flags = "-XX:+UseG1GC -XX:MaxGCPauseMillis=100"
    return f"-Xms{xms}G -Xmx{xmx}G {flags}"


# ---------- 高级参数：读取 / 作用域解析 ----------
def load_advanced():
    """返回补齐默认值后的全局高级参数。"""
    adv = _adv_clone(ADV_DEFAULTS)
    cur = load_config().get("advanced") or {}
    if isinstance(cur, dict):
        for k, v in cur.items():
            if k not in adv:
                # 兼容旧结构中的独立等待开关
                if k == "pre_launch_wait":
                    adv["pre_launch_wait"] = v
                continue
            if isinstance(adv[k], dict) and isinstance(v, dict):
                adv[k].update(v)
            else:
                adv[k] = v
    return adv


def load_version_override(version):
    """返回某版本的独立设置 {group: {state, value}}。"""
    data = (load_config().get("version_overrides") or {}).get(str(version or ""))
    return data if isinstance(data, dict) else {}


def resolve_advanced(version=""):
    """解析某版本最终生效的高级参数；None 表示该项被明确禁用。"""
    adv = load_advanced()
    ov = load_version_override(version) if version else {}
    out = {}
    for k in ADV_GROUPS:
        item = ov.get(k) if isinstance(ov.get(k), dict) else {}
        st = item.get("state") or ADV_STATE_FOLLOW
        if st == ADV_STATE_CUSTOM:
            val = item.get("value")
            out[k] = val if val is not None else adv.get(k)
        elif st == ADV_STATE_OFF:
            out[k] = None
        else:
            out[k] = adv.get(k)
    # 兼容旧结构：pre_launch 为字符串、wait 单独存放
    pre = out.get("pre_launch")
    if isinstance(pre, str):
        out["pre_launch"] = {"text": pre,
                             "wait": bool(adv.get("pre_launch_wait"))}
    return out


def _mb_from_text(s, default=0):
    """把 4G / 4096M / 4096 解析为 MB。"""
    t = str(s or "").strip().upper()
    if not t:
        return default
    m = re.match(r"^(\d+(?:\.\d+)?)\s*([GM]?)$", t)
    if not m:
        return default
    v = float(m.group(1))
    unit = m.group(2)
    if unit == "G":
        return int(v * 1024)
    if unit == "M":
        return int(v)
    return int(v)


def _fmt_mb(mb):
    return "%dG" % round(mb / 1024.0) if mb >= 1024 else "%dM" % mb


_JAVA_32BIT_CACHE = {}


def java_is_32bit(path):
    """检测 Java 是否为 32 位；无法判断时按 64 位处理。"""
    if not path or not os.path.isfile(path):
        return False
    try:
        key = (path, os.path.getmtime(path))
    except Exception:
        key = (path, 0)
    if key in _JAVA_32BIT_CACHE:
        return _JAVA_32BIT_CACHE[key]
    res = False
    try:
        cf = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        p = subprocess.run([path, "-XshowSettings:properties", "-version"],
                           capture_output=True, text=True, errors="replace",
                           timeout=8, creationflags=cf)
        txt = (p.stderr or "") + (p.stdout or "")
        res = bool(re.search(r"sun\.arch\.data\.model\s*=\s*32", txt))
    except Exception:
        pass
    _JAVA_32BIT_CACHE[key] = res
    return res


def count_instance_mods(mc_dir, version):
    try:
        d = instance_mods_dir(mc_dir, version)
        return len([f for f in os.listdir(d) if f.lower().endswith(".jar")])
    except Exception:
        return 0


def compute_auto_memory(version, mc_dir, java_path=""):
    """自动内存建议：原版与模组/整合包走不同计算路径。"""
    total_mb = total_memory_gb() * 1024 or 8192
    try:
        _mcver, loader, _lver = detect_instance_loader(mc_dir, version)
    except Exception:
        loader = ""
    mods = count_instance_mods(mc_dir, version)
    modded = bool(loader) or mods > 0
    if modded:
        # 模组/整合包：基线 4G，按模组数增加，且不超过物理内存的 60%
        xmx = 4096 + min(mods, 200) * 32
        xmx = min(xmx, int(total_mb * 0.6), 12288)
        xmx = max(xmx, 2048)
    else:
        # 原版：约占物理内存 1/4，上限 4G
        xmx = max(2048, min(int(total_mb * 0.25), 4096))
    xms = max(1024, xmx // 2)
    if java_is_32bit(java_path):
        # 32 位 Java 寻址上限约 1.5G，强制收敛到 1G 以内
        xmx = min(xmx, 1024)
        xms = min(xms, 512)
    return {"xms": xms, "xmx": xmx, "modded": modded, "mods": mods}


def auto_gc_mode(java_major, xmx_mb):
    """Java 17+ 且堆充足时推荐 ZGC（低停顿），否则 G1GC。"""
    if (java_major or 0) >= 17 and (xmx_mb or 0) >= 4096:
        return "zgc"
    return "g1"


def gc_java_requirement(mode):
    """返回某 GC 所需的最低 Java 大版本。"""
    return {"g1": 8, "zgc": 15}.get(mode, 8)


def split_args(text):
    """安全拆分命令行片段（引号不匹配时回退空格分割）。"""
    t = str(text or "").strip()
    if not t:
        return []
    try:
        return shlex.split(t, posix=(sys.platform != "win32"))
    except ValueError:
        return t.split()


def version_main_class(mc_dir, version):
    """读取版本主类；找不到时沿 inheritsFrom 向上查找。"""
    seen = set()
    name = str(version or "")
    while name and name not in seen:
        seen.add(name)
        jf = os.path.join(mc_dir, "versions", name, name + ".json")
        if not os.path.isfile(jf):
            return ""
        try:
            with open(jf, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return ""
        if data.get("mainClass"):
            return str(data["mainClass"])
        name = str(data.get("inheritsFrom") or "")
    return ""


def build_advanced_plan(version, mc_dir, java_path=""):
    """按作用域解析出最终的启动计划（JVM 段 / 游戏段 / 前置命令等）。"""
    cfg = load_config()
    if not cfg.get("advanced_enabled"):
        # 未启用高级参数：完全沿用旧逻辑，保证行为与修改前一致
        return {"jvm": split_args(cfg.get("jvm_args", "")),
                "game": split_args(cfg.get("extra_game_args", "")),
                "pre_launch": "", "pre_launch_wait": False,
                "priority": "normal", "gpu": "off",
                "warnings": [], "legacy": True}
    adv = resolve_advanced(version)
    jvm, game, warns = [], [], []
    java_major = java_major_from_path(java_path) if java_path else None
    auto_mem = None
    # ---- 内存 ----
    mem = adv.get("memory")
    xmx_mb = 0
    if isinstance(mem, dict):
        if mem.get("mode") == "custom":
            xms_mb = _mb_from_text(mem.get("xms"))
            xmx_mb = _mb_from_text(mem.get("xmx"))
        else:
            auto_mem = compute_auto_memory(version, mc_dir, java_path)
            xms_mb, xmx_mb = auto_mem["xms"], auto_mem["xmx"]
        if xmx_mb > 0:
            jvm.append("-Xmx%dM" % xmx_mb)
        if xms_mb > 0:
            jvm.append("-Xms%dM" % xms_mb)
    # ---- 垃圾回收器 ----
    gc = adv.get("gc")
    if isinstance(gc, dict):
        mode = gc.get("mode") or "auto"
        if mode == "auto":
            if auto_mem is None:
                auto_mem = compute_auto_memory(version, mc_dir, java_path)
            mode = auto_gc_mode(java_major, xmx_mb or auto_mem["xmx"])
        if mode in ("g1", "zgc"):
            need = gc_java_requirement(mode)
            if java_major and java_major < need:
                warns.append(_t("%s 需要 Java %d 及以上，当前版本为 Java %d，"
                                "该 GC 参数可能无法生效。")
                             % (mode.upper(), need, java_major))
            jvm.append("-XX:+UseZGC" if mode == "zgc" else "-XX:+UseG1GC")
        tuning = str(gc.get("tuning") or "").strip()
        if tuning:
            jvm.extend(split_args(tuning))
    # ---- 用户 JVM / 游戏参数（严格分区）----
    jvm.extend(split_args(adv.get("jvm_args")))
    game.extend(split_args(adv.get("game_args")))
    win = adv.get("window") if isinstance(adv.get("window"), dict) else {}
    for key in ("width", "height"):
        val = str((win or {}).get(key) or "").strip()
        if val.isdigit() and int(val) > 0:
            game.extend(["--%s" % key, val])
    pre = adv.get("pre_launch")
    wait = False
    if isinstance(pre, dict):
        wait = bool(pre.get("wait"))
        pre = pre.get("text")
    return {"jvm": jvm, "game": game,
            "pre_launch": str(pre or "").strip(),
            "pre_launch_wait": wait,
            "priority": adv.get("priority") or "normal",
            "gpu": adv.get("gpu") or "off",
            "warnings": warns, "legacy": False}


def command_preview(version, mc_dir, java_path=""):
    """生成命令行预览的三段内容：JVM / 启动器固定 / 游戏参数。"""
    plan = build_advanced_plan(version, mc_dir, java_path)
    main_cls = version_main_class(mc_dir, version) or "<主类>"
    return {
        "jvm": " ".join(plan["jvm"]),
        "fixed": "-cp <游戏库与版本文件> " + main_cls,
        "game": " ".join(plan["game"]),
        "warnings": plan["warnings"],
    }


def apply_gpu_preference(java_exe):
    """把 Java 可执行文件标记为“高性能显卡”偏好（仅 Windows 可自动实现）。"""
    if sys.platform != "win32":
        return False, _t("当前平台不支持自动设置显卡偏好，"
                         "请在系统显卡设置中手动指定 Java 使用独立显卡。")
    if not java_exe or not os.path.isfile(java_exe):
        return False, _t("尚未找到 Java 可执行文件。")
    try:
        import winreg
        key_path = r"Software\Microsoft\DirectX\UserGpuPreferences"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as k:
            winreg.SetValueEx(k, java_exe, 0, winreg.REG_SZ, "GpuPreference=2;")
        return True, java_exe
    except Exception as e:
        return False, str(e)


def find_terracotta():
    """返回已安装的 Terracotta 可执行文件路径；未安装返回空串。"""
    if not os.path.isdir(TERRA_DIR):
        return ""
    for name in sorted(os.listdir(TERRA_DIR)):
        low = name.lower()
        if not low.startswith("terracotta"):
            continue
        path = os.path.join(TERRA_DIR, name)
        if not os.path.isfile(path):
            continue
        if sys.platform == "win32":
            if low.endswith(".exe"):
                return path
        elif os.access(path, os.X_OK):
            return path
    return ""


def normalize_room_code(text):
    """整理成 Terracotta 认可的 U/XXXX-XXXX-XXXX-XXXX（含 34 进制校验）。"""
    s = re.sub(r"\s+", "", (text or "")).upper()
    if s.startswith("U/"):
        s = s[2:]
    # 官方解析会把易混字符 I/O 分别当作 1/0
    s = s.replace("-", "").replace("I", "1").replace("O", "0")
    if len(s) != 16 or any(c not in _ROOM_CHARS for c in s):
        return ""
    value = 0
    for c in s:
        value = value * 34 + _ROOM_CHARS.index(c)
    if value % 7:        # 房间码带「7 的倍数」校验
        return ""
    return "U/" + "-".join(s[i:i + 4] for i in range(0, 16, 4))


def _stellar_get(path, token=None, **kwargs):
    import requests
    get = _orig_requests_get or requests.get
    headers = dict(kwargs.pop("headers", {}) or {})
    if token:
        headers["Authorization"] = token
    return get(STELLAR_API + path, headers=headers, **kwargs)


def _stellar_post(path, token=None, **kwargs):
    import requests
    headers = dict(kwargs.pop("headers", {}) or {})
    if token:
        headers["Authorization"] = token
    return requests.post(STELLAR_API + path, headers=headers, **kwargs)


def _fmt_traffic(n):
    """把字节数格式化成人类可读的流量字符串。"""
    try:
        n = float(n or 0)
    except Exception:
        return "0 B"
    units = [("TB", 1024 ** 4), ("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)]
    for label, size in units:
        if n >= size:
            return "%.2f %s" % (n / size, label)
    return "%.0f B" % n


def _stellar_frpc_exe():
    """返回已安装的 StellarFrpc 可执行文件路径；未安装返回空串。"""
    name = "StellarFrpc.exe" if sys.platform == "win32" else "StellarFrpc"
    p = os.path.join(STELLAR_FRPC_DIR, name)
    return p if os.path.isfile(p) else ""


def _stellar_pick_frpc_file(names):
    """从 Alist 文件列表中挑出当前系统/架构对应的 StellarFrpc 压缩包文件名。"""
    # 只取 StellarFrpc 客户端（排除 StellarLauncher 等其它文件）
    names = [n for n in names if n.lower().startswith("stellarfrpc")]
    plat = "windows" if sys.platform == "win32" else (
        "darwin" if sys.platform == "darwin" else "linux")
    import platform
    arch = platform.machine().lower()
    if arch in ("amd64", "x86_64", "x64"):
        arch = "amd64"
    elif arch in ("arm64", "aarch64"):
        arch = "arm64"
    elif arch in ("i386", "i686", "x86"):
        arch = "386"
    # 优先精确匹配平台+架构，其次只匹配平台
    exact = [n for n in names if plat in n and arch in n]
    if exact:
        return exact[0]
    plat_only = [n for n in names if plat in n]
    if plat_only:
        return plat_only[0]
    return names[0] if names else ""


def _stellar_pick_bin_from_archive(zip_path):
    """从下载的压缩包里解出 StellarFrpc 可执行文件到 STELLAR_FRPC_DIR，返回路径。"""
    import zipfile, tarfile
    os.makedirs(STELLAR_FRPC_DIR, exist_ok=True)
    target_name = "StellarFrpc.exe" if sys.platform == "win32" else "StellarFrpc"
    if zip_path.endswith(".zip"):
        with zipfile.ZipFile(zip_path) as z:
            for info in z.infolist():
                base = os.path.basename(info.filename)
                if base.lower() == target_name.lower() or base.lower().startswith("stellarfrpc"):
                    z.extract(info, STELLAR_FRPC_DIR)
                    extracted = os.path.join(STELLAR_FRPC_DIR, info.filename)
                    final = os.path.join(STELLAR_FRPC_DIR, target_name)
                    if extracted != final:
                        os.replace(extracted, final)
                    if sys.platform != "win32":
                        os.chmod(final, 0o755)
                    return final
    elif zip_path.endswith(".tar.gz") or zip_path.endswith(".tgz"):
        with tarfile.open(zip_path, "r:gz") as t:
            for m in t.getmembers():
                base = os.path.basename(m.name)
                if base.lower() == target_name.lower() or base.lower().startswith("stellarfrpc"):
                    t.extract(m, STELLAR_FRPC_DIR)
                    extracted = os.path.join(STELLAR_FRPC_DIR, m.name)
                    final = os.path.join(STELLAR_FRPC_DIR, target_name)
                    if extracted != final:
                        os.replace(extracted, final)
                    if sys.platform != "win32":
                        os.chmod(final, 0o755)
                    return final
    return ""


def animations_enabled():
    try:
        return bool(load_config().get("enable_animations", True))
    except Exception:
        return True
# ============ 线程兼容层（替代 QThread / pyqtSignal） ============
# 界面是单线程 tkinter：后台线程只把回调塞进队列，由主线程定时泵出执行。
_UI_QUEUE = queue.Queue()
_UI_ROOT = None


def _ui_call(fn, *args):
    _UI_QUEUE.put((fn, args))


def _pump_ui():
    while True:
        try:
            fn, args = _UI_QUEUE.get_nowait()
        except queue.Empty:
            break
        try:
            fn(*args)
        except Exception:
            traceback.print_exc()
    try:
        if _UI_ROOT is not None:
            _UI_ROOT.after(15, _pump_ui)
    except Exception:
        pass


class _Signal:
    """极简信号：connect 收集槽，emit 把调用投递到主线程。"""

    def __init__(self, *types):
        self._slots = []

    def connect(self, fn):
        self._slots.append(fn)
        return fn

    def disconnect(self, fn=None):
        if fn is None:
            self._slots.clear()
        else:
            try:
                self._slots.remove(fn)
            except ValueError:
                pass

    def emit(self, *args):
        for fn in list(self._slots):
            _ui_call(fn, *args)


def pyqtSignal(*types):
    return _Signal()


class QThread(threading.Thread):
    """把 threading.Thread 包装成 Qt 风格，供原线程类原样复用。"""

    def __init__(self, parent=None):
        super().__init__(daemon=True)
        self._parent = parent
        seen = set()
        for cls in type(self).__mro__:
            for k, v in vars(cls).items():
                if isinstance(v, _Signal) and k not in seen:
                    seen.add(k)
                    self.__dict__[k] = _Signal()
        self.finished = _Signal()

    def start(self):
        orig = self.run

        def _wrapped():
            try:
                orig()
            finally:
                _ui_call(self.finished.emit)

        self.run = _wrapped
        super().start()

    def isRunning(self):
        return self.is_alive()

    def wait(self, ms=None):
        if ms is None:
            self.join()
            return True
        self.join(ms / 1000.0)
        return not self.is_alive()
class JavaScanThread(QThread):
    done = pyqtSignal(list)

    def run(self):
        out = []
        try:
            for exe in find_java_candidates():
                out.append((exe, java_major_from_path(exe)))
        except Exception:
            pass
        self.done.emit(out)


class JavaInstallThread(QThread):
    """用 minecraft-launcher-lib 的 runtime 模块在启动器内下载并安装 Java。"""
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, version, need, mc_dir, parent=None):
        super().__init__(parent)
        self._version = version
        self._need = need
        self._mc_dir = mc_dir
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        global _JAVA_SOURCE_OVERRIDE
        prev_override = _JAVA_SOURCE_OVERRIDE
        comp = _java_runtime_component_for_version(self._version, self._need, self._mc_dir)
        if not comp:
            self.failed.emit(_t("当前系统没有可下载的 Java ") + str(self._need))
            return
        try:
            import minecraft_launcher_lib.runtime as rt
        except Exception as e:
            self.failed.emit(_err_text(e))
            return

        def _status(msg):
            if self._cancel:
                raise RuntimeError("__cancelled__")
            self.status.emit(str(msg))

        state = {"max": 1}

        def _setmax(m):
            try:
                state["max"] = max(1, int(m))
            except (TypeError, ValueError):
                state["max"] = 1

        def _progress(v):
            if self._cancel:
                raise RuntimeError("__cancelled__")
            try:
                pct = int(int(v) * 100 / state["max"])
            except (TypeError, ValueError):
                return
            self.progress.emit(max(0, min(100, pct)))

        cb = {"setStatus": _status, "setMax": _setmax, "setProgress": _progress}
        # Java 运行时固定走官方源，不走 BMCL 镜像
        try:
            if self._cancel:
                self.failed.emit(_t("已取消下载"))
                return
            reset_download_cancel()
            _JAVA_SOURCE_OVERRIDE = "official"
            self.status.emit(_t("正在安装 Java ") + str(self._need) + " ...")
            rt.install_jvm_runtime(comp, self._mc_dir, cb)
            exe = rt.get_executable_path(comp, self._mc_dir)
            if exe and os.path.isfile(exe):
                self.done.emit(exe)
                return
            self.failed.emit(_t("Java 安装完成，但未找到可执行文件。"))
        except Exception as e:
            if self._cancel:
                self.failed.emit(_t("已取消下载"))
            elif isinstance(e, str):
                self.failed.emit(e)
            else:
                self.failed.emit(_err_text(e))
        finally:
            _JAVA_SOURCE_OVERRIDE = prev_override


class MicrosoftLoginThread(QThread):
    code_ready = pyqtSignal(str, str)   # (verification_uri, user_code)
    success = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, client_id):
        super().__init__()
        self.client_id = client_id
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    _DEVICE_CODE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
    _TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
    _SCOPE = "XboxLive.signin offline_access"

    def _sleep_checked(self, seconds):
        """分片 sleep，取消时能尽快返回。"""
        end = time.time() + seconds
        while time.time() < end:
            if self._cancelled:
                return False
            time.sleep(0.2)
        return True

    def _device_code_login(self, ma):
        """微软设备码登录：不依赖回调地址，公共 Client ID 也能直接使用。

        返回 True 表示已经处理（成功或失败）；False 表示该 Client ID
        不支持设备码，交给浏览器回调流程。
        """
        import requests
        try:
            r = requests.post(self._DEVICE_CODE_URL, data={
                "client_id": self.client_id,
                "scope": self._SCOPE,
            }, timeout=20)
            info = r.json()
        except Exception as e:
            self.failed.emit(_t("设备码登录失败：%s") % e)
            return True
        if not info.get("device_code"):
            return False
        self.code_ready.emit(
            info.get("verification_uri") or info.get("verification_url", ""),
            info.get("user_code", ""))
        interval = max(1, int(info.get("interval", 5)))
        deadline = time.time() + int(info.get("expires_in", 900))
        token_data = None
        while time.time() < deadline:
            if not self._sleep_checked(interval):
                return True
            try:
                tr = requests.post(self._TOKEN_URL, data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": self.client_id,
                    "device_code": info["device_code"],
                }, timeout=20)
                td = tr.json()
            except Exception:
                continue
            err = td.get("error")
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval += 2
                continue
            if err:
                self.failed.emit(
                    _t("设备码登录失败：%s") % (td.get("error_description") or err))
                return True
            token_data = td
            break
        if not token_data or not token_data.get("access_token"):
            self.failed.emit(_t("设备码登录超时，请重试。"))
            return True
        try:
            xbl = ma.authenticate_with_xbl(token_data["access_token"])
            userhash = xbl["DisplayClaims"]["xui"][0]["uhs"]
            xsts = ma.authenticate_with_xsts(xbl["Token"])
            mc = ma.authenticate_with_minecraft(userhash, xsts["Token"])
            if "access_token" not in mc:
                self.failed.emit(_t("该 Microsoft 账户未拥有 Minecraft，或应用权限不足。"))
                return True
            profile = ma.get_profile(mc["access_token"])
            if profile.get("error") == "NOT_FOUND":
                self.failed.emit(_t("该 Microsoft 账户未购买 Minecraft。"))
                return True
        except Exception as e:
            if not self._cancelled:
                self.failed.emit(_t("正版验证失败：%s") % e)
            return True
        if self._cancelled:
            return True
        result = {
            "name": profile.get("name", ""),
            "type": "microsoft",
            "uuid": profile.get("id", ""),
            "token": mc["access_token"],
            "refresh_token": token_data.get("refresh_token", ""),
        }
        if not result["name"] or not result["token"]:
            self.failed.emit(_t("登录返回数据不完整"))
            return True
        self.success.emit(result)
        return True

    def run(self):
        try:
            from minecraft_launcher_lib import microsoft_account
        except ImportError as e:
            self.failed.emit(_t("缺少依赖：%s") % e)
            return
        # 优先走设备码流程：无需回调地址，公共 Client ID 也能直接登录
        if self._device_code_login(microsoft_account):
            return
        # 回退：该 Client ID 不支持设备码时用浏览器回调流程
        if hasattr(microsoft_account, "get_login_url"):
            try:
                redirect_uri = "http://localhost:8080"
                state, verifier = "", ""
                if hasattr(microsoft_account, "get_secure_login_data"):
                    # 带 state + PKCE，避免本地回调被伪造授权码
                    state = microsoft_account.generate_state()
                    url, state, verifier = microsoft_account.get_secure_login_data(
                        self.client_id, redirect_uri, state)
                else:
                    url = microsoft_account.get_login_url(self.client_id, redirect_uri)
                self.code_ready.emit(url, "")
                # 先确保本地回调服务器能启动，再打开浏览器，避免端口占用时白开浏览器
                from http.server import HTTPServer, BaseHTTPRequestHandler
                import urllib.parse as up
                code_holder = {"code": None}

                class Handler(BaseHTTPRequestHandler):
                    def do_GET(h):
                        qs = up.urlparse(h.path).query
                        params = up.parse_qs(qs)
                        got_state = params.get("state", [""])[0]
                        if "code" in params and (not state or got_state == state):
                            code_holder["code"] = params["code"][0]
                            body = b"Login OK. You can close this window."
                            h.send_response(200)
                            h.send_header("Content-Length", str(len(body)))
                            h.end_headers()
                            h.wfile.write(body)
                        else:
                            h.send_response(400)
                            h.send_header("Content-Length", "0")
                            h.end_headers()
                    def log_message(h, *a): pass

                srv = HTTPServer(("localhost", 8080), Handler)
                # 服务器已就绪，再打开浏览器
                webbrowser.open(url)
                # timeout 必须短，否则取消操作会被阻塞很久
                srv.timeout = 1
                for _ in range(300):
                    if self._cancelled:
                        srv.server_close()
                        return
                    srv.handle_request()
                    if code_holder["code"]:
                        break
                srv.server_close()
                if not code_holder["code"]:
                    self.failed.emit(_t("未获取到授权码（超时或用户取消）"))
                    return
                try:
                    acc = microsoft_account.complete_login(
                        self.client_id, None, redirect_uri, code_holder["code"],
                        verifier or None)
                except TypeError:
                    # 兼容不支持 code_verifier 参数的旧版本
                    acc = microsoft_account.complete_login(
                        self.client_id, None, redirect_uri, code_holder["code"])
                if self._cancelled:
                    return
                result = {
                    "name": acc.get("name", ""),
                    "type": "microsoft",
                    "uuid": acc.get("id", ""),
                    "token": acc.get("access_token") or acc.get("accessToken", ""),
                    "refresh_token": acc.get("refresh_token") or acc.get("refreshToken", ""),
                }
                if not result["name"] or not result["token"]:
                    self.failed.emit(_t("登录返回数据不完整"))
                    return
                self.success.emit(result)
            except Exception as e:
                if not self._cancelled:
                    self.failed.emit(_t("浏览器登录失败：%s") % e)
            return
        self.failed.emit(_t(
            "当前 minecraft-launcher-lib 版本不支持微软登录，\n"
            "请升级：pip install -U minecraft-launcher-lib"))


class LaunchThread(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(str)             # 字符进度条文本（原地刷新）
    pid_signal = pyqtSignal(int)                  # 进程创建成功后上报 PID
    finished_signal = pyqtSignal(int)
    error_signal = pyqtSignal(str)
    token_refreshed = pyqtSignal(str, str, str)   # (账号名, access_token, refresh_token)
    def __init__(self, version, mc_dir, options, need_install=False,
                 account=None, client_id=""):
        super().__init__()
        self.version, self.mc_dir, self.options = version, mc_dir, options
        self.need_install = need_install
        self.account = account or {}
        self.client_id = client_id
        self.proc = None
        self._cancel_requested = False
    def run(self):
        try:
            # 上一次下载被取消会留下全局取消标记，不清掉这里每次请求都会直接报错
            reset_download_cancel()
            try:
                import minecraft_launcher_lib
            except ImportError as e:
                self.error_signal.emit(_t("缺少依赖：%s") % e)
                self.finished_signal.emit(-1); return
            # 版本 JSON 损坏时补全与启动都不可行，先明确拦截并提示重装
            issue = version_json_issue(self.mc_dir, self.version)
            if issue:
                self.error_signal.emit(_t(
                    "版本文件损坏，无法补全或启动：\n%s\n"
                    "请在「下载游戏」中重新安装该版本。") % issue)
                self.finished_signal.emit(-1); return
            # 正版 access_token 有效期约 24 小时，启动前用 refresh_token 续期
            if (self.account.get("type") == "microsoft"
                    and self.account.get("refresh_token") and self.client_id):
                try:
                    data = minecraft_launcher_lib.microsoft_account.complete_refresh(
                        self.client_id, None, None, self.account["refresh_token"])
                    tok = data.get("access_token") or data.get("accessToken", "")
                    rtok = (data.get("refresh_token") or data.get("refreshToken", "")
                            or self.account["refresh_token"])
                    if tok:
                        self.options["token"] = tok
                        self.account["token"] = tok
                        self.account["refresh_token"] = rtok
                        self.token_refreshed.emit(
                            self.account.get("name", ""), tok, rtok)
                except Exception as e:
                    self.log_signal.emit(
                        _t("登录令牌刷新失败，将使用已保存的令牌：%s") % e)
            if self.need_install:
                self.log_signal.emit(_t("正在补全版本文件 ..."))
                try:
                    ensure_libraries_dir(self.mc_dir)
                    minecraft_launcher_lib.install.install_minecraft_version(
                        self.version, self.mc_dir, callback=self._mk_repair_cb())
                    self.log_signal.emit(_t("版本文件补全完成。"))
                except Exception as e:
                    if self._cancel_requested:
                        self.log_signal.emit(_t("已取消启动。"))
                    else:
                        self.error_signal.emit(_t("版本补全失败：%s") % e)
                    self.finished_signal.emit(-1); return
            else:
                # 库文件被误删或下载中断时，Java 会报「找不到主类」，
                # 这里先做完整性检查，发现缺失就自动补全
                try:
                    missing = missing_version_libraries(self.mc_dir, self.version)
                except Exception:
                    missing = []
                if missing:
                    self.log_signal.emit(
                        _t("检测到 %d 个库文件缺失（如 %s），正在自动补全 ...")
                        % (len(missing), missing[0]))
                    if not self._repair_libraries(minecraft_launcher_lib):
                        # 补全失败（网络断开或镜像源都不可用）时不再继续启动，
                        # 否则 Java 只会抛一串晦涩的 ClassNotFound，用户难以定位原因
                        if self._cancel_requested:
                            self.log_signal.emit(_t("已取消启动。"))
                        else:
                            self.error_signal.emit(_t(
                                "库文件补全失败，请检查网络连接后重试，"
                                "或在「下载游戏」中重新安装该版本。"))
                        self.finished_signal.emit(-1); return
            opts_clean = {k: v for k, v in self.options.items() if not k.startswith("_")}
            # 个别情况下版本 JSON 的 mainClass 会被写坏（如 Fabric 类名中间多出乱码），
            # Java 会原样使用，导致「找不到或无法加载主类」，这里启动前自动纠正
            try:
                for fixed_name in repair_loader_main_class(self.mc_dir, self.version):
                    self.log_signal.emit(
                        _t("已修复版本「%s」的加载器主类。") % fixed_name)
            except Exception as e:
                self.log_signal.emit(_t("检查加载器主类失败：%s") % e)
            # 核心加载器库补不回来时直接给出可操作提示，不再让 Java 抛晦涩的 ClassNotFound
            try:
                core_missing = missing_loader_library(self.mc_dir, self.version)
            except Exception:
                core_missing = []
            if core_missing:
                self.error_signal.emit(
                    _t("加载器核心库缺失：%s\n"
                       "自动补全未能修复，请检查网络后在「下载游戏」中用同一加载器版本重新安装一次。")
                    % core_missing[0])
                self.finished_signal.emit(-1); return
            # 启动前命令：默认后台执行不阻塞；勾选等待时先执行完再继续
            self._run_pre_launch()
            # 进程尚未创建时按取消，也能立即中止启动
            if self._cancel_requested:
                self.log_signal.emit(_t("已取消启动。"))
                self.finished_signal.emit(-1); return
            cmd = minecraft_launcher_lib.command.get_minecraft_command(
                self.version, self.mc_dir, opts_clean)
            extra = self.options.get("_extra_game_args")
            if extra:
                cmd.extend(extra)
            cf = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            if sys.platform == "win32" and self.options.get("_priority") == "high":
                cf |= 0x00000080  # HIGH_PRIORITY_CLASS
                self.log_signal.emit(_t("已按高优先级启动游戏进程。"))
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, cwd=self.mc_dir,
                                         creationflags=cf)
            self.pid_signal.emit(self.proc.pid)
            for raw in iter(self.proc.stdout.readline, b""):
                try:
                    line = raw.decode("utf-8")
                except UnicodeDecodeError:
                    line = raw.decode("gbk", errors="replace")
                self.log_signal.emit(line.rstrip("\r\n"))
            self.proc.wait()
            self.finished_signal.emit(self.proc.returncode)
        except Exception as e:
            self.error_signal.emit(str(e))
            self.finished_signal.emit(-1)
    def _run_pre_launch(self):
        """执行启动前命令；等待模式下会阻塞到命令结束。"""
        cmd = str(self.options.get("_pre_launch") or "").strip()
        if not cmd:
            return
        self.log_signal.emit(_t("执行启动前命令：") + cmd)
        cf = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            if self.options.get("_pre_launch_wait"):
                p = subprocess.Popen(cmd, shell=True, cwd=self.mc_dir,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT,
                                     creationflags=cf)
                for raw in iter(p.stdout.readline, b""):
                    try:
                        line = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        line = raw.decode("gbk", errors="replace")
                    self.log_signal.emit(_t("[前置] ") + line.rstrip("\r\n"))
                p.wait()
                self.log_signal.emit(_t("启动前命令已结束（返回码 %s）。")
                                     % p.returncode)
            else:
                subprocess.Popen(cmd, shell=True, cwd=self.mc_dir,
                                 creationflags=cf)
                self.log_signal.emit(_t("启动前命令已在后台启动，不等待其结束。"))
        except Exception as e:
            self.log_signal.emit(_t("启动前命令执行失败：%s") % e)

    def _mk_repair_cb(self):
        """mll 下载回调：进度转成字符进度条发到输出区，并响应取消。"""
        state = {"max": 1, "step": ""}
        def set_max(v):
            try:
                state["max"] = max(int(v), 1)
            except Exception:
                pass
        def set_status(t):
            if self._cancel_requested:
                raise RuntimeError("用户取消启动")
            state["step"] = _dl_step_text(t)
        def set_progress(v):
            if self._cancel_requested:
                raise RuntimeError("用户取消启动")
            try:
                pct = int(100.0 * int(v) / max(1, state["max"]))
            except Exception:
                return
            bar = "■" * (pct // 10) + "□" * (10 - pct // 10)
            step = (" " + state["step"]) if state["step"] else ""
            self.progress_signal.emit("[%s] %3d%%%s" % (bar, pct, step))
        return {"setStatus": set_status, "setMax": set_max,
                "setProgress": set_progress}
    def _repair_libraries(self, mll):
        """重新校验并下载缺失的库；镜像源失败时自动切官方源再试一次。"""
        prev = load_config().get("download_source", "mirror")
        sources = [prev]
        if prev == "mirror":
            sources.append("official")
        try:
            for i, src in enumerate(sources):
                if self._cancel_requested:
                    return False
                try:
                    reset_download_cancel()
                    set_download_source(src)
                    if i:
                        self.log_signal.emit(
                            _t("镜像源下载失败，正在改用官方源重试 ..."))
                    ensure_libraries_dir(self.mc_dir)
                    mll.install.install_minecraft_version(
                        self.version, self.mc_dir, callback=self._mk_repair_cb())
                    still = missing_version_libraries(self.mc_dir, self.version)
                    if not still:
                        self.log_signal.emit(_t("库文件补全完成。"))
                        return True
                    self.log_signal.emit(
                        _t("仍有 %d 个库文件缺失（如 %s）。") % (len(still), still[0]))
                except Exception as e:
                    if self._cancel_requested:
                        return False
                    self.log_signal.emit(_t("库文件补全失败（%s）：%s") % (src, e))
        finally:
            set_download_source(prev)
        return False
    def cancel(self):
        """中止启动：进程还没起来或启动阶段用强制结束。"""
        self._cancel_requested = True
        if self.proc and self.proc.poll() is None:
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    self.proc.terminate()
            except Exception:
                pass
        else:
            # 启动阶段可能正在补全版本文件：中断阻塞中的下载请求，避免取消卡住
            interrupt_active_downloads()
    def request_close(self):
        """关闭已运行的游戏：只发送正常退出请求，不强制杀进程。"""
        if self.proc and self.proc.poll() is None:
            if graceful_close_process(self.proc.pid):
                self.log_signal.emit(_t("已请求游戏正常退出，等待游戏保存并关闭窗口 ..."))
            else:
                self.log_signal.emit(_t(
                    "未找到游戏窗口，无法发送正常关闭请求，请手动关闭游戏窗口。"))
        else:
            self._cancel_requested = True
            # 启动阶段可能正卡在下载阻塞里：切断网络请求，让线程立刻退出
            interrupt_active_downloads()


class VersionRepairThread(QThread):
    """补全选中版本缺失的游戏文件（库、jar、资源等），并上报进度。"""
    status_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    done_signal = pyqtSignal(bool, str)

    def __init__(self, version, mc_dir):
        super().__init__()
        self.version, self.mc_dir = version, mc_dir

    def run(self):
        reset_download_cancel()
        try:
            import minecraft_launcher_lib
        except ImportError as e:
            self.done_signal.emit(False, _t("缺少依赖：%s") % e)
            return
        # 进入下载前再校验一次，避免损坏的 JSON 让下载异常难以定位
        issue = version_json_issue(self.mc_dir, self.version)
        if issue:
            self.done_signal.emit(
                False, _t("版本 JSON 异常，无法补全：\n%s\n\n"
                          "请在「下载游戏」中重新安装该版本。") % issue)
            return
        prev = load_config().get("download_source", "mirror")
        sources = [prev]
        if prev == "mirror":
            sources.append("official")
        cur_max = [1]

        def make_cb():
            def set_status(t):
                self.status_signal.emit(str(t))
            def set_max(v):
                cur_max[0] = max(1, int(v))
            def set_progress(v):
                pct = int(int(v) / cur_max[0] * 100)
                self.progress_signal.emit(min(100, max(0, pct)))
            return {"setStatus": set_status,
                    "setMax": set_max, "setProgress": set_progress}

        last_err = _t("下载失败")
        try:
            for i, src in enumerate(sources):
                try:
                    reset_download_cancel()
                    set_download_source(src)
                    if i:
                        self.status_signal.emit(
                            _t("镜像源下载失败，正在改用官方源重试 ..."))
                    ensure_libraries_dir(self.mc_dir)
                    minecraft_launcher_lib.install.install_minecraft_version(
                        self.version, self.mc_dir, callback=make_cb())
                    still = missing_version_libraries(self.mc_dir, self.version)
                    if not still:
                        self.progress_signal.emit(100)
                        self.done_signal.emit(True, _t("版本文件补全完成。"))
                        return
                    last_err = _t("仍有 %d 个库文件缺失（如 %s）。") % (
                        len(still), still[0])
                except Exception as e:
                    last_err = str(e)
                self.progress_signal.emit(0)
        finally:
            set_download_source(prev)
        self.done_signal.emit(False, _t("补全失败：%s") % last_err)


class DownloadWorker(QThread):
    """下载队列执行器：直接操作共享任务列表，支持运行中追加、单任务取消、
    取消后清理下载到一半的版本文件夹。"""
    status = pyqtSignal(str); progress = pyqtSignal(int); step = pyqtSignal(str)
    finished_one = pyqtSignal(int, bool, str); task_started = pyqtSignal(int, str)
    def __init__(self, tasks):
        super().__init__()
        self.tasks = tasks              # 共享引用：GUI 线程可运行中追加
        self._cancel = False
        self._cancel_tids = set()
        self._cur_max = 1
    def is_cancelled(self): return self._cancel
    def cancel_task(self, tid):
        """取消单个任务；若它正在下载，立即打断阻塞中的网络请求。"""
        self._cancel_tids.add(tid)
        interrupt_active_downloads()
    @staticmethod
    def _version_dirs(mc_dir, task):
        vr = os.path.join(mc_dir, "versions")
        dirs = {os.path.join(vr, task["name"])}
        if task["version"] != task["name"]:
            dirs.add(os.path.join(vr, task["version"]))
        return dirs
    def _cleanup_partial(self, mc_dir, task):
        """删除本次下载产生的半成品版本文件夹；任务开始前就存在的目录不动。"""
        pre = task.get("_pre_dirs") or set()
        for d in self._version_dirs(mc_dir, task):
            try:
                if d not in pre and os.path.isdir(d):
                    shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass
    def run(self):
        reset_download_cancel()
        try:
            import minecraft_launcher_lib
        except ImportError as e:
            self.status.emit(_t("缺少依赖：%s") % e)
            self.progress.emit(100)
            return
        mc_dir = load_config()["minecraft_directory"]
        ensure_libraries_dir(mc_dir)
        i = 0
        while i < len(self.tasks):
            if self._cancel:
                break
            task = self.tasks[i]; i += 1
            tid = task.get("_tid", i)
            if task.get("_removed") or task["status"] in ("已完成", "已取消"):
                continue
            if tid in self._cancel_tids:
                task["status"] = "已取消"
                self.finished_one.emit(tid, False, task["name"])
                continue
            version, loader, name = task["version"], task.get("loader"), task["name"]
            task["_pre_dirs"] = {d for d in self._version_dirs(mc_dir, task)
                                 if os.path.isdir(d)}
            self._cur_max = 1
            self.task_started.emit(tid, name)
            self.status.emit(f"{_t('正在下载')} {name} ...")
            def _cancelled(tid=tid):
                return self._cancel or tid in self._cancel_tids
            def make_cb(_cancelled=_cancelled):
                def _check():
                    if _cancelled():
                        raise RuntimeError("用户取消下载")
                def set_progress(v):
                    _check()
                    total = max(1, sum(1 for t in self.tasks if not t.get("_removed")))
                    done = sum(1 for t in self.tasks
                               if not t.get("_removed")
                               and t["status"] in ("已完成", "已取消", "失败"))
                    ratio = v / max(self._cur_max, 1)
                    pct = int(((done + ratio) / total) * 100)
                    self.progress.emit(min(100, max(0, pct)))
                def set_max(v): self._cur_max = max(int(v), 1)
                def set_status(t):
                    _check()
                    self.step.emit(_dl_step_text(t))
                return {"setStatus": set_status,
                        "setMax": set_max, "setProgress": set_progress}
            cb = make_cb()
            prev_source = _DOWNLOAD_SOURCE
            ok, last_err, was_cancel = False, None, False
            for attempt in range(2):
                try:
                    minecraft_launcher_lib.install.install_minecraft_version(
                        version, mc_dir, callback=cb)
                    if loader:
                        self._install_loader(loader, version, mc_dir, name=name, callback=cb)
                    fa = task.get("fabric_api")
                    if fa and not _cancelled():
                        self._install_fabric_api(fa, mc_dir, name, _cancelled)
                    ok = True
                    break
                except Exception as e:
                    last_err = e
                    if _cancelled():
                        was_cancel = True
                        break
                    if attempt == 1 or prev_source != "mirror":
                        break
                    # 镜像节点抖动时改用官方源再试一次，避免整次下载白费
                    self.status.emit(_t("镜像源下载失败，正在改用官方源重试 ..."))
                    self.progress.emit(0)
                    reset_download_cancel()
                    set_download_source("official")
            set_download_source(prev_source)
            if was_cancel or _cancelled():
                self._cleanup_partial(mc_dir, task)
                task["status"] = "已取消"
                self.finished_one.emit(tid, False, name)
            else:
                self.finished_one.emit(tid, ok, name if ok else f"{name}: {last_err}")
        self.progress.emit(100)
        self.step.emit("")
        self.status.emit(_t("已取消") if self._cancel else _t("全部完成"))
    def _install_fabric_api(self, fa, mc_dir, name, cancelled):
        """把选定的 Fabric API 模组下载到该版本自己的 mods 文件夹。"""
        url, fname = fa.get("url"), fa.get("filename")
        if not url or not fname:
            return
        self.step.emit(_t("正在下载 Fabric API ..."))
        mods = version_mods_dir(mc_dir, name)
        os.makedirs(mods, exist_ok=True)
        dst = os.path.join(mods, fname)
        tmp = dst + ".part"
        import requests
        r = requests.get(url, stream=True, timeout=30,
                         headers={"User-Agent": "LCL-Launcher"})
        r.raise_for_status()
        try:
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(65536):
                    if cancelled():
                        raise RuntimeError("用户取消下载")
                    if chunk:
                        f.write(chunk)
        except Exception:
            try:
                os.remove(tmp)
            except Exception:
                pass
            raise
        os.replace(tmp, dst)
    def _install_loader(self, ln, version, mc_dir, name=None, callback=None):
        import shutil
        import minecraft_launcher_lib
        if not hasattr(minecraft_launcher_lib, "mod_loader"):
            raise RuntimeError("minecraft-launcher-lib 版本过低（需 8.0+），请先升级")
        jp, _ = resolve_java(version, load_config().get("java_path", ""))
        jp = jp or shutil.which("java")
        if not jp:
            raise RuntimeError(_t(
                "未检测到 Java，Forge/NeoForge 安装器需要 Java，请在设置中指定 Java 路径"))
        installed = minecraft_launcher_lib.mod_loader.get_mod_loader(ln).install(
            version, mc_dir, java=jp, callback=callback or {})
        installed = str(installed)
        if name and name != installed:
            ok, err = rename_version_dir(mc_dir, installed, name)
            if ok:
                self.status.emit(_t("已安装并重命名为 %s") % name)
                return name
            self.status.emit(_t("已安装为 %s，重命名为 %s 失败：%s")
                             % (installed, name, err))
        return installed
    def cancel(self):
        self._cancel = True
        # 关掉正在阻塞读取的下载响应，否则取消会一直卡在「正在取消」
        interrupt_active_downloads()


class FabricApiQueryWorker(QThread):
    """异步查询某游戏版本可用的 Fabric API 模组版本列表（Modrinth）。"""
    done = pyqtSignal(str, list)
    def __init__(self, game_version):
        super().__init__()
        self.game_version = game_version
    def run(self):
        out = []
        try:
            import requests, urllib.parse
            q = urllib.parse.urlencode({
                "game_versions": json.dumps([self.game_version]),
                "loaders": json.dumps(["fabric"]),
            })
            r = requests.get("https://api.modrinth.com/v2/project/fabric-api/version?" + q,
                             timeout=20, headers={"User-Agent": "LCL-Launcher"})
            if r.ok:
                for v in r.json():
                    files = v.get("files") or []
                    pri = [f for f in files if f.get("primary")] or files
                    if not pri:
                        continue
                    out.append({"version_number": v.get("version_number", ""),
                                "url": pri[0].get("url", ""),
                                "filename": pri[0].get("filename", "")})
        except Exception:
            pass
        self.done.emit(self.game_version, out)


class ModpackInstallThread(QThread):
    """在后台安装 Modrinth(.mrpack) 或 CurseForge(.zip) 整合包到独立实例目录。"""
    status = pyqtSignal(str)
    progress = pyqtSignal(int)
    done = pyqtSignal(bool, str, dict)

    def __init__(self, path, mc_dir, instance, kind="mrpack",
                 manifest=None, optional=None, cf_key=""):
        super().__init__()
        self.path, self.mc_dir, self.instance = path, mc_dir, instance
        self.kind, self.manifest = kind, manifest or {}
        self.optional = list(optional or [])
        self.cf_key = cf_key
        self._cancel = False
        self._cur_max = 1
        self._missing = []

    def is_cancelled(self):
        return self._cancel

    def cancel(self):
        self._cancel = True
        interrupt_active_downloads()

    def _cb(self):
        def _check():
            if self._cancel:
                raise RuntimeError(_t("已取消导入"))
        def set_status(t):
            _check(); self.status.emit(t)
        def set_max(v):
            self._cur_max = max(int(v), 1)
        def set_progress(v):
            _check()
            self.progress.emit(min(99, max(0, int(v / self._cur_max * 100))))
        return {"setStatus": set_status, "setMax": set_max, "setProgress": set_progress}

    def run(self):
        reset_download_cancel()
        ensure_libraries_dir(self.mc_dir)
        try:
            import minecraft_launcher_lib as ml
        except ImportError as e:
            self.done.emit(False, _t("缺少依赖：%s") % e, {})
            return
        target = os.path.join(self.mc_dir, "versions", self.instance)
        try:
            if self.kind == "mrpack":
                info = self._install_mrpack(ml, target)
            else:
                info = self._install_curseforge(ml, target)
        except Exception as e:
            self.done.emit(False, str(e), {})
            return
        self.progress.emit(100)
        info["missing"] = self._missing
        self.done.emit(True, "", info)

    # ---- Modrinth ----
    def _install_mrpack(self, ml, target):
        if not hasattr(ml, "mrpack"):
            raise RuntimeError("minecraft-launcher-lib 版本过低（需 8.0+），请先升级")
        self.status.emit(_t("正在安装整合包..."))
        launch_v = ml.mrpack.get_mrpack_launch_version(self.path)
        info = ml.mrpack.get_mrpack_information(self.path)
        ml.mrpack.install_mrpack(
            self.path, self.mc_dir, modpack_directory=target,
            callback=self._cb(),
            mrpack_install_options={"optionalFiles": self.optional})
        return {"launch_version": launch_v,
                "mc_version": info.get("minecraftVersion", ""),
                "instance_dir": target}

    # ---- CurseForge ----
    def _install_curseforge(self, ml, target):
        import shutil
        import zipfile
        import requests
        mf = self.manifest
        mc_meta = mf.get("minecraft") or {}
        mc_ver = mc_meta.get("version") or ""
        if not mc_ver:
            raise RuntimeError(_t("整合包解析失败") + "：manifest.json 缺少 minecraft.version")
        # 1) 原版游戏
        self.status.emit("%s Minecraft %s ..." % (_t("正在下载"), mc_ver))
        ml.install.install_minecraft_version(mc_ver, self.mc_dir, callback=self._cb())
        # 2) Mod 加载器
        loaders = mc_meta.get("modLoaders") or []
        primary = [x for x in loaders if x.get("primary")] or loaders
        loader_id = primary[0].get("id", "") if primary else ""
        launch_v = mc_ver
        if loader_id:
            kind, _, ver = loader_id.partition("-")
            # 个别整合包的加载器 id 会带 Minecraft 版本前缀（forge-1.12.2-14.23.5.2859）
            if ver.startswith(mc_ver + "-"):
                ver = ver[len(mc_ver) + 1:]
            if kind and ver:
                jp, _need = resolve_java(mc_ver, load_config().get("java_path", ""))
                jp = jp or shutil.which("java")
                if not jp:
                    raise RuntimeError(_t("未检测到 Java，整合包加载器安装需要 Java"))
                self.status.emit("%s %s ..." % (_t("正在安装"), loader_id))
                # 必须安装整合包指定的加载器版本，不能装最新版，否则模组会不兼容
                launch_v = str(ml.mod_loader.get_mod_loader(kind).install(
                    mc_ver, self.mc_dir, loader_version=ver, java=jp,
                    callback=self._cb()))
        # 3) overrides 覆盖部署（先普通，再 client-overrides）
        os.makedirs(target, exist_ok=True)
        override_dir = mf.get("overrides") or "overrides"
        for folder in (override_dir, "client-overrides"):
            self.status.emit("%s %s ..." % (_t("正在部署"), folder))
            with zipfile.ZipFile(self.path) as zf:
                prefix = folder.rstrip("/") + "/"
                for info in zf.infolist():
                    if info.is_dir() or not info.filename.startswith(prefix):
                        continue
                    rel = info.filename[len(prefix):]
                    dest = os.path.join(target, rel)
                    if not _is_inside(target, dest):
                        continue
                    os.makedirs(os.path.dirname(dest) or target, exist_ok=True)
                    with zf.open(info) as src, open(dest, "wb") as out:
                        shutil.copyfileobj(src, out)
        # 4) 下载 mods
        files = mf.get("files") or []
        mods_dir = os.path.join(target, "mods")
        os.makedirs(mods_dir, exist_ok=True)
        total = max(len(files), 1)
        ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        getter = _orig_requests_get or requests.get
        for i, f in enumerate(files):
            if self._cancel:
                raise RuntimeError(_t("已取消导入"))
            pid, fid = f.get("projectID"), f.get("fileID")
            self.status.emit("%s %d/%d  project=%s file=%s" % (
                _t("正在下载 Mod"), i + 1, len(files), pid, fid))
            self.progress.emit(min(99, int(i / total * 100)))
            url = _cf_mod_download_url(pid, fid, self.cf_key)
            if not url:
                self._missing.append("%s/%s" % (pid, fid))
                continue
            fname = os.path.basename(url.split("?")[0]) or ("mod_%s.jar" % fid)
            dest = os.path.join(mods_dir, fname)
            if not _is_inside(mods_dir, dest):
                continue
            try:
                with getter(url, headers=ua, stream=True, timeout=60) as r:
                    r.raise_for_status()
                    with open(dest + ".part", "wb") as fp:
                        for chunk in r.iter_content(8192):
                            if self._cancel:
                                raise RuntimeError(_t("已取消导入"))
                            if chunk:
                                fp.write(chunk)
                os.replace(dest + ".part", dest)
            except RuntimeError:
                raise
            except Exception:
                self._missing.append("%s/%s" % (pid, fid))
                try:
                    if os.path.exists(dest + ".part"):
                        os.remove(dest + ".part")
                except Exception:
                    pass
        return {"launch_version": launch_v, "mc_version": mc_ver,
                "instance_dir": target}


class VersionListWorker(QThread):
    done = pyqtSignal(list)
    def run(self):
        # 不走 mll 的缓存请求（它没有超时，断网时会永久挂起），这里显式设置超时
        try:
            import requests
            r = requests.get(
                "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json",
                timeout=10)
            r.raise_for_status()
            data = r.json()
            self.done.emit(data.get("versions", []))
        except Exception:
            self.done.emit([])


class LoaderQueryWorker(QThread):
    """异步检测某版本支持的 Mod 加载器：分源请求、带超时、失败可见。"""
    done = pyqtSignal(str, list, list)  # (version, [(lid, display), ...], [错误信息, ...])
    _LOADERS = [("fabric", "Fabric"), ("quilt", "Quilt"),
                ("forge", "Forge"), ("neoforge", "NeoForge")]

    def __init__(self, version):
        super().__init__()
        self.version = version

    def run(self):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        source = load_config().get("download_source", "mirror")
        found, errors = [], []
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {ex.submit(_loader_supports, lid, self.version, source): (lid, disp)
                       for lid, disp in self._LOADERS}
            for fut in as_completed(futures):
                lid, disp = futures[fut]
                try:
                    if fut.result():
                        found.append((lid, disp))
                except Exception as e:
                    errors.append(f"{disp}（{_err_text(e)}）")
        order = {lid: i for i, (lid, _) in enumerate(self._LOADERS)}
        found.sort(key=lambda x: order[x[0]])
        self.done.emit(self.version, found, errors)


class TerraDownloadThread(QThread):
    """下载并解包陶瓦联机官方发行包。"""
    progress = pyqtSignal(int)
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancel = False

    def cancel(self):
        self._cancel = True
        # 关闭正在阻塞读取的响应，避免退出时卡在下载上
        interrupt_active_downloads()

    def run(self):
        import requests, tarfile, tempfile, shutil
        reset_download_cancel()
        os.makedirs(TERRA_DIR, exist_ok=True)
        # 下载源为镜像时优先走 Gitee，官方源优先走 GitHub
        urls = _terra_urls()
        if _DOWNLOAD_SOURCE == "mirror":
            urls.reverse()
        last = ""
        for url in urls:
            if self._cancel:
                self.failed.emit(_t("已取消"))
                return
            tmp = ""
            try:
                with requests.get(url, stream=True, timeout=(10, 60)) as r:
                    r.raise_for_status()
                    total = int(r.headers.get("Content-Length") or 0)
                    fd, tmp = tempfile.mkstemp(suffix=".tar.gz", dir=TERRA_DIR)
                    os.close(fd)
                    got = 0
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_content(65536):
                            if self._cancel:
                                raise RuntimeError("cancelled")
                            if not chunk:
                                continue
                            f.write(chunk)
                            got += len(chunk)
                            if total:
                                self.progress.emit(min(99, int(got * 100 / total)))
                with tarfile.open(tmp, "r:gz") as tf:
                    for m in tf.getmembers():
                        if not m.isfile():
                            continue
                        name = os.path.basename(m.name)
                        if not name:
                            continue
                        src = tf.extractfile(m)
                        if src is None:
                            continue
                        with src, open(os.path.join(TERRA_DIR, name), "wb") as out:
                            shutil.copyfileobj(src, out)
                if sys.platform != "win32":
                    for n in os.listdir(TERRA_DIR):
                        if n.lower().startswith("terracotta"):
                            try:
                                os.chmod(os.path.join(TERRA_DIR, n), 0o755)
                            except Exception:
                                pass
                os.remove(tmp)
                self.progress.emit(100)
                exe = find_terracotta()
                if not exe:
                    raise RuntimeError("发行包中未找到可执行文件")
                self.done.emit(exe)
                return
            except Exception as e:
                if self._cancel:
                    self.failed.emit(_t("已取消"))
                    return
                last = str(e) if isinstance(e, RuntimeError) else _err_text(e)
                try:
                    if tmp and os.path.exists(tmp):
                        os.remove(tmp)
                except Exception:
                    pass
        self.failed.emit(last or _t("网络错误"))


class TerraStartThread(QThread):
    """启动 terracotta --hmcl <file>，并从端口文件读取本地 API 端口。"""
    ok = pyqtSignal(int)
    failed = pyqtSignal(str)

    def __init__(self, exe, port_file, parent=None):
        super().__init__(parent)
        self._exe = exe
        self._port_file = port_file
        self._stop = False
        self.proc = None

    def stop(self):
        self._stop = True

    def terminate(self):
        """结束已启动的陶瓦进程（用于「停止联机」）。"""
        self._stop = True
        p = self.proc
        if p is not None:
            try:
                p.terminate()
            except Exception:
                pass
        self.proc = None

    def run(self):
        try:
            if os.path.exists(self._port_file):
                os.remove(self._port_file)
            kw = {}
            if os.name == "nt":
                kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW，避免弹出黑窗
            self.proc = subprocess.Popen(
                [self._exe, "--hmcl", self._port_file], cwd=TERRA_DIR,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, **kw)
            deadline = time.time() + 15
            while time.time() < deadline and not self._stop:
                if os.path.isfile(self._port_file):
                    try:
                        with open(self._port_file, "r", encoding="utf-8") as f:
                            port = json.load(f).get("port")
                        if isinstance(port, int) and port > 0:
                            self.ok.emit(port)
                            return
                    except Exception:
                        pass
                time.sleep(0.2)
            if not self._stop:
                self.failed.emit(_t("陶瓦联机启动超时"))
        except Exception as e:
            self.failed.emit(str(e))


class TerraStateThread(QThread):
    """轮询陶瓦联机本地 HTTP API 的 /state，状态变化时通知界面。"""
    changed = pyqtSignal(dict)
    lost = pyqtSignal()

    def __init__(self, port, parent=None):
        super().__init__(parent)
        self._port = port
        self._stop = False
        self._last = None

    def stop(self):
        self._stop = True

    def run(self):
        import requests
        fails = 0
        while not self._stop:
            try:
                r = requests.get("http://127.0.0.1:%d/state" % self._port, timeout=2)
                if r.status_code == 200:
                    fails = 0
                    d = r.json()
                    key = json.dumps(d, sort_keys=True, ensure_ascii=False)
                    if key != self._last:
                        self._last = key
                        self.changed.emit(d)
                else:
                    fails += 1
            except Exception:
                fails += 1
            if fails >= 5:
                # 连续 5 次失败：陶瓦进程已退出或异常
                self.lost.emit()
                return
            for _ in range(8):
                if self._stop:
                    return
                time.sleep(0.1)


class TerraControlThread(QThread):
    """调用陶瓦本地 HTTP 控制接口（/state/scanning、/state/guesting、/state/ide）。"""
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, port, path, params=None, parent=None):
        super().__init__(parent)
        self._port = port
        self._path = path
        self._params = params or {}

    def run(self):
        import requests
        try:
            r = requests.get("http://127.0.0.1:%d%s" % (self._port, self._path),
                             params=self._params, timeout=(3, 8))
            if r.status_code == 200:
                try:
                    self.done.emit(r.json() if r.text.strip() else {})
                except Exception:
                    self.done.emit({})
            elif r.status_code == 400:
                self.failed.emit(_t("房间码无效，或当前不处于待机状态。"))
            else:
                self.failed.emit(_t("陶瓦接口返回错误：HTTP %s") % r.status_code)
        except Exception as e:
            self.failed.emit(_err_text(e))


class StellarLoginThread(QThread):
    """恒星映射登录，成功发出 (token, username)，失败发出错误信息。"""
    ok = pyqtSignal(str, str)
    failed = pyqtSignal(str)

    def __init__(self, account, password, parent=None):
        super().__init__(parent)
        self._account = account
        self._password = password

    def run(self):
        try:
            reset_download_cancel()
            r = _stellar_post(
                "/api/v1/users/login",
                json={"account": self._account, "password": self._password},
                timeout=(10, 30),
            )
            try:
                data = r.json()
            except Exception:
                self.failed.emit(_t("网络错误"))
                return
            if data.get("code") == 200:
                token = (data.get("data") or {}).get("token")
                if not token:
                    self.failed.emit(_t("服务器返回数据异常"))
                    return
                self.ok.emit(token, self._account)
                return
            msg = data.get("msg") or _t("用户名或密码错误")
            if "密码" in msg or "用户不存在" in msg or "账号" in msg:
                self.failed.emit(_t("用户名或密码错误"))
            else:
                self.failed.emit(msg)
        except Exception as e:
            self.failed.emit(_t("无法连接（网络问题）")
                             if "Connection" in type(e).__name__
                             or "Timeout" in type(e).__name__
                             or "SSL" in type(e).__name__
                             else _err_text(e))


class StellarInfoThread(QThread):
    """拉取用户信息与签到状态；若未签到则自动签到。"""
    info = pyqtSignal(dict)
    checkin = pyqtSignal(dict)
    checked = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, token, do_checkin=True, parent=None):
        super().__init__(parent)
        self._token = token
        self._do_checkin = do_checkin

    def run(self):
        try:
            r = _stellar_get("/api/v1/users/info", token=self._token, timeout=(10, 30))
            info = r.json()
            if info.get("code") != 200:
                self.failed.emit(info.get("msg") or _t("获取用户信息失败"))
                return
            self.info.emit(info.get("data") or {})
            rc = _stellar_get("/api/v1/users/checkin/status",
                              token=self._token, timeout=(10, 30)).json()
            cs = (rc.get("data") or {}) if rc.get("code") == 200 else {}
            if self._do_checkin and not cs.get("has_checked"):
                try:
                    cr = _stellar_post("/api/v1/users/checkin",
                                       token=self._token, timeout=(10, 30)).json()
                    if cr.get("code") == 200:
                        cs["has_checked"] = True
                        self.checked.emit(_t("自动签到成功"))
                    else:
                        self.checked.emit(cr.get("msg") or _t("签到失败"))
                except Exception:
                    self.checked.emit(_t("签到失败（网络错误）"))
            self.checkin.emit(cs)
        except Exception as e:
            self.failed.emit(_err_text(e))


class StellarDownloadThread(QThread):
    """下载恒星映射客户端（StellarFrpc），成功发出可执行文件路径，失败发出错误信息。"""
    progress = pyqtSignal(int)
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def run(self):
        try:
            import requests
            r = requests.get(STELLAR_FRPC_LIST_URL, timeout=(10, 30))
            data = r.json()
            if data.get("code") != 200:
                self.failed.emit(_t("获取客户端列表失败"))
                return
            content = (data.get("data") or {}).get("content") or []
            names = [c.get("name", "") for c in content if c.get("name")]
            fname = _stellar_pick_frpc_file(names)
            if not fname:
                self.failed.emit(_t("未找到适合当前系统的客户端"))
                return
            url = STELLAR_FRPC_DL_BASE + "/" + fname
            os.makedirs(STELLAR_FRPC_DIR, exist_ok=True)
            tmp = os.path.join(STELLAR_FRPC_DIR, fname)
            with requests.get(url, stream=True, timeout=(10, 60)) as r:
                r.raise_for_status()
                total = int(r.headers.get("Content-Length", 0))
                got = 0
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        if not chunk:
                            continue
                        f.write(chunk)
                        got += len(chunk)
                        if total:
                            self.progress.emit(min(100, int(got * 100 / total)))
            exe = _stellar_pick_bin_from_archive(tmp)
            try:
                os.remove(tmp)
            except Exception:
                pass
            if exe and os.path.isfile(exe):
                self.done.emit(exe)
            else:
                self.failed.emit(_t("客户端解压失败"))
        except Exception as e:
            self.failed.emit(_err_text(e))


class StellarTunnelsThread(QThread):
    """拉取隧道列表及在线状态，成功发出隧道列表。"""
    done = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, token, parent=None):
        super().__init__(parent)
        self._token = token

    def run(self):
        try:
            r = _stellar_get("/api/v1/proxy/get", token=self._token,
                             params={"page": 1, "page_size": 100}, timeout=(10, 30))
            data = r.json()
            if data.get("code") != 200:
                self.failed.emit(data.get("msg") or _t("获取隧道列表失败"))
                return
            raw = data.get("tunnel") or {}
            # API 返回的 tunnel 是以隧道名（或 ID）为键的字典，
            # StellarFrpc 的 -t 参数正是拿这个键去 tunnelData 里取配置，
            # 所以必须原样保留键名，不能只留下 Id 字段。
            tunnels = []
            for key, t in raw.items():
                if isinstance(t, dict):
                    t["_key"] = str(key)
                    tunnels.append(t)
            ids = [str(t.get("Id")) for t in tunnels if t.get("Id") is not None]
            status_map = {}
            if ids:
                try:
                    sr = _stellar_post("/api/v1/proxy/status", token=self._token,
                                       json={"id": ids}, timeout=(10, 30)).json()
                    if sr.get("code") == 200:
                        status_map = sr.get("data") or {}
                except Exception:
                    pass
            for t in tunnels:
                tid = str(t.get("Id", ""))
                st = status_map.get(tid, {})
                t["_online"] = (st.get("status") or t.get("Status") or "").lower() == "online"
            self.done.emit(tunnels)
        except Exception as e:
            self.failed.emit(_err_text(e))


class StellarFrpcProcess(QThread):
    """启动 StellarFrpc 客户端并监控其输出，结束时发出退出码与输出。"""
    output = pyqtSignal(str)
    finished_code = pyqtSignal(int, str)

    def __init__(self, exe, token, tunnel_id, parent=None):
        super().__init__(parent)
        self._exe = exe
        self._token = token
        self._tunnel_id = str(tunnel_id)
        self._proc = None
        self._stop = False

    def run(self):
        try:
            # 启动前先验证客户端本身能否在本机运行（-v 只输出一行版本号）。
            # 文件损坏、下载不完整或被安全软件拦截时，这一步就会失败，
            # 比直接连隧道更容易定位问题。
            ok, probe_out = self._probe()
            if not ok:
                self.finished_code.emit(self.PROBE_FAILED, probe_out)
                return
            args = [self._exe, "-u", self._token, "-t", self._tunnel_id, "-d"]
            creationflags = 0
            if sys.platform == "win32":
                creationflags = 0x08000000  # CREATE_NO_WINDOW
            self._proc = subprocess.Popen(
                args, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                cwd=os.path.dirname(self._exe) or None,
                creationflags=creationflags)
            out_lines = []
            while True:
                line = self._proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                out_lines.append(text)
                self.output.emit(text)
            code = self._proc.wait()
            self._write_log(out_lines)
            self.finished_code.emit(code, "\n".join(out_lines[-60:]))
        except Exception as e:
            self.finished_code.emit(-1, str(e))

    PROBE_FAILED = -2  # 客户端无法在本机执行（文件损坏 / 被杀毒软件拦截）

    def _probe(self):
        """返回 (是否可用, 输出)。用 -v 验证可执行文件真的能跑起来。"""
        try:
            r = subprocess.run(
                [self._exe, "-v"], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=10, creationflags=(0x08000000 if sys.platform == "win32" else 0),
                cwd=os.path.dirname(self._exe) or None)
        except Exception as e:
            return False, "%s" % e
        out = (r.stdout or b"").decode("utf-8", errors="replace").strip()
        return (r.returncode == 0 and bool(out)), out

    def _write_log(self, lines):
        """把完整输出留在本地，方便用户反馈问题时贴日志。"""
        try:
            os.makedirs(STELLAR_FRPC_DIR, exist_ok=True)
            with open(os.path.join(STELLAR_FRPC_DIR, "last_run.log"),
                      "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception:
            pass

    def stop(self):
        self._stop = True
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass
# ============ tkinter 界面基础设施 ============
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, font as tkfont

_THEME_WATCH = []


def C(role):
    """角色名（bg/card/accent…）或 #hex → 具体色值。"""
    if not role:
        return None
    if isinstance(role, str) and role.startswith("#"):
        return role
    return theme_palette().get(role, "#000000")


def F(size, bold=False, mono=False):
    fam = "Consolas" if mono else "Microsoft YaHei"
    return (fam, -int(size), "bold" if bold else "normal")


def _mk_scrollbar(parent, command):
    return tk.Scrollbar(parent, orient="vertical", command=command,
                        bg=C("card"), troughcolor=C("bg"), activebackground=C("border"),
                        highlightthickness=0, bd=0, width=10, relief="flat")


def _resolve(v):
    return C(v) if v is not None else None


def style(widget, bg=None, fg=None, active_bg=None, active_fg=None, **extra):
    """记录控件的主题色角色，主题切换时统一重绘。"""
    spec = {"bg": bg, "fg": fg, "active_bg": active_bg, "active_fg": active_fg}
    for k, v in extra.items():
        if v is not None:
            spec[k] = v
    widget._lcl_style = spec
    _THEME_WATCH.append((weakref.ref(widget), spec))
    _apply_style(widget, spec)
    return widget


_SPEC_TO_TK = {
    "bg": "bg", "fg": "fg",
    "active_bg": "activebackground", "active_fg": "activeforeground",
    "insert_bg": "insertbackground", "hl_bg": "highlightbackground",
    "select_bg": "selectbackground", "select_fg": "selectforeground",
    "trough": "troughcolor",
}


def _apply_style(widget, spec):
    keys = {}
    for name, opt in _SPEC_TO_TK.items():
        if spec.get(name) is not None:
            keys[opt] = _resolve(spec[name])
    if keys:
        for opt, val in keys.items():
            try:
                widget.configure(**{opt: val})
            except Exception:
                pass
    if hasattr(widget, "_on_theme"):
        try:
            widget._on_theme()
        except Exception:
            pass


def restyle_all():
    alive = []
    for ref, spec in _THEME_WATCH:
        w = ref()
        if w is None:
            continue
        alive.append((ref, spec))
        _apply_style(w, spec)
    _THEME_WATCH[:] = alive


def set_roles(widget, **roles):
    """按动态状态设置配色，并同步更新已登记的主题角色（切换主题不丢失状态）。"""
    spec = getattr(widget, "_lcl_style", None)
    if not isinstance(spec, dict):
        spec = {}
        widget._lcl_style = spec
        _THEME_WATCH.append((weakref.ref(widget), spec))
    spec.update(roles)
    _apply_style(widget, spec)



# ---------- 自动主题登记 ----------
# 页面里大量控件直接用 C("bg") 等色值构造。这里在构造后反查该色值对应的
# 主题角色，自动登记到 _THEME_WATCH，切换主题时即可统一重绘。
_AUTO_ROLES = (
    "bg", "nav", "panel", "card", "border", "border2", "log_bg",
    "text", "text2", "muted", "dim",
    "accent", "accent2", "accent_hover", "accent_hover2",
    "accent_press", "accent_press2",
    "red1", "red1h", "red2", "red2h", "red2p",
    "yellow", "teal", "teal2", "log_fg", "log_fg2", "log_fg3",
)
_AUTO_TK_KEYS = {
    "bg": "bg", "fg": "fg",
    "activebackground": "active_bg", "activeforeground": "active_fg",
    "insertbackground": "insert_bg", "highlightbackground": "hl_bg",
    "selectbackground": "select_bg", "selectforeground": "select_fg",
    "troughcolor": "trough",
}


def _auto_role(color):
    if not isinstance(color, str) or not color.startswith("#"):
        return None
    val = color.lower()
    pal = theme_palette()
    for role in _AUTO_ROLES:
        col = pal.get(role)
        if col and col.lower() == val:
            return role
    return None


def _install_auto_theme():
    def wrap(cls):
        if getattr(cls, "_lcl_auto_init", False):
            return
        orig = cls.__init__

        def _init(self, master=None, cnf=None, **kw):
            if cnf is None:
                cnf = {}
            orig(self, master, cnf, **kw)
            spec = {}
            for tk_key, spec_key in _AUTO_TK_KEYS.items():
                try:
                    val = self.cget(tk_key)
                except Exception:
                    continue
                if not val:
                    continue
                role = _auto_role(val)
                # "#1e1e2e" 既是深色底也是强调按钮上的固定前景色，
                # 选中项前景保持固定深色更易读，避免被误映射成 bg
                if role and not (tk_key == "selectforeground" and role == "bg"):
                    spec[spec_key] = role
            if spec:
                self._lcl_style = spec
                _THEME_WATCH.append((weakref.ref(self), spec))

        cls.__init__ = _init
        cls._lcl_auto_init = True

    for _cls in (tk.Frame, tk.Label, tk.Button, tk.Canvas, tk.Text, tk.Listbox,
                 tk.Entry, tk.Checkbutton, tk.Scrollbar, tk.Toplevel):
        wrap(_cls)


_install_auto_theme()



# ---------- 图标（用 Canvas 矢量绘制，替代表情符号） ----------
ICON_KINDS = ("power", "gear", "download", "folder", "info", "link", "palette",
              "pencil", "x", "minus", "back", "warn", "dot", "dot_o", "bolt")


class IconCanvas(tk.Canvas):
    def __init__(self, parent, kind="dot", color="text2", size=16, bg=None):
        self._bg_role = bg or "bg"
        super().__init__(parent, width=size, height=size,
                         bg=_resolve(self._bg_role),
                         highlightthickness=0, bd=0)
        self._kind = kind
        self._color = color or "text2"
        self._size = size
        self._on_theme = self._redraw
        _THEME_WATCH.append((weakref.ref(self), {"bg": self._bg_role}))
        self._redraw()

    def set_icon(self, kind, color=None):
        self._kind = kind
        if color:
            self._color = color
        self._redraw()

    def set_bg(self, role):
        self._bg_role = role
        self.configure(bg=_resolve(role))
        self._redraw()

    def _redraw(self):
        try:
            self.delete("all")
            self.configure(bg=_resolve(self._bg_role))
        except Exception:
            pass
        draw_icon(self, self._kind, _resolve(self._color), self._size)


def draw_icon(cv, kind, color, s):
    """在 Canvas 上绘制矢量图标（坐标同原 QPainter 版本）。"""
    lw = max(1, int(s / 10))
    if kind == "power":
        cv.create_line(s * 0.5, s * 0.10, s * 0.5, s * 0.46, fill=color, width=lw)
        cv.create_arc(s * 0.18, s * 0.22, s * 0.82, s * 0.86, start=130, extent=-260,
                      style="arc", outline=color, width=lw)
    elif kind == "gear":
        cx = cy = s / 2
        r1 = s * 0.30
        cv.create_oval(cx - r1, cy - r1, cx + r1, cy + r1, outline=color, width=lw)
        r2 = s * 0.42
        import math as _m
        for i in range(6):
            a = _m.radians(i * 60)
            cv.create_line(cx + r1 * _m.cos(a), cy + r1 * _m.sin(a),
                           cx + r2 * _m.cos(a), cy + r2 * _m.sin(a), fill=color, width=lw * 2)
    elif kind == "download":
        cv.create_line(s * 0.5, s * 0.12, s * 0.5, s * 0.62, fill=color, width=lw)
        cv.create_line(s * 0.28, s * 0.44, s * 0.5, s * 0.66, fill=color, width=lw)
        cv.create_line(s * 0.72, s * 0.44, s * 0.5, s * 0.66, fill=color, width=lw)
        cv.create_line(s * 0.16, s * 0.84, s * 0.84, s * 0.84, fill=color, width=lw)
    elif kind == "folder":
        cv.create_line(s * 0.10, s * 0.30, s * 0.40, s * 0.30, fill=color, width=lw)
        cv.create_line(s * 0.40, s * 0.30, s * 0.48, s * 0.42, fill=color, width=lw)
        cv.create_rectangle(s * 0.10, s * 0.30, s * 0.90, s * 0.78, outline=color, width=lw)
    elif kind == "info":
        cv.create_oval(s * 0.12, s * 0.12, s * 0.88, s * 0.88, outline=color, width=lw)
        cv.create_line(s * 0.5, s * 0.44, s * 0.5, s * 0.70, fill=color, width=lw)
        cv.create_oval(s * 0.44, s * 0.26, s * 0.56, s * 0.38, outline=color, width=lw)
    elif kind == "link":
        cv.create_oval(s * 0.08, s * 0.38, s * 0.46, s * 0.66, outline=color, width=lw)
        cv.create_oval(s * 0.54, s * 0.38, s * 0.92, s * 0.66, outline=color, width=lw)
        cv.create_line(s * 0.40, s * 0.52, s * 0.62, s * 0.52, fill=color, width=lw)
    elif kind == "palette":
        cv.create_oval(s * 0.10, s * 0.10, s * 0.90, s * 0.90, outline=color, width=lw)
        for dx, dy in ((0.30, 0.26), (0.52, 0.20), (0.28, 0.54)):
            cv.create_oval(s * dx - 2, s * dy - 2, s * dx + 2, s * dy + 2,
                           fill=color, outline=color)
    elif kind == "pencil":
        cv.create_line(s * 0.18, s * 0.82, s * 0.34, s * 0.78, fill=color, width=lw)
        cv.create_line(s * 0.34, s * 0.78, s * 0.82, s * 0.16, fill=color, width=lw * 2)
    elif kind == "x":
        cv.create_line(s * 0.22, s * 0.22, s * 0.78, s * 0.78, fill=color, width=lw)
        cv.create_line(s * 0.78, s * 0.22, s * 0.22, s * 0.78, fill=color, width=lw)
    elif kind == "minus":
        cv.create_line(s * 0.18, s * 0.5, s * 0.82, s * 0.5, fill=color, width=lw)
    elif kind == "back":
        cv.create_line(s * 0.86, s * 0.5, s * 0.20, s * 0.5, fill=color, width=lw)
        cv.create_line(s * 0.20, s * 0.5, s * 0.45, s * 0.26, fill=color, width=lw)
        cv.create_line(s * 0.20, s * 0.5, s * 0.45, s * 0.74, fill=color, width=lw)
    elif kind == "warn":
        cv.create_polygon(s * 0.5, s * 0.12, s * 0.92, s * 0.86, s * 0.08, s * 0.86,
                          outline=color, fill="", width=lw)
        cv.create_line(s * 0.5, s * 0.38, s * 0.5, s * 0.62, fill=color, width=lw)
        cv.create_oval(s * 0.46, s * 0.68, s * 0.54, s * 0.76, outline=color, width=lw)
    elif kind == "dot":
        cv.create_oval(s * 0.18, s * 0.18, s * 0.82, s * 0.82, fill=color, outline=color)
    elif kind == "user":
        cv.create_oval(s * 0.30, s * 0.10, s * 0.70, s * 0.50,
                       outline=color, width=lw)
        cv.create_arc(s * 0.14, s * 0.52, s * 0.86, s * 1.32, start=20,
                      extent=140, style="arc", outline=color, width=lw)
    elif kind == "dot_o":
        cv.create_oval(s * 0.18, s * 0.18, s * 0.82, s * 0.82, outline=color, width=lw)
    elif kind == "bolt":
        cv.create_polygon(s * 0.58, s * 0.04, s * 0.20, s * 0.56, s * 0.44, s * 0.56,
                          s * 0.38, s * 0.96, s * 0.80, s * 0.42, s * 0.54, s * 0.42,
                          fill=color, outline=color)


# ---------- 通用控件 ----------
class ScrollFrame(tk.Frame):
    """带纵向滚动的容器，inner 为内容父控件。"""

    def __init__(self, parent, bg="bg"):
        super().__init__(parent, bg=_resolve(bg))
        self._bg_role = bg
        style(self, bg=bg)
        self.canvas = tk.Canvas(self, bg=_resolve(bg), highlightthickness=0, bd=0)
        self.vbar = _mk_scrollbar(self, self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vbar.pack(side="right", fill="y")
        self.inner = tk.Frame(self.canvas, bg=_resolve(bg))
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

    def _bind_wheel(self, _=None):
        self.canvas.bind_all("<MouseWheel>", self._wheel)
        self.canvas.bind_all("<Button-4>", lambda e: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind_all("<Button-5>", lambda e: self.canvas.yview_scroll(1, "units"))

    def _unbind_wheel(self, _=None):
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            try:
                self.canvas.unbind_all(seq)
            except Exception:
                pass

    def _on_inner(self, _=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, e):
        self.canvas.itemconfigure(self._win, width=e.width)

    def _wheel(self, e):
        delta = -1 if getattr(e, "delta", 0) > 0 else 1
        self.canvas.yview_scroll(delta, "units")

    def _on_theme(self):
        self.configure(bg=_resolve(self._bg_role))
        self.canvas.configure(bg=_resolve(self._bg_role))
        self.inner.configure(bg=_resolve(self._bg_role))

    def clear(self):
        for w in self.inner.winfo_children():
            w.destroy()


class DropDown(tk.Canvas):
    """自绘下拉框（圆角，避免 ttk 下拉框在深色主题下无法着色）。"""

    def __init__(self, parent, values=None, width=200, command=None, bg="card"):
        self._surround_role = _surround_of(parent)
        super().__init__(parent, bg=_resolve(self._surround_role),
                         height=32, highlightthickness=0, bd=0, cursor="hand2")
        self._values = list(values or [])
        self._command = command
        self._bg_role = bg
        self.configure(width=width)
        self._lcl_style = {"bg": bg}
        _THEME_WATCH.append((weakref.ref(self), self._lcl_style))
        self.bind("<Button-1>", self._open)
        self.bind("<Configure>", lambda e: self._redraw())
        self._idx = 0
        self._pop = None
        self._disabled = False

    def set_values(self, values, index=0):
        self._values = list(values)
        self._idx = index if 0 <= index < len(self._values) else 0
        self._refresh_text()

    def _refresh_text(self):
        self._redraw()

    def _redraw(self):
        try:
            self.delete("all")
            self.configure(bg=_resolve(self._surround_role))
            w = max(2, self.winfo_width())
            h = max(2, self.winfo_height())
            spec = getattr(self, "_lcl_style", None) or {}
            fill = _resolve(spec.get("bg") or self._bg_role)
            _rounded_rect(self, 0.5, 0.5, w - 0.5, h - 0.5, 8, fill)
            fg = _resolve("muted" if self._disabled else "text")
            txt = str(self._values[self._idx]) if self._values else ""
            self.create_text(10, h / 2, text=txt, fill=fg, font=F(13),
                             anchor="w")
            self.create_text(w - 18, h / 2, text="▾",
                             fill=_resolve("muted" if self._disabled
                                           else "text2"), font=F(12))
        except Exception:
            pass

    def _on_theme(self):
        self._redraw()

    def current(self):
        return self._idx

    def current_text(self):
        return self._values[self._idx] if self._values else ""

    def set_current_index(self, i):
        if self._values:
            self._idx = max(0, min(i, len(self._values) - 1))
            self._refresh_text()

    def set_current_text(self, text):
        if text in self._values:
            self._idx = self._values.index(text)
            self._refresh_text()

    def setEnabled(self, ok):
        self._disabled = not ok
        try:
            self.configure(cursor="" if self._disabled else "hand2")
        except Exception:
            pass
        self._redraw()

    def _open(self, _=None):
        if self._disabled or not self._values:
            return
        if self._pop is not None:
            return
        pop = tk.Toplevel(self)
        self._pop = pop
        pop.overrideredirect(True)
        pop.configure(bg=_resolve("border"))
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height()
        h = min(260, 30 * len(self._values) + 4)
        pop.geometry("%dx%d+%d+%d" % (max(self.winfo_width(), 160), h, x, y))
        lb = tk.Listbox(pop, bg=_resolve("panel"), fg=_resolve("text"),
                        selectbackground=_resolve("accent"), selectforeground="#1e1e2e",
                        font=F(13), bd=0, highlightthickness=0, activestyle="none")
        for v in self._values:
            lb.insert("end", v)
        lb.selection_set(self._idx)
        lb.pack(fill="both", expand=True, padx=1, pady=1)

        def choose(_=None):
            sel = lb.curselection()
            if sel:
                self._idx = sel[0]
                self._refresh_text()
                if self._command:
                    self._command(self._idx)
            self._close()

        lb.bind("<ButtonRelease-1>", choose)
        lb.bind("<Escape>", lambda e: self._close())
        lb.bind("<FocusOut>", lambda e: self._close())
        lb.focus_set()

    def _close(self):
        if self._pop is not None:
            try:
                self._pop.destroy()
            except Exception:
                pass
            self._pop = None


class ProgressBar(tk.Canvas):
    def __init__(self, parent, height=16, bg="card", fill="accent", bg_role="bg", text=True):
        super().__init__(parent, height=height, bg=_resolve(bg_role),
                         highlightthickness=0, bd=0)
        self._height = height
        self._bg = bg
        self._fill = fill
        self._bg_role = bg_role
        self._text = text
        self._value = 0
        self._maximum = 100
        self._label = ""
        self._on_theme = self._redraw
        self.bind("<Configure>", lambda e: self._redraw())
        self._redraw()

    def setValue(self, v):
        self._value = v
        self._redraw()

    def setRange(self, lo, hi):
        self._maximum = hi if hi > lo else 1
        self._redraw()

    def setMaximum(self, n):
        self._maximum = max(1, int(n))

    def setVisible(self, ok):
        if ok:
            self.pack(fill="x", pady=2)
        else:
            self.pack_forget()

    def setText(self, s):
        self._label = s
        self._redraw()

    def setFill(self, role):
        self._fill = role
        self._redraw()

    def _redraw(self):
        try:
            self.delete("all")
            w = self.winfo_width() or 100
            h = self._height
            r = max(2, int(h * 0.45))
            _rounded_rect(self, 0, 0, w, h, r, _resolve(self._bg))
            ratio = 0 if self._maximum <= 0 else min(1.0, max(0.0, self._value / self._maximum))
            if ratio > 0:
                _rounded_rect(self, 0, 0, max(2.0, w * ratio), h, r,
                              _resolve(self._fill))
            if self._text:
                txt = self._label or ("%d%%" % int(ratio * 100))
                self.create_text(w / 2, h / 2, text=txt, fill=_resolve("text"),
                                 font=F(max(9, h - 5)))
        except Exception:
            pass


class AccentButton(tk.Canvas):
    """主按钮：渐变蓝，自绘圆角矩形 + 文字。"""

    def __init__(self, parent, text="", command=None, width=140, height=40,
                 font_size=14, bg_role="bg"):
        super().__init__(parent, width=width, height=height, bg=_resolve(bg_role),
                         highlightthickness=0, bd=0)
        self._text = text
        self._command = command
        self._bw = width
        self._bh = height
        self._fs = font_size
        self._bg_role = bg_role
        self._state = "normal"
        self._hover = False
        self._pressed = False
        self._colors = None
        self._on_theme = self._redraw
        self.bind("<Button-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Configure>", self._on_resize)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self._redraw()

    def setText(self, t):
        self._text = t
        self._redraw()

    def _on_resize(self, e):
        if e.width > 1 and e.height > 1 and (e.width, e.height) != (self._bw, self._bh):
            self._bw, self._bh = e.width, e.height
            self._redraw()

    def setEnabled(self, ok):
        self._state = "normal" if ok else "disabled"
        self._redraw()

    def setColors(self, normal=None, hover=None, press=None, fg="#1e1e2e"):
        self._colors = {"normal": normal, "hover": hover, "press": press, "fg": fg}
        self._redraw()

    def _set_hover(self, ok):
        self._hover = ok
        try:
            self.configure(cursor="hand2" if ok else "")
        except Exception:
            pass
        self._redraw()

    def _press(self, _):
        if self._state == "disabled":
            return
        self._pressed = True
        self._redraw()

    def _release(self, _):
        if self._state == "disabled":
            return
        was = self._pressed
        self._pressed = False
        self._redraw()
        if was and self._command:
            self._command()

    def _redraw(self):
        try:
            self.delete("all")
            w, h = self._bw, self._bh
            if self._state == "disabled":
                c1 = c2 = _resolve("card")
                fg = _resolve("muted")
            elif self._colors:
                key = "press" if self._pressed else ("hover" if self._hover else "normal")
                pair = self._colors.get(key) or self._colors.get("normal")
                c1, c2 = _resolve(pair[0]), _resolve(pair[1])
                fg = _resolve(self._colors.get("fg", "#1e1e2e"))
            elif self._pressed:
                c1, c2 = _resolve("accent_press"), _resolve("accent_press2")
                fg = "#1e1e2e"
            elif self._hover:
                c1, c2 = _resolve("accent_hover"), _resolve("accent_hover2")
                fg = "#1e1e2e"
            else:
                c1, c2 = _resolve("accent"), _resolve("accent2")
                fg = "#1e1e2e"
            _gradient_round_rect(self, w, h, c1, c2, 8)
            self.create_text(w / 2, h / 2, text=self._text, fill=fg,
                             font=F(self._fs, bold=True))
        except Exception:
            pass


def _rounded_rect(cv, x0, y0, x1, y1, r, fill, outline=None, width=1, **kw):
    """在 Canvas 上绘制圆角矩形（平滑多边形近似），可单独指定描边。"""
    r = max(0.0, min(float(r), (x1 - x0) / 2.0, (y1 - y0) / 2.0))
    if outline is None:
        outline = fill
    if r <= 0.5:
        cv.create_rectangle(x0, y0, x1, y1, fill=fill, outline=outline,
                            width=width, **kw)
        return
    cv.create_polygon(
        [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r,
         x1, y1 - r, x1, y1, x1 - r, y1,
         x0 + r, y1, x0, y1, x0, y1 - r,
         x0, y0 + r, x0, y0],
        fill=fill, outline=outline, width=width, smooth=True, splinesteps=8,
        **kw)


def _gradient_round_rect(cv, w, h, c1, c2, r=8):
    """横向渐变 + 圆角的填充（逐列绘制，保证四角是圆的）。"""
    w = max(2, int(w))
    h = max(2, int(h))
    r = max(0, min(int(r), w // 2, h // 2))
    if r <= 0:
        for i in range(24):
            x0 = w * i / 24.0
            x1 = w * (i + 1) / 24.0
            col = _mix(c1, c2, i / 23.0)
            cv.create_rectangle(x0, 0, x1, h, fill=col, outline=col)
        return
    import math as _m
    for x in range(w):
        if x < r:
            dy = r - _m.sqrt(max(0.0, r * r - (r - x - 0.5) ** 2))
        elif x >= w - r:
            dy = r - _m.sqrt(max(0.0, r * r - (x - (w - r) + 0.5) ** 2))
        else:
            dy = 0.0
        col = _mix(c1, c2, x / max(1, w - 1))
        cv.create_rectangle(x, dy, x + 1, h - dy, fill=col, outline=col)


def _mix(c1, c2, t):
    try:
        r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
        r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
        r = int(r1 + (r2 - r1) * t)
        g = int(g1 + (g2 - g1) * t)
        b = int(b1 + (b2 - b1) * t)
        return "#%02x%02x%02x" % (r, g, b)
    except Exception:
        return c1


def _surround_of(parent, default="bg"):
    """取父控件背景对应的主题角色，作为圆角控件的衬底色。"""
    try:
        return _auto_role(str(parent.cget("bg"))) or default
    except Exception:
        return default


class FlatButton(tk.Canvas):
    """次要按钮：自绘圆角矩形 + 文字，悬停变亮。"""

    def __init__(self, parent, text="", command=None, bg="card", fg="text",
                 hover="border", padx=16, pady=7, font_size=13, bold=False,
                 radius=8):
        self._surround_role = _surround_of(parent)
        super().__init__(parent, bg=_resolve(self._surround_role),
                         highlightthickness=0, bd=0, cursor="hand2")
        self._fill_role, self._fg_role, self._hover_role = bg, fg, hover
        self._text, self._command = text, command
        self._padx, self._pady = padx, pady
        self._fs, self._bold = font_size, bold
        self._radius = radius
        self._enabled = True
        self._hover = False
        self._font = tkfont.Font(font=F(font_size, bold))
        self._fit()
        self._lcl_style = {"bg": bg, "fg": fg}
        _THEME_WATCH.append((weakref.ref(self), self._lcl_style))
        self.bind("<Enter>", lambda e: self._hover_set(True))
        self.bind("<Leave>", lambda e: self._hover_set(False))
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<Configure>", lambda e: self._redraw())
        self._redraw()

    def _fit(self):
        try:
            w = self._font.measure(self._text or " ") + self._padx * 2
            h = self._font.metrics("linespace") + self._pady * 2
            self.configure(width=max(8, w), height=max(4, h))
        except Exception:
            pass

    def _hover_set(self, ok):
        self._hover = ok
        self._redraw()

    def _click(self, _):
        if self._enabled and self._command:
            self._command()

    def _redraw(self):
        try:
            self.delete("all")
            self.configure(bg=_resolve(self._surround_role))
            w = max(2, self.winfo_width())
            h = max(2, self.winfo_height())
            spec = getattr(self, "_lcl_style", None) or {}
            fill_role = spec.get("bg") or self._fill_role
            fg_role = spec.get("fg") or self._fg_role
            if not self._enabled:
                fill_role, fg_role = "panel", "muted"
            elif self._hover:
                fill_role = self._hover_role
            _rounded_rect(self, 0.5, 0.5, w - 0.5, h - 0.5, self._radius,
                          _resolve(fill_role))
            self.create_text(w / 2, h / 2, text=self._text,
                             fill=_resolve(fg_role),
                             font=F(self._fs, self._bold))
        except Exception:
            pass

    def _on_theme(self):
        self._redraw()

    def setEnabled(self, ok):
        self._enabled = bool(ok)
        spec = getattr(self, "_lcl_style", None)
        if isinstance(spec, dict):
            spec["bg"] = self._fill_role if ok else "panel"
            spec["fg"] = self._fg_role if ok else "muted"
        try:
            self.configure(cursor="hand2" if ok else "")
        except Exception:
            pass
        self._redraw()

    def setText(self, t):
        self._text = t
        self._fit()
        self._redraw()


class RoundedCard(tk.Canvas):
    """圆角卡片容器：内容放进 .inner；支持选中描边。"""

    def __init__(self, parent, radius=10, bg="card", border="border",
                 surround=None, borderwidth=1, padx=10, pady=8, cursor=None):
        self._surround_role = surround or _surround_of(parent)
        super().__init__(parent, bg=_resolve(self._surround_role),
                         highlightthickness=0, bd=0,
                         cursor=cursor or "")
        self._radius, self._bg_role, self._border_role = radius, bg, border
        self._bw = max(1, int(borderwidth))
        self._selected = False
        self.inner = tk.Frame(self, bg=_resolve(bg), padx=padx, pady=pady)
        style(self.inner, bg=bg)
        self._win = self.create_window(self._bw, self._bw,
                                       window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._fit)
        self.bind("<Configure>", lambda e: self._redraw())
        self._on_theme = self._redraw
        self._redraw()

    def _fit(self, _e=None):
        try:
            h = self.inner.winfo_reqheight() + self._bw * 2
            if h > 2 and abs(h - self.winfo_height()) > 1:
                self.configure(height=h)
        except Exception:
            pass

    def _redraw(self):
        try:
            w, h = self.winfo_width(), self.winfo_height()
            if w < 3 or h < 3:
                return
            self.configure(bg=_resolve(self._surround_role))
            self.itemconfigure(self._win, width=max(1, w - self._bw * 2))
            self.delete("card_bg")
            bw = 2 if self._selected else self._bw
            border = _resolve("accent" if self._selected else self._border_role)
            _rounded_rect(self, 0, 0, w, h, self._radius,
                          _resolve(self._bg_role), tag="card_bg")
            _rounded_rect(self, bw / 2, bw / 2, w - bw / 2, h - bw / 2,
                          max(1, self._radius - bw / 2), "",
                          outline=border, width=bw, tag="card_bg")
            self.tag_lower("card_bg")
        except Exception:
            pass

    def set_selected(self, on):
        self._selected = bool(on)
        self._redraw()


def rounded_entry(parent, textvariable=None, font_size=13, show="", radius=8,
                  bg="card"):
    """圆角输入框：返回 (外壳, Entry)，布局时请 pack/grid 外壳。"""
    card = RoundedCard(parent, radius=radius, bg=bg, border="border",
                       padx=8, pady=4)
    ent = tk.Entry(card.inner, textvariable=textvariable, bg=C(bg),
                   fg=C("text"), insertbackground=C("text"), relief="flat",
                   font=F(font_size), bd=0, highlightthickness=0,
                   disabledbackground=C(bg),
                   disabledforeground=C("muted"),
                   readonlybackground=C(bg),
                   show=show or "")
    style(ent, bg=bg, fg="text", insert_bg="text", select_bg="accent",
          select_fg="#1e1e2e")
    ent.pack(fill="x", expand=True)
    card.bind("<Button-1>", lambda _e: ent.focus_set())
    card.inner.bind("<Button-1>", lambda _e: ent.focus_set())
    ent.bind("<FocusIn>", lambda _e: card.set_selected(True))
    ent.bind("<FocusOut>", lambda _e: card.set_selected(False))
    card.entry = ent
    return card, ent


class IconButton(tk.Frame):
    """图标 + 文字的小按钮（用于标题栏、工具栏）。"""

    def __init__(self, parent, icon=None, text="", command=None, bg="panel",
                 fg="text2", hover="card", size=30, icon_size=14, font_size=12):
        super().__init__(parent, bg=_resolve(bg), height=size, cursor="hand2")
        self._bg_role, self._hover_role = bg, hover
        self._command = command
        self._enabled = True
        style(self, bg=bg)
        self.pack_propagate(False)
        if icon:
            self.icon = IconCanvas(self, icon, fg, icon_size, bg=bg)
            self.icon.pack(side="left", padx=(8, 2))
        if text:
            self.tk_label = tk.Label(self, text=text, bg=_resolve(bg), fg=_resolve(fg),
                                     font=F(font_size))
            style(self.tk_label, bg=bg, fg=fg)
            self.tk_label.pack(side="left", padx=(0 if icon else 6, 8))
        for w in (self, getattr(self, "icon", None), getattr(self, "tk_label", None)):
            if w is not None:
                w.bind("<Button-1>", self._click)
                w.bind("<Enter>", lambda e: self._hover(True))
                w.bind("<Leave>", lambda e: self._hover(False))

    def _hover(self, ok):
        if not self._enabled:
            return
        col = _resolve(self._hover_role if ok else self._bg_role)
        self.configure(bg=col)
        if hasattr(self, "tk_label"):
            self.tk_label.configure(bg=col)
        if hasattr(self, "icon"):
            self.icon.configure(bg=col)

    def _click(self, _):
        if self._enabled and self._command:
            self._command()


class CheckRow(tk.Frame):
    def __init__(self, parent, text="", checked=False, command=None, bg="bg",
                 font_size=13, fg="text"):
        super().__init__(parent, bg=_resolve(bg))
        self._bg_role = bg
        style(self, bg=bg)
        self.var = tk.BooleanVar(value=bool(checked))
        self.cb = tk.Checkbutton(self, text=text, variable=self.var,
                                 bg=_resolve(bg), fg=_resolve(fg), font=F(font_size),
                                 activebackground=_resolve(bg),
                                 activeforeground=_resolve(fg),
                                 selectcolor=_resolve("card"),
                                 highlightthickness=0, bd=0, anchor="w",
                                 padx=0, command=self._changed)
        style(self.cb, bg=bg, fg=fg)
        self.cb.pack(side="left")
        self._command = command

    def _changed(self):
        if self._command:
            self._command()

    def isChecked(self):
        return bool(self.var.get())

    def setChecked(self, ok):
        self.var.set(bool(ok))

    def _on_theme(self):
        self.configure(bg=_resolve(self._bg_role))
        self.cb.configure(bg=_resolve(self._bg_role),
                          activebackground=_resolve(self._bg_role),
                          selectcolor=_resolve("card"))


class LogText(tk.Text):
    def __init__(self, parent, bg="log_bg", fg="log_fg", height=10):
        super().__init__(parent, bg=_resolve(bg), fg=_resolve(fg), font=F(11, mono=True),
                         bd=0, highlightthickness=1, highlightbackground=_resolve("border"),
                         relief="flat", wrap="word", state="disabled", padx=8, pady=6)
        self._bg_role, self._fg_role = bg, fg
        self._on_theme = self._theme
        self.tag_config("info", foreground=_resolve("text2"))
        self.tag_config("ok", foreground=_resolve("log_fg"))
        self.tag_config("err", foreground=_resolve("red1"))
        self.tag_config("warn", foreground=_resolve("yellow"))
        self.tag_config("accent", foreground=_resolve("accent"))

    def _theme(self):
        self.configure(bg=_resolve(self._bg_role), fg=_resolve(self._fg_role),
                       highlightbackground=_resolve("border"))
        self.tag_config("info", foreground=_resolve("text2"))
        self.tag_config("ok", foreground=_resolve("log_fg"))
        self.tag_config("err", foreground=_resolve("red1"))
        self.tag_config("warn", foreground=_resolve("yellow"))
        self.tag_config("accent", foreground=_resolve("accent"))

    def append(self, text, tag=None):
        self.configure(state="normal")
        self.insert("end", str(text) + "\n", tag or ())
        self.see("end")
        self.configure(state="disabled")

    def replace_last_line(self, text, tag="accent"):
        self.configure(state="normal")
        line_start = self.index("end-1c linestart")
        old = self.get(line_start, "end-1c")
        prefix = ""
        idx = old.find("] ")
        if old.startswith("[") and idx > 0:
            prefix = old[:idx + 2]
        self.delete(line_start, "end-1c")
        self.insert("end", prefix + text, tag)
        self.see("end")
        self.configure(state="disabled")

    def clear(self):
        self.configure(state="normal")
        self.delete("1.0", "end")
        self.configure(state="disabled")


class Page(tk.Frame):
    def __init__(self, parent, app=None, bg="bg"):
        super().__init__(parent, bg=_resolve(bg))
        self.app = app
        self._bg_role = bg
        style(self, bg=bg)

    def _on_theme(self):
        self.configure(bg=_resolve(self._bg_role))

# ============ 通用对话框 ============
class BaseDialog(tk.Toplevel):
    def __init__(self, parent, title, width=430, height=None):
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.configure(bg=C("bg"))
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        self._body = tk.Frame(self, bg=C("bg"))
        self._body.pack(fill="both", expand=True, padx=18, pady=14)
        self._width = width
        self._height = height
        self._parent = parent

    def show(self):
        self._center()
        self.deiconify()
        try:
            self.grab_set()
            self.focus_force()
        except Exception:
            pass
        self.wait_window()
        return self.result

    def _center(self):
        self.update_idletasks()
        w = self._width or self.winfo_reqwidth()
        h = self._height or self.winfo_reqheight()
        try:
            px, py = self._parent.winfo_rootx(), self._parent.winfo_rooty()
            pw, ph = self._parent.winfo_width(), self._parent.winfo_height()
            x = px + (pw - w) // 2
            y = py + (ph - h) // 3
        except Exception:
            x = y = 100
        self.geometry("%dx%d+%d+%d" % (w, h, max(0, x), max(0, y)))

    def button_row(self, buttons):
        row = tk.Frame(self._body, bg=C("bg"))
        row.pack(side="bottom", fill="x", pady=(14, 0))
        inner = tk.Frame(row, bg=C("bg"))
        inner.pack(side="right")
        for text, cb, primary in buttons:
            if primary:
                b = AccentButton(inner, text, cb, width=110, height=34, font_size=13, bg_role="bg")
            else:
                b = FlatButton(inner, text, cb, bg="border", fg="text", hover="border2",
                               padx=18, pady=6)
            b.pack(side="left", padx=(6, 0))
        return row

    def add_label(self, text, fg="text", size=13, bold=False, wrap=True):
        lb = tk.Label(self._body, text=text, bg=C("bg"), fg=C(fg), font=F(size, bold),
                      justify="left", anchor="w")
        if wrap:
            lb.configure(wraplength=self._width - 60)
        lb.pack(fill="x", pady=(0, 8))
        return lb


def _simple_message(parent, title, msg, fg="text"):
    d = BaseDialog(parent, title, width=430)
    d.add_label(msg, fg=fg, size=13)
    d.button_row([("确定", lambda: (setattr(d, "result", True), d.destroy()), True)])
    return d.show()


def alert(parent, title, msg, kind="info"):
    fg = {"info": "text", "warn": "yellow", "error": "red1"}.get(kind, "text")
    return _simple_message(parent, title, msg, fg)


def confirm(parent, title, msg, danger=False):
    d = BaseDialog(parent, title, width=460)
    d.add_label(msg, fg="red1" if danger else "text", size=13)
    def _yes():
        d.result = True; d.destroy()
    def _no():
        d.result = False; d.destroy()
    d.button_row([(_t("取消"), _no, False), (_t("确定"), _yes, True)])
    return bool(d.show())


def ask_text(parent, title, prompt, initial=""):
    d = BaseDialog(parent, title, width=430)
    d.add_label(prompt, size=13, wrap=False)
    var = tk.StringVar(value=initial)
    ewrap, ent = rounded_entry(d._body, textvariable=var, font_size=13)
    ewrap.pack(fill="x", pady=(0, 8))
    ent.focus_set()
    try:
        ent.selection_range(0, "end")
        ent.icursor("end")
    except Exception:
        pass
    def _ok(_=None):
        d.result = var.get(); d.destroy()
    def _cancel():
        d.result = None; d.destroy()
    ent.bind("<Return>", _ok)
    ent.bind("<Escape>", lambda e: _cancel())
    d.button_row([(_t("取消"), _cancel, False), (_t("确定"), _ok, True)])
    d.show()
    return d.result


def ask_multi_choice(parent, title, prompt, items, checked=True):
    """多选对话框，返回被选中项的下标列表；取消返回 None。"""
    d = BaseDialog(parent, title, width=470)
    d.add_label(prompt, size=13)
    wrap = ScrollFrame(d._body, bg="bg")
    wrap.pack(fill="both", expand=True, pady=(0, 8))
    wrap.canvas.configure(height=min(210, 30 * len(items) + 8))
    rows = []
    for it in items:
        row = CheckRow(wrap.inner, it, checked=checked, bg="bg")
        row.pack(fill="x", pady=2)
        rows.append(row)

    def _ok():
        d.result = [i for i, r in enumerate(rows) if r.isChecked()]
        d.destroy()

    d.button_row([(_t("取消"), lambda: (setattr(d, "result", None), d.destroy()), False),
                  (_t("确定"), _ok, True)])
    d.show()
    return d.result


def merge_thirdparty_accounts(targets, token, api, injector):
    """把 (玩家名, UUID) 列表写入账号文件，返回 (新增, 已存在跳过)。"""
    accs = load_accounts().setdefault("accounts", [])
    added, skipped = [], []
    for player_name, uuid_val in targets:
        if any(a.get("name") == player_name for a in accs):
            skipped.append(player_name)
            continue
        accs.append({"name": player_name, "type": "thirdparty",
                     "uuid": uuid_val or offline_uuid(player_name),
                     "token": token or "0", "api": api, "injector": injector})
        added.append(player_name)
    if added:
        save_accounts({"accounts": accs})
    return added, skipped


class TextInputDialog(BaseDialog):
    """第三方登录等多字段表单。"""

    def __init__(self, parent, title, width=440):
        super().__init__(parent, title, width=width)
        self.vars = {}
        self.widgets = {}

    def add_field(self, key, label, initial="", password=False, placeholder=""):
        lb = tk.Label(self._body, text=label, bg=C("bg"), fg=C("text"), font=F(12),
                      anchor="w")
        lb.pack(fill="x", pady=(4, 2))
        var = tk.StringVar(value=initial)
        ewrap, ent = rounded_entry(self._body, textvariable=var, font_size=13,
                                   show="*" if password else "")
        ewrap.pack(fill="x")
        self.vars[key] = var
        self.widgets[key] = ent
        return ent

    def add_check(self, key, text, checked=False):
        var = tk.BooleanVar(value=checked)
        cb = tk.Checkbutton(self._body, text=text, variable=var, bg=C("bg"),
                            fg=C("text"), activebackground=C("bg"),
                            activeforeground=C("text"), selectcolor=C("card"),
                            font=F(12), anchor="w", highlightthickness=0, bd=0)
        cb.pack(fill="x", pady=(4, 0))
        self.vars[key] = var
        return cb

    def value(self, key):
        return self.vars[key].get()

# ============ 游戏窗口标题 ============
def render_game_window_title(template, values):
    """把 {player} 等占位符替换为实际值，未知占位符保持原样。"""
    text = template or ""
    for key, val in (values or {}).items():
        text = text.replace("{" + key + "}", str(val))
    return text


def apply_game_window_title(pid, title):
    """启动后把该游戏进程的窗口标题改为指定文本（仅 Windows 生效）。"""
    if sys.platform != "win32" or not pid or not title:
        return

    def _worker():
        try:
            import ctypes
            from ctypes import wintypes
            u32 = ctypes.windll.user32
            u32.GetWindowThreadProcessId.argtypes = [
                wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
            u32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
            find_cb = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                         wintypes.LPARAM)
            found = []

            def _collect(hwnd, _lparam):
                proc_id = wintypes.DWORD()
                u32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
                if (proc_id.value == pid and u32.IsWindowVisible(hwnd)
                        and u32.GetWindowTextLengthW(hwnd) > 0):
                    found.append(hwnd)
                return True

            cb = find_cb(_collect)
            deadline = time.time() + 90
            set_at = None
            while time.time() < deadline:
                found.clear()
                try:
                    u32.EnumWindows(cb, 0)
                except Exception:
                    break
                if found:
                    for hwnd in found:
                        buf = ctypes.create_unicode_buffer(512)
                        u32.GetWindowTextW(hwnd, buf, 512)
                        if buf.value != title:
                            u32.SetWindowTextW(hwnd, title)
                    if set_at is None:
                        set_at = time.time()
                    elif time.time() - set_at > 10:
                        # 标题已稳定生效，短暂守护后退出，避免长期占用线程
                        break
                time.sleep(1.0)
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


# ============ 账号头像 ============
AVATAR_SIZE = 40
_AVATAR_MEM = {}
_AVATAR_PENDING = set()


def _account_type_text(acc):
    typ = acc.get("type", "offline")
    if typ == "microsoft":
        return _t("正版")
    if typ == "thirdparty":
        api = str(acc.get("api") or "").lower()
        if "littleskin" in api:
            return _t("第三方") + " · LittleSkin"
        return _t("第三方")
    return _t("离线")


def _avatar_key(acc):
    uid = (acc.get("uuid") or acc.get("name") or "").replace("-", "").lower()
    return "%s_%s" % (acc.get("type", "offline"), uid or "default")


def _avatar_cache_file(acc):
    folder = _dp(os.path.join("cache", "avatars"))
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception:
        pass
    safe = re.sub(r"[^0-9a-zA-Z_.-]", "_", _avatar_key(acc))[:90]
    return os.path.join(folder, safe + ".png")


def _draw_barrier_avatar(cv, size=AVATAR_SIZE):
    """离线账号头像：MC 屏障方块样式（禁止符号）。"""
    try:
        cv.delete("all")
        _rounded_rect(cv, 0.5, 0.5, size - 0.5, size - 0.5, 7,
                      "#3a4050", outline="#4c566a")
        r = size * 0.29
        cx = cy = size / 2.0
        cv.create_oval(cx - r, cy - r, cx + r, cy + r,
                       outline="#c05c5c", width=3)
        d = r * 0.62
        cv.create_line(cx - d, cy - d, cx + d, cy + d, fill="#c05c5c", width=3)
    except Exception:
        pass


def _skin_pixel(img, x, y):
    """兼容 Tk 返回 #rrggbb 或 (r, g, b) 两种形式。"""
    try:
        v = img.get(x, y)
    except Exception:
        return ""
    if isinstance(v, (tuple, list)):
        try:
            if len(v) >= 3:
                return "#%02x%02x%02x" % (int(v[0]), int(v[1]), int(v[2]))
            if len(v) == 1:
                c = int(v[0])
                return "#%02x%02x%02x" % (c, c, c)
        except Exception:
            return ""
        return ""
    return str(v or "")


def _skin_transparent(img, x, y):
    try:
        return bool(img.transparency_get(x, y))
    except Exception:
        return False


def _face_from_skin(img, master=None, size=AVATAR_SIZE):
    """从完整皮肤图中裁出 8x8 面部并放大，叠加帽子层。"""
    face = tk.PhotoImage(width=size, height=size, master=master)
    scale = max(1, size // 8)
    for y in range(8):
        for x in range(8):
            color = _skin_pixel(img, 8 + x, 8 + y)
            if color:
                face.put(color, to=(x * scale, y * scale,
                                    (x + 1) * scale, (y + 1) * scale))
    try:
        has_hat = img.width() >= 56
    except Exception:
        has_hat = False
    if has_hat:
        for y in range(8):
            for x in range(8):
                if _skin_transparent(img, 40 + x, 8 + y):
                    continue
                color = _skin_pixel(img, 40 + x, 8 + y)
                if color:
                    face.put(color, to=(x * scale, y * scale,
                                        (x + 1) * scale, (y + 1) * scale))
    return face


def _avatar_photo_from_bytes(data, kind, master=None):
    import base64
    img = tk.PhotoImage(data=base64.b64encode(data), format="png", master=master)
    if kind == "head":
        if img.width() == AVATAR_SIZE and img.height() == AVATAR_SIZE:
            return img
        return None
    return _face_from_skin(img, master)


def _avatar_photo_cached(acc, master=None):
    key = _avatar_key(acc)
    photo = _AVATAR_MEM.get(key)
    if photo is not None:
        return photo
    path = _avatar_cache_file(acc)
    if os.path.isfile(path):
        try:
            photo = tk.PhotoImage(file=path, master=master)
            _AVATAR_MEM[key] = photo
            return photo
        except Exception:
            pass
    return None


def _load_remote_avatar(acc):
    """后台获取头像：返回 (kind, png bytes, 缓存文件) 或 None。"""
    import requests
    typ = acc.get("type", "offline")
    uid = (acc.get("uuid") or "").replace("-", "").lower()
    if not uid or typ not in ("microsoft", "thirdparty"):
        return None
    if typ == "microsoft":
        for url in ("https://crafatar.com/avatars/%s?size=%d&overlay"
                    % (uid, AVATAR_SIZE),
                    "https://mc-heads.net/avatar/%s/%d" % (uid, AVATAR_SIZE)):
            try:
                r = requests.get(url, timeout=8)
                if r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n":
                    return ("head", r.content, _avatar_cache_file(acc))
            except Exception:
                continue
        return None
    base = str(acc.get("api") or "").rstrip("/")
    if not base:
        return None
    try:
        r = requests.get("%s/sessionserver/session/minecraft/profile/%s"
                         % (base, uid), timeout=8)
        if r.status_code != 200:
            return None
        props = r.json().get("properties") or []
        value = props[0].get("value") if props else ""
        if not value:
            return None
        import base64
        meta = json.loads(base64.b64decode(value).decode("utf-8"))
        skin_url = ((((meta.get("textures") or {}).get("SKIN") or {})
                     .get("url")) or "")
        if not skin_url:
            return None
        sr = requests.get(skin_url, timeout=10)
        if sr.status_code == 200 and sr.content[:8] == b"\x89PNG\r\n\x1a\n":
            return ("skin", sr.content, _avatar_cache_file(acc))
    except Exception:
        return None
    return None


# ============ 启动游戏 ============
class LaunchPage(Page):

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.config = load_config()
        self.accounts_data = load_accounts()
        self.versions_data = load_versions()
        self.selected_account = None
        self.selected_version = None
        self._procs = {}
        self._proc_seq = 0
        self._proc_keys = ["main"]
        self._active_key = "main"
        self._starting_key = None
        self._force_java = ""
        self.on_java_missing = None
        self._ms_dialog = None
        self._refreshing = False
        self._build_ui()
        self.refresh_accounts()
        self.refresh_versions()
        self._update_info()
        self._scan_job = self.after(2000, self._periodic_versions)

    # ---------- 界面 ----------
    def _build_ui(self):
        root = tk.Frame(self, bg=C("bg"))
        root.pack(fill="both", expand=True, padx=16, pady=(10, 14))
        root.columnconfigure(0, weight=1, uniform="col")
        root.columnconfigure(1, weight=1, uniform="col")
        root.columnconfigure(2, weight=2)
        root.rowconfigure(0, weight=1)

        # ---- 账号列 ----
        left = tk.Frame(root, bg=C("bg"))
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        head = tk.Frame(left, bg=C("bg")); head.pack(fill="x")
        tk.Label(head, text=_t("账号"), bg=C("bg"), fg=C("text"),
                 font=F(15, True)).pack(side="left")
        IconButton(head, "x", "", self.delete_selected_account, bg="bg",
                   fg="red2", hover="panel", size=26, icon_size=13).pack(side="right")
        self.acc_scroll = ScrollFrame(left, bg="bg")
        self.acc_scroll.pack(fill="both", expand=True, pady=(6, 8))
        self.account_cards = self.acc_scroll.inner
        self._account_entries = []
        for txt, cb in ((_t("添加正版账号"), self.add_microsoft_account),
                        (_t("第三方登录"), self.third_party_login),
                        (_t("添加离线账号"), self.add_offline_account)):
            FlatButton(left, txt, cb, bg="card", fg="text", hover="border",
                       padx=6, pady=8, font_size=12).pack(fill="x", pady=2)

        # ---- 版本列 ----
        mid = tk.Frame(root, bg=C("bg"))
        mid.grid(row=0, column=1, sticky="nsew", padx=(0, 12))
        mh = tk.Frame(mid, bg=C("bg")); mh.pack(fill="x")
        tk.Label(mh, text=_t("版本"), bg=C("bg"), fg=C("text"),
                 font=F(15, True)).pack(side="left")
        IconButton(mh, "gear", "", self.refresh_versions, bg="bg", fg="accent",
                   hover="panel", size=26, icon_size=13).pack(side="right")
        ver_wrap, self.version_list = self._make_listbox(mid)
        ver_wrap.pack(fill="both", expand=True, pady=(6, 0))
        self.version_list.bind("<<ListboxSelect>>", self._on_ver_click)

        # ---- 右侧 ----
        right = tk.Frame(root, bg=C("bg"))
        right.grid(row=0, column=2, sticky="nsew")
        rh = tk.Frame(right, bg=C("bg")); rh.pack(fill="x")
        tk.Label(rh, text=_t("启动游戏"), bg=C("bg"), fg=C("text"),
                 font=F(24, True)).pack(side="left")
        self.stop_all_btn = FlatButton(rh, _t("停止全部"), self.stop_all_games,
                                       bg="card", fg="red1", hover="border",
                                       padx=14, pady=4, font_size=12)
        self.stop_all_btn.pack(side="right")
        self.account_info = tk.Label(right, text=_t("账号：未选择"), bg=C("bg"),
                                     fg=C("muted"), font=F(12), anchor="w")
        self.account_info.pack(fill="x", pady=(4, 0))
        self.version_info = tk.Label(right, text=_t("游戏版本：未选择"), bg=C("bg"),
                                     fg=C("muted"), font=F(12), anchor="w")
        self.version_info.pack(fill="x")
        self.launch_btn = AccentButton(right, _t("启动游戏"), self.toggle_launch,
                                       width=400, height=76, font_size=20)
        self.launch_btn.pack(fill="x", pady=(10, 8))

        prow = tk.Frame(right, bg=C("bg")); prow.pack(fill="x", pady=(2, 0))
        tk.Label(prow, text=_t("请选择进程"), bg=C("bg"), fg=C("muted"),
                 font=F(11)).pack(side="left")
        self.proc_combo = DropDown(prow, [_t("尚未启动游戏")], width=180,
                                   command=self._on_proc_changed, bg="card")
        self.proc_combo.pack(side="left", padx=6)
        FlatButton(prow, _t("清除已关闭"), self.clear_closed_procs, bg="card",
                   fg="text2", hover="border", padx=10, pady=3,
                   font_size=11).pack(side="right", padx=(4, 0))
        FlatButton(prow, _t("清空"), self.clear_current_log, bg="card",
                   fg="text2", hover="border", padx=10, pady=3,
                   font_size=11).pack(side="right")

        self.log_holder = tk.Frame(right, bg=C("log_bg"))
        self.log_holder.pack(fill="both", expand=True, pady=(6, 0))
        # 异常退出操作条：默认隐藏，仅当前输出区对应进程异常退出时显示
        self.log_actions = tk.Frame(right, bg=C("bg"))
        self._log_actions_shown = False
        self.log_action_lbl = tk.Label(
            self.log_actions, text=_t("检测到游戏异常退出"), bg=C("bg"),
            fg=C("red1"), font=F(12, True), anchor="w")
        self.log_action_lbl.pack(side="left")
        AccentButton(self.log_actions, _t("智能分析"), self._analyze_active,
                     width=110, height=32, font_size=12).pack(
            side="right", padx=(8, 0))
        FlatButton(self.log_actions, _t("打开日志"), self._open_active_log,
                   bg="card", fg="accent", hover="border", padx=14, pady=6,
                   font_size=12).pack(side="right")
        self._create_log_page("main", _t("尚未启动游戏"), None)
        self._active_key = "main"

    def _make_listbox(self, parent):
        wrap = tk.Frame(parent, bg=C("border"))
        lb = tk.Listbox(wrap, bg=C("panel"), fg=C("text"),
                        selectbackground=C("card"), selectforeground=C("text"),
                        font=F(12), bd=0, highlightthickness=0,
                        activestyle="none", exportselection=False)
        sb = _mk_scrollbar(wrap, lb.yview)
        lb.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        lb.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        lb._lcl_wrap = wrap
        return wrap, lb

    def _refresh_list_theme(self):
        lb = self.version_list
        lb.configure(bg=C("panel"), fg=C("text"),
                     selectbackground=C("card"), selectforeground=C("text"))
        for entry in getattr(self, "_account_entries", []):
            self._paint_account_entry(entry, entry.get("selected", False))

    def _on_theme(self):
        Page._on_theme(self)
        try:
            self._refresh_list_theme()
            self.account_info.configure(bg=C("bg"), fg=C("muted"))
            self.version_info.configure(bg=C("bg"), fg=C("muted"))
            if getattr(self, "log_actions", None) is not None:
                self.log_actions.configure(bg=C("bg"))
                self.log_action_lbl.configure(bg=C("bg"), fg=C("red1"))
        except Exception:
            pass

    # ---------- 账号 ----------
    def refresh_accounts(self):
        self._refreshing = True
        try:
            self.accounts_data = load_accounts()
            for w in self.account_cards.winfo_children():
                w.destroy()
            self._account_entries = []
            cur = load_config().get("selected_account", "")
            self.selected_account = None
            for acc in self.accounts_data.get("accounts", []):
                selected = acc.get("name") == cur
                entry = self._build_account_card(acc, selected)
                self._account_entries.append(entry)
                if selected:
                    self.selected_account = acc
        finally:
            self._refreshing = False

    @staticmethod
    def _bind_card_events(widget, on_click):
        widget.bind("<Button-1>", on_click)
        widget.configure(cursor="hand2")

    def _build_account_card(self, acc, selected):
        bg_role = "card" if selected else "panel"
        card = tk.Frame(self.account_cards, bg=C(bg_role), highlightthickness=1,
                        highlightbackground=C("border"),
                        highlightcolor=C("accent"), cursor="hand2")
        card.pack(fill="x", padx=2, pady=3)
        holder = tk.Frame(card, bg=C(bg_role), width=AVATAR_SIZE + 4,
                          height=AVATAR_SIZE + 4)
        holder.pack(side="left", padx=(7, 9), pady=7)
        holder.pack_propagate(False)
        info = tk.Frame(card, bg=C(bg_role))
        info.pack(side="left", fill="both", expand=True, pady=7)
        name_lb = tk.Label(info, text=acc.get("name", "?"), bg=C(bg_role),
                           fg=C("text"), font=F(13, True), anchor="w")
        name_lb.pack(fill="x")
        type_lb = tk.Label(info, text=_account_type_text(acc), bg=C(bg_role),
                           fg=C("muted"), font=F(11), anchor="w")
        type_lb.pack(fill="x", pady=(2, 0))
        entry = {"acc": acc, "card": card, "holder": holder, "info": info,
                 "name": name_lb, "type": type_lb, "avatar": None,
                 "selected": selected, "bg_role": bg_role}
        entry["avatar"] = self._fill_avatar(holder, acc, bg_role)
        for w in (card, holder, info, name_lb, type_lb):
            self._bind_card_events(
                w, lambda e, a=acc: self._select_account(a))
            self._bind_card_wheel(w)
        if entry["avatar"] is not None:
            self._bind_card_events(
                entry["avatar"], lambda e, a=acc: self._select_account(a))
            self._bind_card_wheel(entry["avatar"])
        self._paint_account_entry(entry, selected)
        return entry

    def _bind_card_wheel(self, widget):
        cv = self.acc_scroll.canvas
        widget.bind("<MouseWheel>",
                    lambda e: cv.yview_scroll(
                        -1 if getattr(e, "delta", 0) > 0 else 1, "units"))
        widget.bind("<Button-4>", lambda e: cv.yview_scroll(-1, "units"))
        widget.bind("<Button-5>", lambda e: cv.yview_scroll(1, "units"))

    def _fill_avatar(self, holder, acc, bg_role):
        photo = _avatar_photo_cached(acc, holder)
        if photo is not None:
            lbl = tk.Label(holder, image=photo, bg=C(bg_role), bd=0,
                           highlightthickness=0)
            lbl.image = photo
            lbl.pack(fill="both", expand=True)
            return lbl
        cv = tk.Canvas(holder, width=AVATAR_SIZE, height=AVATAR_SIZE,
                       bg=C(bg_role), highlightthickness=0, bd=0)
        cv.pack(fill="both", expand=True, pady=2)
        _draw_barrier_avatar(cv, AVATAR_SIZE)
        if (acc.get("type") in ("microsoft", "thirdparty")
                and (acc.get("uuid") or "")):
            self._async_avatar(acc, holder)
        return cv

    def _async_avatar(self, acc, holder):
        key = _avatar_key(acc)
        if key in _AVATAR_PENDING:
            return
        _AVATAR_PENDING.add(key)

        def done(res):
            _AVATAR_PENDING.discard(key)
            if not res:
                return
            try:
                if not holder.winfo_exists():
                    return
                kind, data, path = res
                img = _avatar_photo_from_bytes(data, kind, master=holder)
                if img is None:
                    return
                if path and kind == "head":
                    try:
                        with open(path, "wb") as f:
                            f.write(data)
                    except Exception:
                        pass
                for w in holder.winfo_children():
                    w.destroy()
                lbl = tk.Label(holder, image=img, bg=C("panel"), bd=0,
                               highlightthickness=0)
                lbl.image = img
                lbl.pack(fill="both", expand=True)
                _AVATAR_MEM[key] = img
                for entry in self._account_entries:
                    if entry.get("holder") is holder:
                        entry["avatar"] = lbl
                        self._paint_account_entry(entry,
                                                  entry.get("selected", False))
                        break
                self._bind_card_events(
                    lbl, lambda e, a=acc: self._select_account(a))
                self._bind_card_wheel(lbl)
                if path and kind == "skin":
                    try:
                        img.write(path, format="png")
                    except Exception:
                        pass
            except Exception:
                pass

        _run_bg(lambda: _load_remote_avatar(acc), done)

    def _paint_account_entry(self, entry, selected):
        entry["selected"] = bool(selected)
        bg_role = "card" if selected else "panel"
        entry["bg_role"] = bg_role
        card = entry["card"]
        try:
            card.configure(bg=C(bg_role),
                           highlightbackground=C("accent") if selected else C("border"),
                           highlightthickness=2 if selected else 1)
            for w in (entry["holder"], entry["info"], entry["name"],
                      entry["type"]):
                w.configure(bg=C(bg_role))
            if entry["avatar"] is not None:
                entry["avatar"].configure(bg=C(bg_role))
            entry["type"].configure(fg=C("accent") if selected else C("muted"))
            entry["name"].configure(fg=C("text"))
        except Exception:
            pass

    def _account_exists(self, name):
        return any(a.get("name") == name for a in self.accounts_data.get("accounts", []))

    def _select_account(self, acc):
        if self._refreshing:
            return
        self.selected_account = acc
        for entry in self._account_entries:
            self._paint_account_entry(
                entry, entry["acc"].get("name") == acc.get("name"))
        update_config(selected_account=acc.get("name", ""))
        self._update_info()

    def delete_selected_account(self):
        if not self.selected_account:
            alert(self, _t("提示"), _t("请先在账号列表中选择一个账号。"))
            return
        name = self.selected_account.get("name", "")
        if not confirm(self, _t("删除账号"),
                       _t("确定删除账号“%s”吗？") % name, danger=True):
            return
        self.accounts_data["accounts"] = [
            a for a in self.accounts_data.get("accounts", [])
            if a.get("name") != name]
        save_accounts(self.accounts_data)
        if load_config().get("selected_account") == name:
            update_config(selected_account="")
        self.selected_account = None
        self.refresh_accounts()
        self._update_info()

    def add_offline_account(self):
        name = ask_text(self, _t("添加离线账号"), _t("用户名："))
        if not name or not name.strip():
            return
        name = name.strip()
        if self._account_exists(name):
            alert(self, _t("提示"), _t("账号“%s”已存在。") % name, "warn")
            return
        self.accounts_data.setdefault("accounts", []).append(
            {"name": name, "type": "offline",
             "uuid": offline_uuid(name), "token": "0"})
        save_accounts(self.accounts_data)
        self.refresh_accounts()

    def add_microsoft_account(self):
        cfg = load_config()
        client_id = effective_microsoft_client_id(cfg)
        if self._ms_dialog is not None:
            try:
                self._ms_dialog.lift()
            except Exception:
                pass
            return
        dlg = MicrosoftLoginDialog(self, client_id)
        self._ms_dialog = dlg
        acc = dlg.show()
        self._ms_dialog = None
        if not acc:
            return
        if self._account_exists(acc["name"]):
            alert(self, _t("提示"), _t("账号“%s”已存在。") % acc["name"], "warn")
            return
        self.accounts_data.setdefault("accounts", []).append(acc)
        save_accounts(self.accounts_data)
        self.refresh_accounts()
        alert(self, _t("登录成功"),
              _t("已添加正版账号：%s\n请在左侧列表中点击选中它。") % acc["name"])

    def _yggdrasil_authenticate(self, api_base, username, password, profile_id=None):
        import requests
        url = api_base.rstrip("/") + "/authserver/authenticate"
        payload = {
            "agent": {"name": "Minecraft", "version": 1},
            "username": username, "password": password,
            "clientToken": os.urandom(16).hex(),
            "requestUser": False,
        }
        if profile_id:
            payload["selectedProfile"] = {"id": profile_id}
        r = requests.post(url, json=payload, timeout=15)
        r.raise_for_status()
        data = r.json()
        profiles = []
        for p in data.get("availableProfiles") or []:
            if isinstance(p, dict) and p.get("id"):
                profiles.append({"id": p["id"], "name": p.get("name", "")})
        selected = {}
        sp = data.get("selectedProfile")
        if isinstance(sp, dict) and sp.get("id"):
            selected = {"id": sp["id"], "name": sp.get("name", "")}
            if not profiles:
                profiles = [selected]
        return (data.get("accessToken", ""), payload["clientToken"],
                profiles, selected)

    def third_party_login(self):
        cfg = load_config()
        d = TextInputDialog(self, _t("第三方登录"), width=440)
        d.add_field("api", _t("API 地址："),
                    cfg.get("api_config", {}).get("base_url", ""))
        d.add_field("user", _t("用户名："))
        d.add_field("pwd", _t("密码（仅用于本次认证，不保存）："), password=True)
        d.add_field("injector", _t("authlib-injector JAR 路径（可选）："),
                    cfg.get("authlib_injector_path", ""))
        d.add_check("ls", _t("设为 LittleSkin，自动填写信息"))
        status = tk.Label(d._body, text="", bg=C("bg"), fg=C("red1"), font=F(12),
                          anchor="w")
        status.pack(fill="x", pady=(6, 0))

        def on_save():
            api_url = d.value("api").strip()
            if d.value("ls"):
                api_url = "https://littleskin.cn/api/yggdrasil"
                d.vars["api"].set(api_url)
            name = d.value("user").strip()
            if not name:
                status.configure(text=_t("请输入用户名。"))
                return
            pwd = d.value("pwd")
            token_val = "0"
            targets = [(name, "")]  # (玩家名, UUID)；未认证时按离线 UUID 处理
            if api_url and pwd:
                try:
                    token_val, _ct, profiles, _sel = \
                        self._yggdrasil_authenticate(api_url, name, pwd)
                    if not token_val:
                        status.configure(text=_t("认证返回数据不完整。"))
                        return
                    if profiles:
                        chosen = profiles
                        if len(profiles) > 1:
                            labels = []
                            for p in profiles:
                                pid = p.get("id", "")
                                lb = p.get("name") or pid or "?"
                                labels.append("%s (%s)" % (lb, pid[:8]) if pid else lb)
                            picked = ask_multi_choice(
                                d, _t("选择玩家角色"),
                                _t("账号“%s”下有多个玩家角色，可多选，"
                                   "每个角色会单独添加为一张卡片：") % name, labels)
                            if picked is None:
                                return
                            if not picked:
                                status.configure(text=_t("请至少选择一个玩家角色。"))
                                return
                            chosen = [profiles[i] for i in picked]
                        targets = [(p.get("name") or name, p.get("id", ""))
                                   for p in chosen]
                except Exception as e:
                    status.configure(text=_t("认证失败：") + _err_text(e))
                    return
            added, skipped = merge_thirdparty_accounts(
                targets, token_val, api_url, d.value("injector").strip())
            if not added:
                status.configure(text=_t("这些玩家角色已在列表中：%s")
                                 % "、".join(skipped))
                return
            update_config(api_config={"base_url": api_url,
                                      "is_littleskin": bool(d.value("ls"))},
                          authlib_injector_path=d.value("injector").strip())
            d.result = True
            d.destroy()
            self.refresh_accounts()
            msg = _t("已添加 %d 个玩家角色：%s") % (len(added), "、".join(added))
            if skipped:
                msg += "\n" + _t("已存在，已跳过：%s") % "、".join(skipped)
            alert(self, _t("第三方登录"), msg)

        d.button_row([(_t("取消"), lambda: d.destroy(), False),
                      (_t("保存"), on_save, True)])
        d.show()

    # ---------- 版本 ----------
    def _version_names(self):
        mc = load_config().get("minecraft_directory", "")
        names = set(load_versions().get("versions", []))
        try:
            import minecraft_launcher_lib
            names |= {v["id"] for v in
                      minecraft_launcher_lib.utils.get_installed_versions(mc)}
        except Exception:
            pass
        return sorted(names, key=_natural_version_key, reverse=True)

    def refresh_versions(self):
        self._refreshing = True
        try:
            self.versions_data = load_versions()
            cur = self.selected_version or load_config().get("selected_version", "")
            names = self._version_names()
            self.version_list.delete(0, "end")
            self.selected_version = None
            sel_idx = None
            for i, n in enumerate(names):
                self.version_list.insert("end", n)
                if n == cur:
                    sel_idx = i
                    self.selected_version = n
            self.version_list.selection_clear(0, "end")
            if sel_idx is not None:
                self.version_list.selection_set(sel_idx)
                self.version_list.see(sel_idx)
        finally:
            self._refreshing = False
        self._update_info()

    def _periodic_versions(self):
        try:
            names = set(self._version_names())
            cur = set(self.version_list.get(0, "end"))
            if names != cur:
                self.refresh_versions()
            self._scan_job = self.after(2000, self._periodic_versions)
        except Exception:
            pass

    def _on_ver_click(self, _=None):
        if self._refreshing:
            return
        sel = self.version_list.curselection()
        if not sel:
            return
        name = self.version_list.get(sel[0])
        self.selected_version = name
        self._refreshing = True
        try:
            update_config(selected_version=name)
        finally:
            self._refreshing = False
        self._update_info()

    def _update_info(self):
        acc = self.selected_account
        if acc:
            tm = {"offline": _t("离线"), "microsoft": _t("正版"),
                  "thirdparty": _t("第三方")}
            self.account_info.configure(
                text=_t("账号：") + "[%s] %s" % (
                    tm.get(acc.get("type", "offline"), _t("离线")),
                    acc.get("name", "?")))
        else:
            self.account_info.configure(text=_t("账号：未选择"))
        if self.selected_version:
            v = self.selected_version
            mp = (self.config.get("modpack_instances") or {}).get(v) or {}
            java_v = mp.get("mc_version") or v
            self.version_info.configure(
                text=_t("游戏版本：") + "%s   ·   %s%d" % (
                    v, _t("需要 Java "), required_java_major(java_v)))
        else:
            self.version_info.configure(text=_t("游戏版本：未选择"))

    # ---------- 日志 ----------
    @staticmethod
    def _proc_label(version, pid=None):
        if version is None:
            return _t("尚未启动游戏")
        if pid:
            return "%s  (PID %s)" % (version, pid)
        return "%s%s" % (version, _t("（启动中）"))

    def _create_log_page(self, key, label, version, game_dir=None):
        log = LogText(self.log_holder, height=10)
        entry = {"thread": None, "log": log, "version": version,
                 "label": label, "pid": None, "closed": False,
                 "code": None, "removed": False, "_prog_line": False,
                 "game_dir": game_dir, "abnormal": False, "cancelled": False,
                 "log_file": "", "_fp": None}
        # 每个进程单独落盘：供“打开日志”和“智能分析”读取完整输出
        if version is not None:
            try:
                entry["log_file"] = _new_launch_log_path(version or "game", key)
                fp = open(entry["log_file"], "a", encoding="utf-8",
                          errors="replace")
                entry["_fp"] = fp
                fp.write("# Lightning Craft Launcher 启动日志\n")
                fp.write("# 时间：%s\n" % datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"))
                fp.write("# 版本：%s\n" % version)
                if game_dir:
                    fp.write("# 游戏目录：%s\n" % game_dir)
                fp.write("# " + "-" * 56 + "\n")
                fp.flush()
            except Exception:
                pass
        self._procs[key] = entry
        self._show_log(key)
        self._sync_proc_combo()

    def _close_log_fp(self, entry):
        fp = (entry or {}).get("_fp")
        if fp is not None:
            try:
                fp.flush()
                fp.close()
            except Exception:
                pass
            entry["_fp"] = None

    def _show_log(self, key):
        entry = self._procs.get(key)
        if not entry:
            return
        for e in self._procs.values():
            try:
                e["log"].pack_forget()
            except Exception:
                pass
        entry["log"].pack(fill="both", expand=True)
        self._active_key = key
        keys = list(self._procs.keys())
        if key in keys:
            self.proc_combo.set_current_index(keys.index(key))
        self._refresh_log_actions()

    def _sync_proc_combo(self):
        keys = list(self._procs.keys())
        self._proc_keys = keys
        labels = []
        for k in keys:
            e = self._procs[k]
            labels.append(self._proc_label(e.get("version"), e.get("pid")))
        idx = keys.index(self._active_key) if self._active_key in keys else 0
        self.proc_combo.set_values(labels, idx)

    def _on_proc_changed(self, idx):
        keys = getattr(self, "_proc_keys", [])
        if 0 <= idx < len(keys):
            self._show_log(keys[idx])

    def _update_proc_item(self, key):
        entry = self._procs.get(key)
        if not entry:
            return
        self._sync_proc_combo()

    def _append_log(self, text, key=None):
        if key is None:
            key = self._active_key
        entry = self._procs.get(key) or self._procs.get("main")
        if not entry or entry.get("removed"):
            return
        entry["_prog_line"] = False
        ts = datetime.now().strftime("%H:%M:%S")
        entry["log"].append("[%s] %s" % (ts, text))
        fp = entry.get("_fp")
        if fp is not None:
            try:
                fp.write("[%s] %s\n" % (ts, text))
                fp.flush()
            except Exception:
                pass

    def _refresh_log_actions(self):
        """异常退出的两个按钮跟随当前输出区显示/隐藏。"""
        bar = getattr(self, "log_actions", None)
        if bar is None:
            return
        entry = self._procs.get(self._active_key) or {}
        show = bool(entry.get("abnormal")) and not entry.get("removed")
        try:
            if show:
                code = entry.get("code")
                self.log_action_lbl.configure(
                    text=_t("检测到游戏异常退出（错误码 %s）") % code)
                if not self._log_actions_shown:
                    bar.pack(fill="x", pady=(6, 0))
                    self._log_actions_shown = True
            elif self._log_actions_shown:
                bar.pack_forget()
                self._log_actions_shown = False
        except tk.TclError:
            pass

    def _open_active_log(self):
        entry = self._procs.get(self._active_key) or {}
        path = entry.get("log_file") or ""
        if not path or not os.path.isfile(path):
            alert(self, _t("打开日志"), _t("日志文件不存在。"), "warn")
            return
        _open_path(path)

    def _analyze_active(self):
        entry = self._procs.get(self._active_key) or {}
        if not entry:
            return
        path = entry.get("log_file") or ""
        if not path or not os.path.isfile(path):
            alert(self, _t("智能分析"), _t("日志文件不存在。"), "warn")
            return
        dlg = CrashAnalysisDialog(self, path, entry.get("version") or "")
        dlg.start()
        dlg.show()

    def _on_progress(self, text, key=None):
        if key is None:
            key = self._active_key
        entry = self._procs.get(key) or self._procs.get("main")
        if not entry or entry.get("removed"):
            return
        if entry.get("_prog_line"):
            entry["log"].replace_last_line(text)
        else:
            ts = datetime.now().strftime("%H:%M:%S")
            entry["log"].append("[%s] %s" % (ts, text), "accent")
            entry["_prog_line"] = True

    def clear_current_log(self):
        entry = self._procs.get(self._active_key)
        if entry:
            entry["log"].clear()

    def clear_closed_procs(self):
        closed = [k for k, e in self._procs.items()
                  if k != "main" and e.get("closed") and not e.get("removed")]
        if not closed:
            alert(self, _t("清除已关闭"), _t("当前没有已关闭的游戏进程。"))
            return
        for k in closed:
            e = self._procs.pop(k, None)
            if e:
                self._close_log_fp(e)
                try:
                    e["log"].destroy()
                except Exception:
                    pass
        if self._active_key in closed:
            self._active_key = "main"
        self._show_log("main")
        self._sync_proc_combo()

    def _running_threads(self):
        out = []
        for k, e in self._procs.items():
            th = e.get("thread")
            if th is not None and th.isRunning():
                out.append((k, th))
        return out

    def stop_all_games(self):
        procs = self._running_threads()
        if not procs:
            alert(self, _t("停止全部"), _t("当前没有正在运行的游戏进程。"))
            return
        d = BaseDialog(self, _t("停止全部"), width=440)
        d.add_label(_t("是否确定关闭所有Minecraft？"), size=13)
        close_outputs = tk.BooleanVar(value=False)
        cb = tk.Checkbutton(d._body, text=_t("同时关闭所有输出区"),
                            variable=close_outputs, bg=C("bg"), fg=C("text"),
                            activebackground=C("bg"), selectcolor=C("card"),
                            font=F(12), anchor="w", highlightthickness=0, bd=0)
        cb.pack(fill="x")

        def _yes():
            d.result = True
            d.destroy()
        d.button_row([(_t("否"), lambda: (setattr(d, "result", False), d.destroy()), False),
                      (_t("是"), _yes, True)])
        if not d.show():
            return
        self._starting_key = None
        self._reset_btn()
        for k, th in procs:
            entry = self._procs.get(k) or {}
            entry["cancelled"] = True
            has_pid = bool(entry.get("pid"))
            if close_outputs.get():
                entry["removed"] = True
                self._close_log_fp(entry)
                try:
                    entry["log"].destroy()
                except Exception:
                    pass
                self._procs.pop(k, None)
            if has_pid:
                th.request_close()
            else:
                th.cancel()
        if close_outputs.get():
            self._active_key = "main"
            self._show_log("main")
            self._sync_proc_combo()

    # ---------- 启动 ----------
    def toggle_launch(self):
        self.config = load_config()
        if self._starting_key is not None:
            key = self._starting_key
            self._starting_key = None
            entry = self._procs.get(key)
            if entry and entry.get("thread"):
                entry["cancelled"] = True
                self._append_log(_t("已请求取消此次启动 ..."), key)
                entry["thread"].cancel()
            self._reset_btn()
            return
        if not self.selected_account:
            alert(self, _t("提示"), _t("请先选择一个账号。"), "warn")
            return
        if not self.selected_version:
            alert(self, _t("提示"), _t("请先选择一个游戏版本。"), "warn")
            return
        v = self.selected_version
        if safe_version_dir(self.config.get("minecraft_directory", ""), v) is None:
            alert(self, _t("提示"), _t("版本名称非法，已阻止启动。"), "warn")
            return
        mp = (self.config.get("modpack_instances") or {}).get(v) or {}
        java_v = mp.get("mc_version") or v
        jp, need = resolve_java(java_v, self.config.get("java_path", ""))
        if not jp and self._force_java and os.path.isfile(self._force_java):
            jp = self._force_java
            need = required_java_major(java_v)
        self._force_java = ""
        if not jp:
            if self.on_java_missing:
                self.on_java_missing(java_v, need)
            return
        a = java_major_from_path(jp)
        if a is not None and a > need:
            self._append_log(_t("警告：未找到 Java ") + str(need)
                             + _t("，将使用 Java ") + str(a)
                             + _t("，部分旧版模组可能不兼容。"))
        self._do_launch(jp, v)

    def _do_launch(self, jp, v):
        acc = self.selected_account
        mc = self.config["minecraft_directory"]
        mp = (self.config.get("modpack_instances") or {}).get(v) or {}
        launch_v = mp.get("launch_version") or v
        game_dir = os.path.join(mc, mp["dir"]) if mp.get("dir") else mc
        issue = version_json_issue(mc, launch_v)
        if issue:
            alert(self, _t("无法启动"),
                  _t("版本 JSON 文件异常，无法补全或启动：\n%s\n\n"
                   "请在「下载游戏」中重新安装该版本。") % issue, "error")
            return
        self._proc_seq += 1
        key = self._proc_seq
        self._create_log_page(key, self._proc_label(v), v, game_dir)
        self._append_log(_t("开始启动游戏。"), key)
        self._append_log(_t("版本: ") + v, key)
        self._append_log(_t("账号: ") + acc.get("name", "?"), key)
        tm = {"offline": _t("离线"), "microsoft": _t("正版"),
              "thirdparty": _t("第三方")}
        self._append_log(_t("模式: ") + tm.get(acc.get("type", "offline"), _t("离线")), key)
        # 游戏窗口标题：留空不修改，填写的模板支持占位符
        title_tpl = str(self.config.get("game_window_title") or "").strip()
        win_title = ""
        if title_tpl:
            win_title = render_game_window_title(title_tpl, {
                "player": acc.get("name", "Player"),
                "version": v,
                "real_version": launch_v,
                "account_type": tm.get(acc.get("type", "offline"), _t("离线")),
                "launcher": "LCL",
            })
            self._append_log(_t("游戏窗口标题：") + win_title, key)
        self._procs[key]["window_title"] = win_title
        self._append_log("", key)
        if self.config.get("set_chinese_on_first_launch", True):
            self._set_lang_cn(mc, key)
        # 高级参数：按作用域（全局 / 版本独立）解析出最终启动计划
        plan = build_advanced_plan(v, mc, jp)
        ja = list(plan["jvm"])
        for w in plan.get("warnings") or []:
            self._append_log(_t("[提示] ") + w, key)
        if plan.get("gpu") == "high":
            ok, msg = apply_gpu_preference(jp)
            self._append_log((_t("已应用高性能显卡偏好：") if ok
                              else _t("高性能显卡设置未生效：")) + msg, key)
        uuid_val = acc.get("uuid", "") or offline_uuid(acc.get("name", "Player"))
        opts = {
            "username": acc.get("name", "Player"),
            "uuid": uuid_val,
            "token": acc.get("token", "0"),
            "executablePath": jp,
            "defaultExecutablePath": jp,
            "jvmArguments": ja,
            "launcherName": "LCL",
            "launcherVersion": APP_VERSION,
            "gameDirectory": game_dir,
        }
        if acc.get("type") == "thirdparty":
            inj = acc.get("injector") or self.config.get("authlib_injector_path", "")
            api = acc.get("api") or self.config.get("api_config", {}).get("base_url", "")
            if inj and os.path.isfile(inj) and api:
                opts["jvmArguments"].append("-javaagent:%s=%s" % (inj, api))
                self._append_log(_t("已注入 authlib-injector：")
                                 + os.path.basename(inj), key)
            else:
                self._append_log(_t("未找到 authlib-injector JAR 或 API 地址，"
                                    "第三方认证可能失败。"), key)
        if plan.get("game"):
            opts["_extra_game_args"] = plan["game"]
        # 启动前命令 / 进程优先级（仅高级参数启用时非空）
        if plan.get("pre_launch"):
            opts["_pre_launch"] = plan["pre_launch"]
            opts["_pre_launch_wait"] = bool(plan.get("pre_launch_wait"))
        if plan.get("priority") == "high":
            opts["_priority"] = "high"
        ensure_libraries_dir(mc)
        try:
            os.makedirs(version_mods_dir(mc, launch_v), exist_ok=True)
        except Exception:
            pass
        vdir = os.path.join(mc, "versions", launch_v)
        vj = os.path.join(vdir, "%s.json" % launch_v)
        need = not os.path.exists(vj)
        th = LaunchThread(launch_v, mc, opts, need, account=acc,
                          client_id=effective_microsoft_client_id(self.config))
        self._procs[key]["thread"] = th
        th.log_signal.connect(lambda s, k=key: self._append_log(s, k))
        th.progress_signal.connect(lambda s, k=key: self._on_progress(s, k))
        th.pid_signal.connect(lambda pid, k=key: self._on_proc_started(k, pid))
        th.error_signal.connect(lambda e, k=key: self._append_log("[错误] " + str(e), k))
        th.token_refreshed.connect(self._on_token_refreshed)
        th.finished_signal.connect(lambda c, k=key: self._on_proc_exit(k, c))
        self._starting_key = key
        self._set_starting_btn()
        th.start()

    def _on_proc_started(self, key, pid):
        entry = self._procs.get(key)
        if not entry:
            return
        entry["pid"] = pid
        title = entry.get("window_title")
        if title:
            apply_game_window_title(pid, title)
        self._update_proc_item(key)
        if self._starting_key == key:
            self._starting_key = None
            self._reset_btn()

    def _set_lang_cn(self, mc, key):
        f = os.path.join(mc, "options.txt")
        try:
            lines = []
            if os.path.exists(f):
                with open(f, "r", encoding="utf-8") as fp:
                    lines = fp.readlines()
                if any(line.startswith("lang:") for line in lines):
                    return
            lines.append("lang:zh_CN\n")
            with open(f, "w", encoding="utf-8") as fp:
                fp.writelines(lines)
            self._append_log(_t("已将游戏语言设为中文。"), key)
        except Exception as e:
            self._append_log(_t("设置游戏语言失败：") + str(e), key)

    def _on_token_refreshed(self, name, token, refresh_token):
        data = load_accounts()
        changed = False
        for a in data.get("accounts", []):
            if a.get("name") == name and a.get("type") == "microsoft":
                a["token"] = token
                if refresh_token:
                    a["refresh_token"] = refresh_token
                changed = True
        if changed:
            try:
                save_accounts(data)
            except Exception as e:
                self._append_log(_t("保存新登录令牌失败：") + str(e))
            self.refresh_accounts()
            self._update_info()
            self._append_log(_t("正版登录令牌已自动续期。"))

    def _on_proc_exit(self, key, code):
        entry = self._procs.get(key)
        if entry is None:
            if self._starting_key == key:
                self._starting_key = None
                self._reset_btn()
            return
        entry["closed"] = True
        entry["code"] = code
        entry["thread"] = None
        # 用户主动关闭（取消启动 / 停止全部）不算异常，不弹出分析入口
        entry["abnormal"] = (code not in (0, None)
                             and not entry.get("cancelled"))
        if self._starting_key == key:
            self._starting_key = None
            self._reset_btn()
        if entry.get("removed"):
            self._procs.pop(key, None)
            return
        pid = entry.get("pid")
        if pid:
            status = (_t("正常退出") if code == 0
                      else _t("异常退出! 错误码: ") + str(code))
            self._append_log(_t("进程PID为%s的Minecraft已被关闭。状态：") % pid + status, key)
        else:
            self._append_log(_t("启动已取消或失败（错误码 %s）。") % code, key)
        self._update_proc_item(key)
        self._refresh_log_actions()
        # 若下载好的启动器更新在等游戏结束，这里触发延迟重启
        if self.app is not None and hasattr(self.app, "on_game_exited"):
            try:
                self.app.on_game_exited()
            except Exception:
                traceback.print_exc()

    def _reset_btn(self):
        self.launch_btn.setText(_t("启动游戏"))
        self.launch_btn.setColors(("#89b4fa", "#74c7ec"),
                                  ("#a6c8ff", "#89dceb"),
                                  ("#74a0e0", "#5eb8e0"), "#1e1e2e")

    def _set_starting_btn(self):
        self.launch_btn.setText(_t("...正在启动游戏...\n[再次按下取消此次启动]"))
        self.launch_btn.setColors(("#e74c3c", "#f38ba8"),
                                  ("#f06050", "#f5a0b8"),
                                  ("#c0392b", "#c0392b"), "#ffffff")

    def is_game_running(self):
        return bool(self._running_threads())

    def kill_game(self):
        for k, th in self._running_threads():
            entry = self._procs.get(k) or {}
            entry["cancelled"] = True
            if entry.get("pid"):
                th.request_close()
            else:
                th.cancel()

    def shutdown(self):
        try:
            if getattr(self, "_scan_job", None):
                self.after_cancel(self._scan_job)
        except Exception:
            pass
        for entry in list(self._procs.values()):
            self._close_log_fp(entry)


class MicrosoftLoginDialog(BaseDialog):
    def __init__(self, parent, client_id):
        super().__init__(parent, _t("添加正版账号"), width=470, height=280)
        self.account = None
        self._thread = None
        self.add_label(_t("正在向微软申请设备码 ..."), fg="text2", size=12)
        self.code_lbl = tk.Label(self._body, text="", bg=C("bg"), fg=C("accent"),
                                 font=F(22, True))
        self.code_lbl.pack(pady=(4, 6))
        self.tip = tk.Label(self._body, text="", bg=C("bg"), fg=C("text"),
                            font=F(12), justify="left", anchor="w",
                            wraplength=420)
        self.tip.pack(fill="x")
        self.open_btn = FlatButton(self._body, _t("打开浏览器"), self._open, bg="card",
                                   fg="accent", hover="border", padx=14, pady=5,
                                   font_size=12)
        self.open_btn.pack(anchor="w", pady=(8, 0))
        self.open_btn.pack_forget()
        self._uri = ""
        d = self
        d.button_row([(_t("取消"), self._cancel, False)])
        self._thread = MicrosoftLoginThread(client_id)
        self._thread.code_ready.connect(self._on_code)
        self._thread.success.connect(self._on_success)
        self._thread.failed.connect(self._on_failed)
        self._thread.start()

    def _on_code(self, uri, code):
        self._uri = uri or ""
        self.code_lbl.configure(text=code or "")
        self.tip.configure(text=_t("请访问 %s 并输入上方代码完成登录。") % self._uri)
        self.open_btn.pack(anchor="w", pady=(8, 0))

    def _open(self):
        if self._uri:
            webbrowser.open(self._uri)

    def _on_success(self, acc):
        self.account = acc
        self.destroy()

    def _on_failed(self, msg):
        self.tip.configure(text=_t("登录失败：") + msg, fg=C("red1"))
        self.code_lbl.configure(text="")

    def _cancel(self):
        if self._thread and self._thread.isRunning():
            try:
                self._thread.cancel()
            except Exception:
                pass
        self.destroy()

# ============ 管理版本 ============
def _open_path(path):
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


# ============ 启动日志与智能分析 ============
LAUNCH_LOG_DIR = os.path.join(DATA_DIR, "logs")


def _new_launch_log_path(version, key):
    """每个游戏进程一份独立日志，避免多开时互相覆盖。"""
    try:
        os.makedirs(LAUNCH_LOG_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe = re.sub(r"[^0-9A-Za-z_\-\.]+", "_", str(version or "game"))[:40]
        return os.path.join(
            LAUNCH_LOG_DIR, "launch_%s_%s_%s.log" % (stamp, safe, key))
    except Exception:
        return ""


_AI_ERR_KEYS = (
    "error", "exception", "caused by", "fatal", "crash", "failed", "unable",
    "no such", "not found", "mixin", "conflict", "outofmemory", "stacktrace",
    "报错", "错误", "异常", "失败", "崩溃", "不兼容", "缺少",
)


def _trim_log_for_ai(text, limit=18000):
    """日志超长时保留报错行 + 末尾上下文，避免超出模型输入上限。"""
    text = text or ""
    if len(text) <= limit:
        return text
    lines = text.splitlines()
    errs, tail = [], lines[-200:]
    for ln in lines:
        low = ln.lower()
        if any(k in low for k in _AI_ERR_KEYS):
            errs.append(ln)
    picked, seen = [], set()
    for ln in errs[-150:] + tail:
        if ln not in seen:
            seen.add(ln)
            picked.append(ln)
    out = "\n".join(picked)
    return out[-limit:] if len(out) > limit else out


def _read_log_excerpt(path, limit=18000):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except Exception:
        return ""
    return _trim_log_for_ai(raw, limit)


# ============ LogShare（完全免费的日志托管与诊断） ============
LOGSHARE_API = "https://api.logshare.cn"
LOGSHARE_SITE = "https://logshare.cn"
LOGSHARE_SOURCE = "LightningCraftLauncher/1.0.1"


def _prepare_log_for_share(text):
    """去掉启动器头部与追加的时间戳前缀，尽量还原 Minecraft 原始日志行。"""
    out = []
    for ln in (text or "").splitlines():
        if ln.startswith("#"):
            continue
        m = re.match(r"^\[\d{2}:\d{2}:\d{2}\] (.*)$", ln)
        if m:
            ln = m.group(1)
        if ln.strip():
            out.append(ln)
    return "\n".join(out)


def _logshare_upload(log_text, version="", loader=""):
    """上传日志到 LogShare，返回 (分享链接, insights 数据)。"""
    import requests
    log_text = _prepare_log_for_share(log_text)
    if not log_text.strip():
        raise RuntimeError(_t("日志内容为空，无法分析。"))
    # 服务端限制展开后合计 20 MiB，这里控制在约 8 MiB 以内
    if len(log_text.encode("utf-8", "ignore")) > 8 * 1024 * 1024:
        log_text = _trim_log_for_ai(log_text, 2000000)
    meta = [{"key": "launcher",
             "value": "Lightning Craft Launcher", "label": _t("启动器")}]
    if version:
        meta.append({"key": "version", "value": str(version),
                     "label": _t("Minecraft 版本")})
    if loader:
        meta.append({"key": "loader", "value": str(loader),
                     "label": _t("加载器")})
    payload = {
        "files": [{"name": "latest.log", "content": log_text}],
        "source": LOGSHARE_SOURCE,
        "metadata": meta,
    }
    r = requests.post(LOGSHARE_API + "/v1/log", json=payload, timeout=90)
    try:
        data = r.json()
    except Exception:
        raise RuntimeError(_t("LogShare 返回异常（HTTP %s）") % r.status_code
                           + "：" + str(r.text or "")[:200])
    if not data.get("success"):
        raise RuntimeError(
            _t("LogShare 上传失败：")
            + str(data.get("error") or data.get("message") or "")[:300])
    share_url = str(data.get("url") or "").strip()
    if not share_url and data.get("id"):
        share_url = LOGSHARE_SITE + "/" + str(data["id"])
    insights_url = ""
    files = data.get("files") or []
    if files and isinstance(files[0], dict):
        insights_url = str(files[0].get("insights") or "")
    if not insights_url and data.get("id"):
        insights_url = "%s/v1/insights/%s/latest.log" % (LOGSHARE_API,
                                                        data["id"])
    insight = None
    if insights_url:
        try:
            ir = requests.get(insights_url, timeout=90)
            if ir.status_code == 200:
                insight = ir.json()
        except Exception:
            insight = None
    return share_url, insight


def _render_insight_item(p):
    """兼容 Codex 不同版本的字段，尽量把问题与建议都展示出来。"""
    out = []
    if not isinstance(p, dict):
        return ["• " + str(p)]
    msg = p.get("message") or p.get("title")
    if msg:
        out.append("• " + str(msg))
    entry = p.get("entry") or {}
    for ln in (entry.get("lines") or []) if isinstance(entry, dict) else []:
        c = ln.get("content") if isinstance(ln, dict) else ln
        if c:
            out.append("    " + str(c).strip())
    for key in ("solutions", "solution", "advice", "suggestions", "hint"):
        v = p.get(key)
        if isinstance(v, str) and v.strip():
            out.append("  " + _t("建议：") + v.strip())
        elif isinstance(v, list):
            for x in v:
                if str(x).strip():
                    out.append("  - " + str(x).strip())
    return out


def _format_logshare_result(insight, share_url=""):
    lines = []
    if isinstance(insight, dict):
        title = insight.get("title") or insight.get("name") or _t("日志分析")
        lines.append(_t("分析类型：") + str(title))
        ver = insight.get("version")
        if ver:
            lines.append(_t("识别版本：") + str(ver))
        analysis = insight.get("analysis") or {}
        problems = analysis.get("problems") or []
        infos = analysis.get("information") or []
        lines.append("")
        if problems:
            lines.append(_t("检测到的问题："))
            for p in problems:
                lines.extend(_render_insight_item(p))
        else:
            lines.append(_t("LogShare 未识别到明确的崩溃问题，"
                            "可在分享页查看完整日志与高亮。"))
        if infos:
            lines.append("")
            lines.append(_t("相关信息："))
            for it in infos[:25]:
                m = it.get("message") if isinstance(it, dict) else it
                if m:
                    lines.append("• " + str(m))
    else:
        lines.append(_t("日志已上传，但未能获取自动分析结果。"))
    if share_url:
        lines.append("")
        lines.append(_t("分享链接（已脱敏，7 天内有效）："))
        lines.append(share_url)
    return "\n".join(lines)



class CrashAnalysisDialog(BaseDialog):
    """上传日志到 LogShare，展示免费诊断结果与分享链接。"""

    def __init__(self, parent, log_file, version):
        super().__init__(parent, _t("智能分析"), width=680, height=520)
        self.log_file = log_file
        self.version = version
        self.share_url = ""
        self._status = tk.Label(
            self._body, text=_t("正在读取日志并请求分析，请稍候 ..."),
            bg=C("bg"), fg=C("accent"), font=F(12, True), anchor="w",
            justify="left", wraplength=620)
        self._status.pack(fill="x", pady=(0, 4))
        self._link = tk.Label(
            self._body, text="", bg=C("bg"), fg=C("accent"), font=F(11),
            anchor="w", cursor="hand2", justify="left", wraplength=620)
        self._link.bind("<Button-1>", lambda e: self._open_share())
        wrap = tk.Frame(self._body, bg=C("border"))
        wrap.pack(fill="both", expand=True, pady=(4, 0))
        self._text = tk.Text(wrap, bg=C("log_bg"), fg=C("log_fg"),
                             font=F(11, mono=True), bd=0, highlightthickness=0,
                             wrap="word", state="disabled", padx=8, pady=6)
        sb = _mk_scrollbar(wrap, self._text.yview)
        self._text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._text.pack(side="left", fill="both", expand=True)
        self.button_row([(_t("关闭"), lambda: self.destroy(), True)])

    def _open_share(self):
        if self.share_url:
            webbrowser.open(self.share_url)

    def _set_share(self, url):
        self.share_url = str(url or "")
        try:
            if self.share_url:
                self._link.configure(
                    text=_t("分享链接：") + self.share_url + _t("（点击打开）"))
                if not self._link.winfo_ismapped():
                    self._link.pack(fill="x", before=self._text.master)
            else:
                self._link.pack_forget()
        except tk.TclError:
            pass

    def _set_text(self, text):
        try:
            self._text.configure(state="normal")
            self._text.delete("1.0", "end")
            self._text.insert("1.0", text)
            self._text.configure(state="disabled")
        except tk.TclError:
            pass

    def start(self):
        def job():
            text = _read_log_excerpt(self.log_file, 2000000)
            if not text.strip():
                raise RuntimeError(_t("日志内容为空，无法分析。"))
            share, insight = _logshare_upload(text, self.version)
            return (_format_logshare_result(insight, share), share)

        def ok(result):
            if isinstance(result, tuple):
                text, share = result
            else:
                text, share = result, ""
            try:
                self._status.configure(text=_t("分析完成"), fg=C("log_fg"))
                self._set_share(share)
                self._set_text(text)
            except tk.TclError:
                pass

        def err(e):
            try:
                self._status.configure(text=_t("分析失败：") + str(e),
                                       fg=C("red1"))
                self._set_text(_t("分析失败：") + str(e))
            except tk.TclError:
                pass

        _run_bg(job, ok, err)


class RepairDialog(BaseDialog):
    def __init__(self, parent, version, mc_dir):
        super().__init__(parent, _t("补全文件"), width=470, height=230)
        self.add_label(_t("正在检查并补全版本：") + version, size=13)
        self.status_lbl = tk.Label(self._body, text=_t("准备中 ..."), bg=C("bg"),
                                   fg=C("text2"), font=F(12), anchor="w",
                                   wraplength=420, justify="left")
        self.status_lbl.pack(fill="x", pady=(4, 6))
        self.bar = ProgressBar(self._body, height=18, bg="card", fill="accent",
                              bg_role="bg", text=True)
        self.bar.pack(fill="x")
        self._thread = VersionRepairThread(version, mc_dir)
        self._thread.status_signal.connect(self._on_status)
        self._thread.progress_signal.connect(self._on_progress)
        self._thread.done_signal.connect(self._on_done)
        self._thread.start()

    def _on_status(self, s):
        try:
            self.status_lbl.configure(text=s)
        except Exception:
            pass

    def _on_progress(self, v):
        try:
            self.bar.setValue(v)
        except Exception:
            pass

    def _on_done(self, ok, msg):
        try:
            self.status_lbl.configure(text=msg, fg=C("log_fg" if ok else "red1"))
            self.bar.setValue(100 if ok else self.bar._value)
        except Exception:
            pass
        if ok:
            self.after(900, self.destroy)


class ManagePage(Page):
    STATUS_TEXT = {"ok": "可用", "json_broken": "JSON损坏", "libs_missing": "库文件缺失"}
    STATUS_COLOR = {"ok": "log_fg", "json_broken": "red1", "libs_missing": "yellow"}

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.selected = None
        self._cards = {}
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        root = tk.Frame(self, bg=C("bg"))
        root.pack(fill="both", expand=True, padx=16, pady=(10, 14))
        top = tk.Frame(root, bg=C("bg")); top.pack(fill="x")
        tk.Label(top, text=_t("管理版本"), bg=C("bg"), fg=C("text"),
                 font=F(22, True)).pack(side="left")
        IconButton(top, "gear", _t("刷新"), self.refresh, bg="bg", fg="accent",
                   hover="panel", size=28, icon_size=13, font_size=12).pack(side="right")

        self.list_area = tk.Frame(root, bg=C("bg"))
        self.list_area.pack(fill="both", expand=True)
        self.scroll = ScrollFrame(self.list_area, bg="bg")
        self.scroll.pack(fill="both", expand=True, pady=(8, 8))

        bar = tk.Frame(self.list_area, bg=C("bg")); bar.pack(fill="x")
        self.btn_rename = FlatButton(bar, _t("重命名"), self.rename_selected,
                                     bg="card", fg="text", padx=14, pady=5, font_size=12)
        self.btn_delete = FlatButton(bar, _t("删除"), self.delete_selected,
                                     bg="card", fg="red1", padx=14, pady=5, font_size=12)
        self.btn_folder = FlatButton(bar, _t("打开版本文件夹"), self.open_version_folder,
                                     bg="card", fg="text", padx=14, pady=5, font_size=12)
        self.btn_mods = FlatButton(bar, _t("打开MOD文件夹"), self.open_mods_folder,
                                   bg="card", fg="text", padx=14, pady=5, font_size=12)
        self.btn_manage_mods = FlatButton(bar, _t("管理模组"), self.open_mod_manager,
                                          bg="card", fg="accent", padx=14, pady=5,
                                          font_size=12)
        self.btn_repair = FlatButton(bar, _t("补全文件"), self.repair_selected,
                                     bg="accent", fg="#1e1e2e", hover="accent_hover",
                                     padx=14, pady=5, font_size=12)
        self.btn_rename.pack(side="left", padx=(0, 6))
        self.btn_delete.pack(side="left", padx=(0, 6))
        self.btn_folder.pack(side="left", padx=(0, 6))
        self.btn_mods.pack(side="left", padx=(0, 6))
        self.btn_manage_mods.pack(side="left", padx=(0, 6))
        self.btn_repair.pack(side="right")

        self.manager_holder = tk.Frame(root, bg=C("bg"))
        self._manager = None
        self._update_buttons()

    def _version_names(self):
        mc = load_config().get("minecraft_directory", "")
        names = set(load_versions().get("versions", []))
        try:
            import minecraft_launcher_lib
            names |= {v["id"] for v in
                      minecraft_launcher_lib.utils.get_installed_versions(mc)}
        except Exception:
            pass
        return sorted(names, key=_natural_version_key, reverse=True)

    def refresh(self):
        self.scroll.clear()
        self._cards = {}
        mc = load_config().get("minecraft_directory", "")
        names = self._version_names()
        if not names:
            tk.Label(self.scroll.inner, text=_t("暂无已安装的版本。"),
                     bg=C("bg"), fg=C("muted"), font=F(12)).pack(pady=20)
            self.selected = None
            self._update_buttons()
            return
        for name in names:
            real, lver, status = describe_version(mc, name)
            self._make_card(name, real, lver, status)
        if self.selected in self._cards:
            self._highlight(self.selected)
        else:
            self.selected = None
        self._update_buttons()

    def _make_card(self, name, real, lver, status):
        card = RoundedCard(self.scroll.inner, radius=10, padx=12, pady=8,
                           cursor="hand2")
        card.pack(fill="x", padx=6, pady=4)
        inner = card.inner
        r1 = tk.Frame(inner, bg=C("card")); r1.pack(fill="x")
        tk.Label(r1, text=name, bg=C("card"), fg=C("text"),
                 font=F(14, True), anchor="w").pack(side="left")
        tk.Label(r1, text=_t(self.STATUS_TEXT.get(status, "可用")),
                 bg=C("card"), fg=C(self.STATUS_COLOR.get(status, "log_fg")),
                 font=F(11, True)).pack(side="right")
        r2 = tk.Frame(inner, bg=C("card")); r2.pack(fill="x", pady=(3, 0))
        lv_txt = lver if lver else _t("无")
        tk.Label(r2, text=_t("真实版本：") + real + "    " + _t("加载器：") + lv_txt,
                 bg=C("card"), fg=C("text2"), font=F(11), anchor="w").pack(side="left")

        def _click(_=None, n=name):
            self.selected = n
            self._highlight(n)
            self._update_buttons()

        _bind_tree(card, left=_click)
        self._cards[name] = card

    def _highlight(self, name):
        for n, card in self._cards.items():
            try:
                card.set_selected(n == name)
            except Exception:
                pass

    def _update_buttons(self):
        single = self.selected is not None
        for b in (self.btn_rename, self.btn_delete, self.btn_folder, self.btn_repair,
                  self.btn_manage_mods):
            b.setEnabled(single)
        mc = load_config().get("minecraft_directory", "")
        has_mods = bool(single and os.path.isdir(version_mods_dir(mc, self.selected)))
        self.btn_mods.setEnabled(has_mods)

    def _on_theme(self):
        Page._on_theme(self)
        if self._manager is not None:
            return
        try:
            self.refresh()
        except Exception:
            pass

    def rename_selected(self):
        if not self.selected:
            return
        new = ask_text(self, _t("重命名版本"), _t("新名称："), self.selected)
        if not new or new == self.selected:
            return
        mc = load_config().get("minecraft_directory", "")
        ok, err = rename_version_dir(mc, self.selected, new.strip())
        if not ok:
            alert(self, _t("重命名失败"), err, "error")
            return
        # 同步 versions.json 与配置中的实例映射
        vd = load_versions()
        vs = vd.get("versions", [])
        vd["versions"] = [new.strip() if v == self.selected else v for v in vs]
        save_versions(vd)
        self.selected = new.strip()
        self.refresh()

    def delete_selected(self):
        if not self.selected:
            return
        name = self.selected
        if not confirm(self, _t("删除版本"),
                       _t("确定删除版本“%s”吗？版本文件夹会被移入回收站。") % name,
                       danger=True):
            return
        mc = load_config().get("minecraft_directory", "")
        path = os.path.join(mc, "versions", name)
        try:
            try:
                import send2trash
                send2trash.send2trash(path)
            except Exception:
                shutil.rmtree(path, ignore_errors=True)
        except Exception as e:
            alert(self, _t("删除失败"), str(e), "error")
            return
        vd = load_versions()
        vd["versions"] = [v for v in vd.get("versions", []) if v != name]
        save_versions(vd)
        if load_config().get("selected_version") == name:
            update_config(selected_version="")
        self.selected = None
        self.refresh()

    def open_version_folder(self):
        if not self.selected:
            return
        mc = load_config().get("minecraft_directory", "")
        path = os.path.join(mc, "versions", self.selected)
        if os.path.isdir(path):
            _open_path(path)

    def open_mods_folder(self):
        if not self.selected:
            return
        mc = load_config().get("minecraft_directory", "")
        path = version_mods_dir(mc, self.selected)
        if os.path.isdir(path):
            _open_path(path)

    # ---------- 内嵌模组管理 ----------
    def open_mod_manager(self):
        if not self.selected:
            return
        self._close_manager()
        try:
            self.list_area.pack_forget()
            self._manager = ModManagerView(self.manager_holder, self.app,
                                           self.selected, self._close_manager)
            self._manager.pack(fill="both", expand=True)
            self.manager_holder.pack(fill="both", expand=True)
        except Exception as e:
            self._close_manager()
            alert(self, _t("打开模组管理失败"), str(e), "error")

    def _close_manager(self):
        try:
            if self._manager is not None:
                self._manager.destroy()
        except Exception:
            pass
        self._manager = None
        try:
            self.manager_holder.pack_forget()
            self.list_area.pack(fill="both", expand=True)
        except Exception:
            pass
        try:
            self.refresh()
        except Exception:
            pass

    def repair_selected(self):
        if not self.selected:
            return
        mc = load_config().get("minecraft_directory", "")
        d = RepairDialog(self, self.selected, mc)
        d.show()
        self.refresh()

# ============ Modrinth 资源 ============
_MODRINTH_API = "https://api.modrinth.com/v2"
_MR_HEADERS = {"User-Agent": "LCL-Launcher/1.0.1 (Lightning Craft Launcher)"}
_MR_LOADER_NAMES = {
    "fabric": "Fabric", "forge": "Forge", "neoforge": "NeoForge",
    "quilt": "Quilt", "liteloader": "LiteLoader", "rift": "Rift",
    "optifine": "OptiFine", "iris": "Iris", "canvas": "Canvas",
}


def _mr_request(method, path, params=None, payload=None, timeout=25):
    import requests
    r = requests.request(method, _MODRINTH_API + path, params=params,
                         json=payload, headers=_MR_HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _mr_search(project_type, query, offset=0, limit=20, loader=None):
    facets = [["project_type:%s" % project_type]]
    if loader:
        facets.append(["categories:%s" % loader])
    return _mr_request("GET", "/search", {
        "query": query or "", "offset": int(offset), "limit": int(limit),
        "facets": json.dumps(facets), "index": "relevance"})


def _mr_project(pid):
    return _mr_request("GET", "/project/" + str(pid))


def _mr_versions(pid):
    return _mr_request("GET", "/project/%s/version" % pid)


def _mr_projects(ids):
    ids = [str(i) for i in ids if i][:100]
    if not ids:
        return []
    return _mr_request("GET", "/projects", {"ids": json.dumps(ids)})


def _mr_members(pid):
    """返回项目成员用户名列表（用于详情页作者信息）。"""
    try:
        data = _mr_request("GET", "/project/%s/members" % pid)
    except Exception:
        return []
    names = []
    for m in data or []:
        nm = (m.get("user") or {}).get("username") or ""
        if nm:
            names.append(nm)
    return names


# ---------- CurseForge 资源 ----------
# 官方 api.curseforge.com 必须有 API Key；未填写时改走免 Key 公共通道
# （api.curse.tools 社区代理 + mod.mcimirror.top 国内镜像，互为备用），
# 两者返回与官方同样的结构。
_CF_API = "https://api.curseforge.com/v1"
_CF_PROXY = "https://api.curse.tools/v1/cf"
_CF_PROXY2 = "https://mod.mcimirror.top/curseforge/v1"
_CF_GAME_ID = 432
_CF_CLASS_IDS = {"mod": 6, "shader": 6552, "modpack": 4471}
_CF_LOADER_NAMES = {"forge": "Forge", "fabric": "Fabric", "quilt": "Quilt",
                    "neoforge": "NeoForge", "liteloader": "LiteLoader"}


def _cf_key():
    try:
        return str(load_config().get("curseforge_api_key") or "").strip()
    except Exception:
        return ""


def _cf_request(path, params=None, timeout=25):
    import requests
    key = _cf_key()
    if key:
        # 填了 Key 只走官方接口
        r = requests.get(_CF_API + path, params=params,
                         headers={"x-api-key": key,
                                  "User-Agent": "LCL-Launcher/1.0.1"},
                         timeout=timeout)
        r.raise_for_status()
        return r.json()
    # 未填 Key：依次尝试免 Key 公共通道，任一可用即返回
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    last_err = None
    for base in (_CF_PROXY, _CF_PROXY2):
        try:
            r = requests.get(base + path, params=params, headers=headers,
                             timeout=12)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
    raise last_err


def _cf_mod_loaders(m):
    out = []
    for c in (m or {}).get("categories") or []:
        s = str(c.get("slug") or "").lower()
        if s in _CF_LOADER_NAMES and s not in out:
            out.append(s)
    return out


def _cf_hit(m):
    authors = [a.get("name") or "" for a in (m.get("authors") or [])]
    return {"project_id": str(m.get("id") or ""),
            "slug": m.get("slug") or "",
            "title": m.get("name") or "?",
            "description": m.get("summary") or "",
            "author": " / ".join([a for a in authors if a][:2]),
            "icon_url": (m.get("logo") or {}).get("thumbnailUrl") or "",
            "downloads": m.get("downloadCount"),
            "display_categories": _cf_mod_loaders(m)}


def _cf_search(project_type, query, offset=0, limit=20):
    data = _cf_request("/mods/search", {
        "gameId": _CF_GAME_ID,
        "classId": _CF_CLASS_IDS.get(project_type, 6),
        "searchFilter": query or "", "pageSize": int(limit),
        "index": int(offset), "sortField": 2, "sortOrder": "desc"})
    hits = [_cf_hit(m) for m in data.get("data") or []]
    total = int((data.get("pagination") or {}).get("totalCount") or len(hits))
    return {"hits": hits, "total_hits": total}


def _cf_project(mod_id):
    m = (_cf_request("/mods/%s" % mod_id).get("data")) or {}
    authors = [a.get("name") for a in (m.get("authors") or []) if a.get("name")]
    return {"id": str(m.get("id") or ""),
            "title": m.get("name") or "?",
            "slug": m.get("slug") or "",
            "loaders": _cf_mod_loaders(m),
            "description": m.get("summary") or "",
            "downloads": m.get("downloadCount"),
            "icon_url": (m.get("logo") or {}).get("thumbnailUrl") or "",
            "project_type": "mod",
            "authors": authors,
            "cf_page": (m.get("links") or {}).get("websiteUrl") or ""}


def _cf_versions(mod_id, limit=300):
    files, index = [], 0
    while len(files) < limit:
        data = _cf_request("/mods/%s/files" % mod_id,
                           {"pageSize": 50, "index": index})
        batch = data.get("data") or []
        files.extend(batch)
        if len(batch) < 50:
            break
        index += 50
    vers = []
    for f in files:
        gvs, loaders = [], []
        for g in f.get("gameVersions") or []:
            gl = str(g).lower()
            if gl in _CF_LOADER_NAMES:
                if gl not in loaders:
                    loaders.append(gl)
            elif re.match(r"^\d", str(g)) and str(g) not in gvs:
                gvs.append(str(g))
        deps = []
        for d in f.get("dependencies") or []:
            if d.get("modId"):
                deps.append({
                    "project_id": str(d["modId"]),
                    "dependency_type": ("required"
                                        if d.get("relationType") == 1
                                        else "optional")})
        vers.append({
            "id": str(f.get("id") or ""),
            "version_number": (f.get("displayName") or f.get("fileName")
                               or "?"),
            "name": f.get("displayName") or "",
            "game_versions": gvs,
            "loaders": loaders,
            "date_published": f.get("fileDate") or "",
            "files": [{"primary": True,
                       "url": f.get("downloadUrl") or "",
                       "filename": f.get("fileName") or "mod.jar",
                       "size": f.get("fileLength") or 0}],
            "dependencies": deps,
            "_cf_mod_id": str(mod_id),
            "_cf_file_id": str(f.get("id") or ""),
        })
    return vers


def _cf_projects(ids):
    """批量取依赖项目的标题（逐个请求，数量限制在 30 个以内）。"""
    out = []
    seen = []
    for x in ids:
        s = str(x)
        if s.isdigit() and s not in seen:
            seen.append(s)
    for s in seen[:30]:
        try:
            m = (_cf_request("/mods/%s" % s).get("data")) or {}
            if m:
                out.append({"id": str(m.get("id") or s),
                            "title": m.get("name") or s,
                            "slug": m.get("slug") or ""})
        except Exception:
            continue
    return out


def _run_bg(fn, on_ok=None, on_err=None):
    """后台执行 fn，结果通过 _ui_call 回到主线程。"""
    def _worker():
        try:
            res = fn()
            if on_ok:
                _ui_call(on_ok, res)
        except Exception as e:
            if on_err:
                _ui_call(on_err, e)
    threading.Thread(target=_worker, daemon=True).start()


def _loader_text(loaders):
    names = []
    for item in loaders or []:
        nm = _MR_LOADER_NAMES.get(str(item).lower())
        if nm and nm not in names:
            names.append(nm)
    return " / ".join(names)


def _hit_loaders(hit):
    cats = list(hit.get("display_categories") or []) + list(hit.get("categories") or [])
    return _loader_text(cats)


def _primary_file(ver):
    files = (ver or {}).get("files") or []
    for f in files:
        if f.get("primary"):
            return f
    return files[0] if files else {}


def _file_size(ver):
    try:
        n = int(_primary_file(ver).get("size") or 0)
    except Exception:
        n = 0
    if n <= 0:
        return ""
    if n < 1048576:
        return "%.0f KB" % (n / 1024.0)
    return "%.1f MB" % (n / 1048576.0)


def _fmt_num(n):
    try:
        n = int(n)
    except Exception:
        return "?"
    if n >= 1000000:
        return "%.1fM" % (n / 1000000.0)
    if n >= 1000:
        return "%.1fk" % (n / 1000.0)
    return str(n)


def _game_version_key(v):
    out = []
    for part in re.split(r"[.\-_]", str(v)):
        m = re.match(r"(\d+)", part)
        out.append(int(m.group(1)) if m else -1)
    return tuple(out)


# ---------- 本地实例识别 ----------
def detect_instance_loader(mc_dir, name):
    """返回 (真实游戏版本, 加载器标识, 加载器版本)；无法解析时返回 (None, "", "")。"""
    jf = os.path.join(mc_dir, "versions", str(name), str(name) + ".json")
    try:
        with open(jf, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None, "", ""
    if not isinstance(data, dict):
        return None, "", ""
    mcver = str(data.get("inheritsFrom") or data.get("id") or name)
    loader, lver = "", ""
    for lib in data.get("libraries") or []:
        if not isinstance(lib, dict):
            continue
        parts = str(lib.get("name", "")).split(":")
        if len(parts) < 3:
            continue
        g, a, v = parts[0], parts[1], parts[2]
        if g == "net.fabricmc" and a == "fabric-loader":
            loader, lver = "fabric", v
        elif g == "org.quiltmc" and a == "quilt-loader":
            loader, lver = "quilt", v
        elif g == "net.minecraftforge" and a in ("forge", "fmlloader"):
            loader, lver = "forge", v.split("-", 1)[-1]
        elif g == "net.neoforged" and a in ("neoforge", "loader"):
            loader, lver = "neoforge", v
    if not loader:
        nid = str(data.get("id") or "")
        m = re.match(r"fabric-loader-([\w.]+)-(.+)$", nid)
        if m:
            loader, lver, mcver = "fabric", m.group(1), m.group(2)
        else:
            m = re.match(r"quilt-loader-([\w.]+)-(.+)$", nid)
            if m:
                loader, lver, mcver = "quilt", m.group(1), m.group(2)
            else:
                m = re.match(r"(.+)-forge-([\w.]+)$", nid)
                if m:
                    loader, lver, mcver = "forge", m.group(2), m.group(1)
                else:
                    m = re.match(r"neoforge-([\w.]+)$", nid)
                    if m:
                        loader, lver = "neoforge", m.group(1)
    return mcver, loader, lver


def instance_mods_dir(mc_dir, version):
    mp = (load_config().get("modpack_instances") or {}).get(version) or {}
    if mp.get("dir"):
        return os.path.join(mc_dir, mp["dir"], "mods")
    return version_mods_dir(mc_dir, version)


def instance_shader_dir(mc_dir, version):
    return os.path.join(os.path.dirname(instance_mods_dir(mc_dir, version)),
                        "shaderpacks")


def list_local_instances(mc_dir):
    out = []
    vr = os.path.join(mc_dir, "versions")
    try:
        names = sorted(os.listdir(vr))
    except Exception:
        return out
    for n in names:
        if not os.path.isdir(os.path.join(vr, n)):
            continue
        mcver, loader, lver = detect_instance_loader(mc_dir, n)
        if not mcver:
            continue
        out.append({"name": n, "mcver": mcver, "loader": loader,
                    "loader_version": lver})
    return out


def _loader_matches(instance_loader, version_loaders):
    vl = {str(x).lower() for x in (version_loaders or [])}
    if not vl:
        return True
    il = str(instance_loader or "").lower()
    if il in vl:
        return True
    if il == "quilt" and "fabric" in vl:
        return True
    return False


def _instance_loader_family(loader):
    if not loader:
        return []
    if loader == "quilt":
        return ["quilt", "fabric"]
    return [loader]


def _sha1_file(path):
    try:
        import hashlib
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def _download_to(url, dest, on_progress=None):
    import requests
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    r = requests.get(url, stream=True, headers=_MR_HEADERS, timeout=60)
    r.raise_for_status()
    try:
        total = int(r.headers.get("Content-Length") or 0)
    except Exception:
        total = 0
    done = 0
    try:
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(65536):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                if on_progress and total:
                    on_progress(min(99, int(done * 100 / total)))
        os.replace(tmp, dest)
    except Exception:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise
    if on_progress:
        on_progress(100)
    return dest


# ---------- 图标异步加载 ----------
_ICON_CACHE = {}
_ICON_QUEUE = []
_ICON_LOCK = threading.Lock()
_ICON_RUNNING = False


def _icon_placeholder(canvas, letter, size):
    try:
        canvas.delete("all")
        canvas.create_text(size / 2, size / 2, text=(letter or "?")[:1].upper(),
                           fill=C("accent"), font=F(int(size * 0.45), True))
    except Exception:
        pass


def load_icon_async(url, canvas, letter="?"):
    if not url:
        return
    if url in _ICON_CACHE:
        _paint_icon(canvas, _ICON_CACHE[url])
        return
    global _ICON_RUNNING
    with _ICON_LOCK:
        _ICON_QUEUE.append((url, canvas))
        if _ICON_RUNNING:
            return
        _ICON_RUNNING = True
    threading.Thread(target=_icon_worker, daemon=True).start()


def _icon_worker():
    global _ICON_RUNNING
    import requests
    while True:
        with _ICON_LOCK:
            if not _ICON_QUEUE:
                _ICON_RUNNING = False
                return
            url, canvas = _ICON_QUEUE.pop(0)
        try:
            data = _ICON_CACHE.get(url)
            if data is None:
                r = requests.get(url, headers=_MR_HEADERS, timeout=15)
                r.raise_for_status()
                data = r.content
                _ICON_CACHE[url] = data
            _ui_call(_paint_icon, canvas, data)
        except Exception:
            pass


def _paint_icon(canvas, data):
    try:
        if not canvas.winfo_exists():
            return
        tw = int(canvas.cget("width"))
        th = int(canvas.cget("height"))
        img = tk.PhotoImage(data=data)
        limit = max(8, min(tw, th))
        scale = int(round(max(img.width(), img.height()) / limit))
        k = max(1, scale)
        if k > 1:
            img = img.subsample(k, k)
        canvas.delete("all")
        canvas.create_image(tw / 2, th / 2, image=img)
        canvas._icon_photo = img
    except Exception:
        pass


def _mod_query_from_filename(name):
    """把 jar 文件名清理成适合搜索的关键词（去掉加载器/版本后缀）。"""
    parts = [p for p in re.split(r"[-_ ]+", name or "") if p]
    out = []
    for i, p in enumerate(parts):
        if i > 0 and re.match(r"^(fabric|forge|neoforge|quilt|mc\d|[vV]?\d)",
                              p, re.I):
            break
        out.append(p)
    return " ".join(out) if out else (name or "")


def _bind_tree(widget, left=None, right=None):
    try:
        if left is not None:
            widget.bind("<Button-1>", left)
        if right is not None:
            widget.bind("<Button-3>", right)
    except Exception:
        pass
    for child in widget.winfo_children():
        _bind_tree(child, left, right)


# ============ 模组 / 光影 下载页 ============
class StoreView(tk.Frame):
    """模组或光影的浏览 + 详情 + 下载位置选择（全部内嵌，不使用弹窗）。"""

    def __init__(self, parent, app, kind):
        super().__init__(parent, bg=C("bg"))
        self.app = app
        self.kind = kind                       # "mod" / "shader" / "modpack"
        self.project_type = {"mod": "mod", "shader": "shader",
                             "modpack": "modpack"}.get(kind, "mod")
        self.source = "modrinth"               # "modrinth" / "curseforge"
        self._loaded = False
        self._loading = False
        self._query = ""
        self._offset = 0
        self._hits = []
        self._pid = ""
        self._project = {}
        self._versions = []
        self._dep_projects = {}
        self._game_versions = []
        self._gv = ""
        self._gv_btns = {}
        self._matches = []
        self._build_ui()

    # ---------- 浏览列表 ----------
    def _build_ui(self):
        self.browse = tk.Frame(self, bg=C("bg"))
        self.browse.pack(fill="both", expand=True)
        src_row = tk.Frame(self.browse, bg=C("bg"))
        src_row.pack(fill="x", pady=(0, 6))
        tk.Label(src_row, text=_t("下载源："), bg=C("bg"), fg=C("text2"),
                 font=F(12)).pack(side="left")
        self.btn_src_mr = FlatButton(src_row, "Modrinth",
                                     lambda: self.set_source("modrinth"),
                                     bg="accent", fg="#1e1e2e",
                                     hover="accent_hover", padx=12, pady=4,
                                     font_size=11, bold=True)
        self.btn_src_mr.pack(side="left", padx=(8, 6))
        self.btn_src_cf = FlatButton(src_row, "CurseForge",
                                     lambda: self.set_source("curseforge"),
                                     bg="card", fg="text", hover="border",
                                     padx=12, pady=4, font_size=11, bold=True)
        self.btn_src_cf.pack(side="left")
        bar = tk.Frame(self.browse, bg=C("bg"))
        bar.pack(fill="x", pady=(0, 8))
        self.search_var = tk.StringVar()
        ewrap, ent = rounded_entry(bar, textvariable=self.search_var,
                                   font_size=12)
        ewrap.pack(side="left", fill="x", expand=True)
        ent.bind("<Return>", lambda e: self.search())
        AccentButton(bar, _t("搜索"), self.search, width=90, height=32,
                     font_size=12).pack(side="left", padx=(8, 0))
        self.status = tk.Label(self.browse, text="", bg=C("bg"), fg=C("muted"),
                               font=F(11), anchor="w")
        self.status.pack(fill="x")
        self.scroll = ScrollFrame(self.browse, bg="bg")
        self.scroll.pack(fill="both", expand=True, pady=(6, 4))
        for i in range(3):
            self.scroll.inner.grid_columnconfigure(i, weight=1, uniform="card")
        more = tk.Frame(self.browse, bg=C("bg"))
        more.pack(fill="x")
        self.more_btn = FlatButton(more, _t("加载更多"), self.load_more, bg="card",
                                   fg="text", hover="border", padx=14, pady=5,
                                   font_size=12)
        self.more_btn.pack(side="left")
        self.more_btn.setEnabled(False)
        self.detail = tk.Frame(self, bg=C("bg"))
        self._built_detail = False

    def ensure_loaded(self):
        if not self._loaded and not self._loading:
            self.search()

    def load_more(self):
        self.search(append=True)

    def set_source(self, src):
        """切换下载源（Modrinth / CurseForge）并刷新列表。"""
        if src == getattr(self, "source", "modrinth"):
            return
        self.source = src
        self._hits = []
        self._offset = 0
        self._loaded = False
        self._loading = False  # 允许立即按新源搜索；旧源结果按标记丢弃
        try:
            self.scroll.clear()
        except Exception:
            pass
        for b, active in ((self.btn_src_mr, src == "modrinth"),
                          (self.btn_src_cf, src == "curseforge")):
            set_roles(b, bg="accent" if active else "card",
                      fg="#1e1e2e" if active else "text")
        self.search()

    def search(self, append=False):
        if self._loading:
            return
        self._query = self.search_var.get().strip()
        if not append:
            self._offset = 0
            self._hits = []
        self._loading = True
        self.more_btn.setEnabled(False)
        self.status.configure(text=_t("正在获取列表 ..."), fg=C("muted"))
        ptype, off, q = self.project_type, self._offset, self._query
        src = self.source

        def _ok(data, _src=src):
            if _src != self.source:
                return  # 来源已切换，丢弃过期结果
            self._on_search_ok(data)

        def _err(err, _src=src):
            if _src != self.source:
                return
            self._on_search_err(err)

        if src == "curseforge":
            _run_bg(lambda: _cf_search(ptype, q, offset=off, limit=20),
                    _ok, _err)
        else:
            _run_bg(lambda: _mr_search(ptype, q, offset=off, limit=20),
                    _ok, _err)

    def _on_search_ok(self, data):
        self._loading = False
        self._loaded = True
        hits = data.get("hits") or []
        self._hits.extend(hits)
        self._offset += len(hits)
        total = int(data.get("total_hits") or 0)
        self.status.configure(text="%s：%d / %d" % (_t("找到"), len(self._hits), total),
                              fg=C("muted"))
        self.more_btn.setEnabled(bool(hits) and self._offset < total)
        self._render_hits()

    def _on_search_err(self, err):
        self._loading = False
        self._loaded = True
        msg = str(err)
        if self.source == "curseforge" and not _cf_key():
            msg += _t("（CurseForge 公共代理不可用，可在设置页填写 CurseForge API Key 后重试）")
        self.status.configure(text=_t("获取失败：") + msg, fg=C("red1"))
        self.more_btn.setEnabled(True)

    def _render_hits(self):
        self.scroll.clear()
        for idx, hit in enumerate(self._hits):
            card = self._make_card(hit)
            card.grid(row=idx // 3, column=idx % 3, padx=5, pady=5, sticky="nsew")

    def _make_card(self, hit):
        title = hit.get("title") or hit.get("slug") or "?"
        loader = _hit_loaders(hit) or (
            {"shader": _t("光影"), "modpack": _t("整合包")}.get(
                self.kind, _t("通用")))
        card = RoundedCard(self.scroll.inner, radius=10, padx=10, pady=10,
                           cursor="hand2")
        top = tk.Frame(card.inner, bg=C("card"))
        top.pack(fill="x")
        icon = tk.Canvas(top, width=56, height=56, bg=C("panel"),
                         highlightthickness=0)
        icon.pack(side="left")
        _icon_placeholder(icon, title, 56)
        load_icon_async(hit.get("icon_url"), icon, title)
        info = tk.Frame(top, bg=C("card"))
        info.pack(side="left", fill="both", expand=True, padx=(8, 0))
        tk.Label(info, text=title, bg=C("card"), fg=C("text"), font=F(13, True),
                 anchor="w", justify="left", wraplength=175).pack(fill="x")
        tk.Label(info, text=loader, bg=C("card"), fg=C("accent"), font=F(10, True),
                 anchor="w").pack(fill="x", pady=(2, 0))
        author = hit.get("author") or ""
        if author:
            tk.Label(info, text=_t("作者：") + author, bg=C("card"),
                     fg=C("text2"), font=F(10), anchor="w").pack(fill="x")
        desc = (hit.get("description") or "").strip()
        if desc:
            tk.Label(card.inner, text=desc, bg=C("card"), fg=C("muted"),
                     font=F(10), anchor="w", justify="left", wraplength=220,
                     height=2).pack(fill="x", pady=(6, 0))
        pid = hit.get("project_id") or hit.get("slug") or ""
        _bind_tree(card, left=lambda _e, p=pid: self.open_project(p))
        return card

    # ---------- 详情 ----------
    def open_project(self, pid):
        if not pid:
            return
        self.browse.pack_forget()
        self.detail.pack(fill="both", expand=True)
        if not self._built_detail:
            self._build_detail()
            self._built_detail = True
        self._pid = pid
        self.detail_status.configure(text=_t("正在加载项目信息 ..."),
                                     fg=C("muted"))
        for w in self.detail_body.winfo_children():
            w.destroy()
        src = self.source

        def _ok(data, _pid=pid, _src=src):
            if _pid != self._pid or _src != self.source:
                return  # 已切换项目或下载源，丢弃过期结果
            self._on_project_ok(data)

        def _err(err, _pid=pid, _src=src):
            if _pid != self._pid or _src != self.source:
                return
            self._on_project_err(err)

        _run_bg(lambda: self._fetch_project(pid), _ok, _err)

    def _fetch_project(self, pid):
        if self.source == "curseforge":
            p = _cf_project(pid)
            vers = _cf_versions(pid)
            dep_ids = set()
            for v in vers:
                for d in v.get("dependencies") or []:
                    if d.get("dependency_type") == "required" and d.get("project_id"):
                        dep_ids.add(d["project_id"])
            deps = {}
            try:
                for pr in _cf_projects(sorted(dep_ids)):
                    deps[pr["id"]] = pr
            except Exception:
                pass
            return p, vers, deps, p.get("authors") or []
        p = _mr_project(pid)
        vers = _mr_versions(pid)
        dep_ids = set()
        for v in vers:
            for d in v.get("dependencies") or []:
                if d.get("dependency_type") == "required" and d.get("project_id"):
                    dep_ids.add(d["project_id"])
        deps = {}
        try:
            for pr in _mr_projects(sorted(dep_ids)):
                deps[pr["id"]] = pr
        except Exception:
            pass
        members = _mr_members(pid)
        return p, vers, deps, members

    def _build_detail(self):
        top = tk.Frame(self.detail, bg=C("bg"))
        top.pack(fill="x")
        FlatButton(top, "← " + _t("返回列表"), self.back_to_list, bg="card",
                   fg="text", hover="border", padx=12, pady=4,
                   font_size=12).pack(side="left")
        self.detail_status = tk.Label(top, text="", bg=C("bg"), fg=C("muted"),
                                      font=F(11), anchor="w")
        self.detail_status.pack(side="left", padx=10)
        self.detail_scroll = ScrollFrame(self.detail, bg="bg")
        self.detail_scroll.pack(fill="both", expand=True, pady=(6, 0))
        self.detail_body = self.detail_scroll.inner

    def back_to_list(self):
        self.detail.pack_forget()
        self.browse.pack(fill="both", expand=True)
        if not self._loaded:
            self.ensure_loaded()

    def _on_project_err(self, err):
        self.detail_status.configure(text=_t("加载失败：") + str(err), fg=C("red1"))

    def _on_project_ok(self, data):
        p, vers, deps, members = data
        self._project, self._versions = p, vers
        self._dep_projects = deps
        self.detail_status.configure(text="")
        body = self.detail_body
        for w in body.winfo_children():
            w.destroy()
        head = tk.Frame(body, bg=C("bg"))
        head.pack(fill="x")
        icon = tk.Canvas(head, width=84, height=84, bg=C("panel"),
                         highlightthickness=0)
        icon.pack(side="left")
        _icon_placeholder(icon, p.get("title") or "?", 84)
        load_icon_async(p.get("icon_url"), icon, p.get("title"))
        meta = tk.Frame(head, bg=C("bg"))
        meta.pack(side="left", fill="both", expand=True, padx=(12, 0))
        tk.Label(meta, text=p.get("title") or "?", bg=C("bg"), fg=C("text"),
                 font=F(18, True), anchor="w").pack(fill="x")
        loaders = _loader_text(p.get("loaders") or [])
        tk.Label(meta, text=loaders or _t("通用"), bg=C("bg"), fg=C("accent"),
                 font=F(11, True), anchor="w").pack(fill="x", pady=(2, 0))
        author_txt = " / ".join(members[:3])
        if author_txt:
            tk.Label(meta, text=_t("作者：") + author_txt, bg=C("bg"),
                     fg=C("text2"), font=F(10), anchor="w").pack(fill="x")
        tk.Label(meta, text=_t("下载量：") + _fmt_num(p.get("downloads")),
                 bg=C("bg"), fg=C("muted"), font=F(10), anchor="w").pack(fill="x")
        btns = tk.Frame(meta, bg=C("bg"))
        btns.pack(fill="x", pady=(6, 0))
        ptype = p.get("project_type") or self.project_type
        if self.source == "curseforge":
            # CurseForge 源：官网直达链接来自接口返回的 websiteUrl
            cf_url = p.get("cf_page") or (
                "https://www.curseforge.com/minecraft/search?search=%s"
                % urllib.parse.quote(p.get("title") or ""))
            mr_url = ("https://modrinth.com/search?q=%s"
                      % urllib.parse.quote(p.get("title") or ""))
            FlatButton(btns, _t("转到 CurseForge"),
                       lambda: webbrowser.open(cf_url), bg="accent",
                       fg="#1e1e2e", hover="accent_hover", padx=12, pady=5,
                       font_size=12).pack(side="left")
            FlatButton(btns, _t("转到 Modrinth"),
                       lambda: webbrowser.open(mr_url), bg="card", fg="text",
                       hover="border", padx=12, pady=5,
                       font_size=12).pack(side="left", padx=8)
        else:
            src_url = "https://modrinth.com/%s/%s" % (ptype, p.get("slug") or "")
            cf_url = ("https://www.curseforge.com/minecraft/search?search=%s"
                      % urllib.parse.quote(p.get("title") or ""))
            FlatButton(btns, _t("转到 Modrinth"), lambda: webbrowser.open(src_url),
                       bg="accent", fg="#1e1e2e", hover="accent_hover", padx=12,
                       pady=5, font_size=12).pack(side="left")
            FlatButton(btns, _t("转到 CurseForge"), lambda: webbrowser.open(cf_url),
                       bg="card", fg="text", hover="border", padx=12, pady=5,
                       font_size=12).pack(side="left", padx=8)
        desc = (p.get("description") or "").strip()
        if desc:
            tk.Label(body, text=desc, bg=C("bg"), fg=C("text2"), font=F(11),
                     anchor="w", justify="left", wraplength=780).pack(
                fill="x", pady=(10, 0))

        self._game_versions = sorted(
            {g for v in vers for g in (v.get("game_versions") or [])},
            key=_game_version_key, reverse=True)
        tk.Label(body, text=_t("支持的游戏版本（点击查看对应版本列表）"),
                 bg=C("bg"), fg=C("text"), font=F(13, True)).pack(
            anchor="w", pady=(14, 4))
        chips = tk.Frame(body, bg=C("bg"))
        chips.pack(fill="x")
        self._gv_btns = {}
        row = col = 0
        for gv in self._game_versions:
            b = FlatButton(chips, gv, lambda g=gv: self.select_game_version(g),
                           bg="card", fg="text", hover="border", padx=8,
                           pady=3, font_size=10)
            b.grid(row=row, column=col, padx=3, pady=3)
            self._gv_btns[gv] = b
            col += 1
            if col >= 10:
                col, row = 0, row + 1
        self.ver_title = tk.Label(body, text="", bg=C("bg"), fg=C("text"),
                                  font=F(13, True), anchor="w")
        self.ver_title.pack(fill="x", pady=(14, 4))
        self.ver_box = tk.Frame(body, bg=C("bg"))
        self.ver_box.pack(fill="x")
        self.target_box = tk.Frame(body, bg=C("bg"))
        self.target_box.pack(fill="x", pady=(12, 0))
        if self._game_versions:
            self.select_game_version(self._game_versions[0])
        else:
            self.ver_title.configure(text=_t("暂无可用版本"))

    def select_game_version(self, gv):
        self._gv = gv
        for g, b in self._gv_btns.items():
            set_roles(b, bg="accent" if g == gv else "card",
                      fg="#1e1e2e" if g == gv else "text")
        self._matches = [v for v in self._versions
                         if gv in (v.get("game_versions") or [])]
        self._matches.sort(key=lambda v: v.get("date_published") or "",
                           reverse=True)
        self.ver_title.configure(
            text="%s %s：%d" % (gv, _t("可用版本"), len(self._matches)))
        for w in self.ver_box.winfo_children():
            w.destroy()
        for w in self.target_box.winfo_children():
            w.destroy()
        if not self._matches:
            tk.Label(self.ver_box, text=_t("该游戏版本下暂无可用版本。"),
                     bg=C("bg"), fg=C("muted"), font=F(11)).pack(anchor="w")
            return
        for ver in self._matches:
            self._make_version_row(ver)

    def _make_version_row(self, ver):
        row = RoundedCard(self.ver_box, radius=8, padx=10, pady=7,
                          cursor="hand2")
        row.pack(fill="x", pady=3)
        r1 = tk.Frame(row.inner, bg=C("card"))
        r1.pack(fill="x")
        tk.Label(r1, text=ver.get("version_number") or ver.get("name") or "?",
                 bg=C("card"), fg=C("text"), font=F(12, True),
                 anchor="w").pack(side="left")
        lds = _loader_text(ver.get("loaders") or [])
        if lds:
            tk.Label(r1, text=lds, bg=C("card"), fg=C("accent"),
                     font=F(10, True)).pack(side="left", padx=8)
        date = str(ver.get("date_published") or "")[:10]
        size = _file_size(ver)
        tk.Label(r1, text=(date + ("    " + size if size else "")),
                 bg=C("card"), fg=C("muted"), font=F(10)).pack(side="right")
        r2 = tk.Frame(row.inner, bg=C("card"))
        r2.pack(fill="x", pady=(3, 0))
        deps = [d for d in (ver.get("dependencies") or [])
                if d.get("dependency_type") == "required" and d.get("project_id")]
        prereq_labels = []
        if deps:
            tk.Label(r2, text=_t("前置："), bg=C("card"), fg=C("muted"),
                     font=F(10)).pack(side="left")
            for d in deps[:5]:
                dpid = d["project_id"]
                pr = self._dep_projects.get(dpid) or {}
                nm = pr.get("title") or str(dpid)[:8]
                lb = tk.Label(r2, text=nm, bg=C("card"), fg=C("accent"),
                              font=F(10, True), cursor="hand2")
                lb.pack(side="left", padx=(0, 8))
                prereq_labels.append((lb, dpid))
        else:
            tk.Label(r2, text=_t("无前置模组"), bg=C("card"), fg=C("muted"),
                     font=F(10)).pack(side="left")
        _bind_tree(row, left=lambda _e, v=ver: self.choose_version(v))
        for lb, dpid in prereq_labels:
            lb.bind("<Button-1>", lambda _e, p=dpid: self.open_project(p))

    def choose_version(self, ver):
        self._render_targets(ver)

    def _render_targets(self, ver):
        box = self.target_box
        for w in box.winfo_children():
            w.destroy()
        if self.kind == "modpack":
            self._render_pack_targets(box, ver)
            return
        tk.Label(box, text=_t("选择下载位置"), bg=C("bg"), fg=C("text"),
                 font=F(13, True), anchor="w").pack(fill="x")
        fname = _primary_file(ver).get("filename") or ""
        tk.Label(box, text=_t("文件：") + (fname or "?"), bg=C("bg"),
                 fg=C("muted"), font=F(10), anchor="w").pack(fill="x", pady=(2, 6))
        mc = load_config().get("minecraft_directory", "")
        insts = list_local_instances(mc)
        gv = self._gv
        loaders = ver.get("loaders") or []
        need = _loader_text([x for x in loaders if str(x).lower() != "minecraft"])
        if self.kind == "mod":
            matched = [i for i in insts if i["mcver"] == gv
                       and _loader_matches(i["loader"], loaders)]
        else:
            matched = [i for i in insts if i["mcver"] == gv]
        if matched:
            tk.Label(box, text=_t("检测到以下实例，点击即可下载到对应文件夹："),
                     bg=C("bg"), fg=C("text2"), font=F(11),
                     anchor="w").pack(fill="x", pady=(0, 4))
            for inst in matched[:10]:
                if self.kind == "mod":
                    dest = instance_mods_dir(mc, inst["name"])
                else:
                    dest = instance_shader_dir(mc, inst["name"])
                ld = _loader_text([inst["loader"]]) or _t("原版")
                b = FlatButton(box, "%s（%s）" % (inst["name"], ld),
                               lambda d=dest, v=ver: self.start_download(v, d),
                               bg="card", fg="text", hover="border", padx=10,
                               pady=5, font_size=11)
                b.pack(anchor="w", pady=2)
                tk.Label(box, text="    " + dest, bg=C("bg"), fg=C("muted"),
                         font=F(9), anchor="w").pack(fill="x")
        else:
            if self.kind == "mod":
                msg = _t("没有检测到 %s %s 客户端。") % (need or _t("对应加载器"), gv)
            else:
                msg = _t("没有检测到 %s 的客户端。") % gv
            tk.Label(box, text=msg, bg=C("bg"), fg=C("yellow"), font=F(11),
                     anchor="w", wraplength=760).pack(fill="x")
            tk.Label(box, text=_t("可以点击下方按钮浏览并选择要下载到的文件夹。"),
                     bg=C("bg"), fg=C("muted"), font=F(10),
                     anchor="w").pack(fill="x", pady=(2, 0))
        FlatButton(box, _t("浏览其他位置..."),
                   lambda v=ver: self._browse_target(v), bg="card", fg="accent",
                   hover="border", padx=12, pady=5, font_size=11).pack(
            anchor="w", pady=(8, 0))
        self.dl_status = tk.Label(box, text="", bg=C("bg"), fg=C("muted"),
                                  font=F(11), anchor="w", wraplength=780)
        self.dl_status.pack(fill="x", pady=(6, 0))

    def _render_pack_targets(self, box, ver):
        """整合包：下载后自动进入导入流程，无需选择实例。"""
        tk.Label(box, text=_t("下载整合包"), bg=C("bg"), fg=C("text"),
                 font=F(13, True), anchor="w").pack(fill="x")
        fname = _primary_file(ver).get("filename") or ""
        tk.Label(box, text=_t("文件：") + (fname or "?"), bg=C("bg"),
                 fg=C("muted"), font=F(10), anchor="w").pack(fill="x",
                                                          pady=(2, 6))
        tk.Label(box,
                 text=_t("下载完成后会自动识别整合包类型，并进入导入流程。"),
                 bg=C("bg"), fg=C("text2"), font=F(11),
                 anchor="w", wraplength=760).pack(fill="x")
        AccentButton(box, _t("下载并导入"), lambda v=ver: self.start_download(
            v, os.path.join(DATA_DIR, "pack_downloads")), width=150, height=34,
            font_size=12).pack(anchor="w", pady=(8, 0))
        self.dl_status = tk.Label(box, text="", bg=C("bg"), fg=C("muted"),
                                  font=F(11), anchor="w", wraplength=780)
        self.dl_status.pack(fill="x", pady=(6, 0))

    def _browse_target(self, ver):
        path = filedialog.askdirectory(parent=self,
                                       title=_t("选择下载到的文件夹"))
        if path:
            self.start_download(ver, path)

    def start_download(self, ver, dest_dir):
        f = _primary_file(ver)
        url, fname = f.get("url"), f.get("filename")
        if not fname:
            try:
                self.dl_status.configure(text=_t("该版本没有可下载文件。"),
                                         fg=C("red1"))
            except Exception:
                pass
            return
        try:
            os.makedirs(dest_dir, exist_ok=True)
        except Exception as e:
            alert(self, _t("下载失败"), str(e), "error")
            return
        dst = os.path.join(dest_dir, fname)
        try:
            self.dl_status.configure(text=_t("正在下载：") + fname + "  0%",
                                     fg=C("text2"))
        except Exception:
            pass

        def _job():
            u = url
            if not u and ver.get("_cf_mod_id"):
                # CurseForge 部分文件不在列表中返回下载地址，需要单独换取
                u = _cf_mod_download_url(ver["_cf_mod_id"],
                                         ver.get("_cf_file_id") or "",
                                         _cf_key())
            if not u:
                raise RuntimeError(
                    _t("该文件没有可用下载地址（作者可能禁止了第三方下载）。"))
            return _download_to(u, dst,
                                lambda p: _ui_call(self._on_dl_progress, p))

        _run_bg(_job, lambda path: self._on_dl_ok(path), self._on_dl_err)

    def _on_dl_progress(self, percent):
        try:
            self.dl_status.configure(
                text=_t("正在下载：") + "%d%%" % percent, fg=C("text2"))
        except Exception:
            pass

    def _on_dl_ok(self, path):
        if self.kind == "modpack":
            try:
                page = self.app.pages.get("download")
                if page is not None and hasattr(page, "open_import_pack"):
                    page.open_import_pack(path)
                    return
            except Exception:
                pass
        try:
            self.dl_status.configure(text=_t("已下载到：") + path,
                                     fg=C("log_fg"), wraplength=780)
        except Exception:
            pass

    def _on_dl_err(self, err):
        try:
            self.dl_status.configure(text=_t("下载失败：") + str(err),
                                     fg=C("red1"))
        except Exception:
            pass


# ============ 模组管理（管理版本页内嵌） ============
class ModManagerView(tk.Frame):
    def __init__(self, parent, app, version, on_back):
        super().__init__(parent, bg=C("bg"))
        self.app = app
        self.version = version
        self.on_back = on_back
        self.mc = load_config().get("minecraft_directory", "")
        self.mods_dir = instance_mods_dir(self.mc, version)
        self.disabled_dir = os.path.join(os.path.dirname(self.mods_dir),
                                         "mods_disabled")
        self.items = []
        self.selected = None
        self._cards = {}
        self._checking = False
        self._updating = False
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        top = tk.Frame(self, bg=C("bg"))
        top.pack(fill="x")
        FlatButton(top, "← " + _t("返回版本列表"), self.on_back, bg="card",
                   fg="text", hover="border", padx=12, pady=4,
                   font_size=12).pack(side="left")
        tk.Label(top, text=_t("模组管理：") + self.version, bg=C("bg"),
                 fg=C("text"), font=F(16, True)).pack(side="left", padx=10)
        acts = tk.Frame(self, bg=C("bg"))
        acts.pack(fill="x", pady=(8, 0))
        self.btn_check = FlatButton(acts, _t("检查更新"), self.check_updates,
                                    bg="card", fg="text", hover="border",
                                    padx=12, pady=5, font_size=12)
        self.btn_check.pack(side="left")
        self.btn_update = FlatButton(acts, _t("一键更新"), self.update_all,
                                     bg="accent", fg="#1e1e2e",
                                     hover="accent_hover", padx=12, pady=5,
                                     font_size=12)
        self.btn_update.pack(side="left", padx=8)
        self.btn_update.setEnabled(False)
        self.status = tk.Label(acts, text="", bg=C("bg"), fg=C("muted"),
                               font=F(11), anchor="w")
        self.status.pack(side="left", padx=8, fill="x", expand=True)
        self.scroll = ScrollFrame(self, bg="bg")
        self.scroll.pack(fill="both", expand=True, pady=(8, 6))
        for i in range(2):
            self.scroll.inner.grid_columnconfigure(i, weight=1, uniform="mod")
        bottom = tk.Frame(self, bg=C("bg"))
        bottom.pack(fill="x")
        self.btn_disable = FlatButton(bottom, _t("禁用所选模组"),
                                      lambda: self.toggle_selected(False),
                                      bg="card", fg="red1", hover="border",
                                      padx=12, pady=5, font_size=12)
        self.btn_disable.pack(side="left")
        self.btn_enable = FlatButton(bottom, _t("启用所选模组"),
                                     lambda: self.toggle_selected(True),
                                     bg="card", fg="accent", hover="border",
                                     padx=12, pady=5, font_size=12)
        self.btn_enable.pack(side="left", padx=8)
        self.btn_disable.setEnabled(False)
        self.btn_enable.setEnabled(False)
        tk.Label(bottom, text=_t("左键选择模组，右键前往该模组的下载页"),
                 bg=C("bg"), fg=C("muted"), font=F(10)).pack(side="right")

    # ---------- 扫描与展示 ----------
    def _scan(self):
        items = []
        for base, enabled in ((self.mods_dir, True), (self.disabled_dir, False)):
            try:
                names = sorted(os.listdir(base))
            except Exception:
                continue
            for fn in names:
                if not fn.lower().endswith(".jar"):
                    continue
                p = os.path.join(base, fn)
                if not os.path.isfile(p):
                    continue
                try:
                    size = os.path.getsize(p)
                except Exception:
                    size = 0
                items.append({"file": p, "name": fn[:-4], "enabled": enabled,
                              "size": size, "project": None, "icon": "",
                              "title": fn[:-4], "loaders": "", "update": None,
                              "sha1": ""})
        return items

    def refresh(self):
        self.items = self._scan()
        self.selected = None
        self._render()
        self._update_actions()

    def _render(self):
        self.scroll.clear()
        self._cards = {}
        if not self.items:
            tk.Label(self.scroll.inner, text=_t("该版本暂未安装模组。"),
                     bg=C("bg"), fg=C("muted"), font=F(12)).pack(pady=20)
            return
        for idx, it in enumerate(self.items):
            card = RoundedCard(self.scroll.inner, radius=10, padx=10, pady=8,
                               cursor="hand2")
            card.grid(row=idx // 2, column=idx % 2, padx=5, pady=5,
                      sticky="nsew")
            top = tk.Frame(card.inner, bg=C("card"))
            top.pack(fill="x")
            icon = tk.Canvas(top, width=44, height=44, bg=C("panel"),
                             highlightthickness=0)
            icon.pack(side="left")
            _icon_placeholder(icon, it["title"], 44)
            load_icon_async(it.get("icon"), icon, it["title"])
            info = tk.Frame(top, bg=C("card"))
            info.pack(side="left", fill="both", expand=True, padx=(8, 0))
            tk.Label(info, text=it["title"], bg=C("card"), fg=C("text"),
                     font=F(12, True), anchor="w", justify="left",
                     wraplength=210).pack(fill="x")
            meta = []
            if it.get("loaders"):
                meta.append(it["loaders"])
            meta.append("%.0f KB" % (it["size"] / 1024.0))
            if not it["enabled"]:
                meta.append(_t("已禁用"))
            tk.Label(info, text="    ".join(meta), bg=C("card"), fg=C("text2"),
                     font=F(10), anchor="w").pack(fill="x", pady=(2, 0))
            if it.get("update"):
                vname = it["update"].get("version_number") or "?"
                tk.Label(info, text=_t("可更新 → ") + vname, bg=C("card"),
                         fg=C("yellow"), font=F(10, True),
                         anchor="w").pack(fill="x")
            _bind_tree(card,
                       left=lambda _e, x=it: self._select(x),
                       right=lambda _e, x=it: self._goto_download(x))
            self._cards[it["file"]] = card
        if self.selected and self.selected["file"] in self._cards:
            self._highlight(self.selected)

    def _select(self, it):
        self.selected = it
        self._highlight(it)
        self._update_actions()

    def _highlight(self, it):
        for path, card in self._cards.items():
            try:
                card.set_selected(path == it["file"])
            except Exception:
                pass

    def _update_actions(self):
        it = self.selected
        self.btn_disable.setEnabled(bool(it and it["enabled"]))
        self.btn_enable.setEnabled(bool(it and not it["enabled"]))
        has_update = any(x.get("update") for x in self.items)
        self.btn_update.setEnabled(bool(has_update) and not self._updating)

    def _goto_download(self, it):
        pid = (it.get("project") or {}).get("id")
        page = self.app.pages.get("download")
        if not page:
            return
        if pid:
            page.open_mod_project(pid)
        else:
            page.open_mod_search(_mod_query_from_filename(it["title"]))

    # ---------- 更新检查 ----------
    def check_updates(self):
        if self._checking or self._updating:
            return
        self._checking = True
        self.btn_check.setEnabled(False)
        self.btn_update.setEnabled(False)
        self.status.configure(text=_t("正在检查更新 ..."), fg=C("muted"))
        items = [it for it in self.items if it["enabled"]]
        mcver, loader, lver = detect_instance_loader(self.mc, self.version)
        loaders = _instance_loader_family(loader)
        _run_bg(lambda: self._do_check(items, mcver, loaders),
                self._on_check_ok, self._on_check_err)

    def _do_check(self, items, mcver, loaders):
        hashes = []
        for it in items:
            it["sha1"] = _sha1_file(it["file"])
            if it["sha1"]:
                hashes.append(it["sha1"])
        if not hashes:
            return items, {}, {}
        cur = _mr_request("POST", "/version_files",
                          payload={"hashes": hashes, "algorithm": "sha1"})
        cur = cur or {}
        payload = {"hashes": hashes, "algorithm": "sha1"}
        if loaders:
            payload["loaders"] = loaders
        if mcver:
            payload["game_versions"] = [mcver]
        try:
            latest = _mr_request("POST", "/version_files/update", payload=payload)
        except Exception:
            latest = {}
        latest = latest or {}
        pids = {(v or {}).get("project_id") for v in cur.values()}
        projects = {}
        try:
            for pr in _mr_projects(sorted(p for p in pids if p)):
                projects[pr["id"]] = pr
        except Exception:
            pass
        return items, cur, latest, projects

    def _on_check_ok(self, data):
        self._checking = False
        self.btn_check.setEnabled(True)
        if len(data) == 3:
            items, cur, latest = data
            projects = {}
        else:
            items, cur, latest, projects = data
        for it in items:
            cv = cur.get(it["sha1"]) or {}
            pr = projects.get(cv.get("project_id")) or {}
            if pr:
                it["project"] = pr
                it["title"] = pr.get("title") or it["title"]
                it["icon"] = pr.get("icon_url") or ""
                it["loaders"] = _loader_text(pr.get("loaders") or [])
            lv = latest.get(it["sha1"]) or {}
            if lv and cv and lv.get("id") != cv.get("id"):
                it["update"] = lv
        n = sum(1 for it in self.items if it.get("update"))
        self.status.configure(
            text=(_t("发现 %d 个可更新模组") % n if n
                  else _t("全部模组均为最新版本")),
            fg=C("log_fg") if not n else C("yellow"))
        self._render()
        self._update_actions()

    def _on_check_err(self, err):
        self._checking = False
        self.btn_check.setEnabled(True)
        self.status.configure(text=_t("检查失败：") + str(err), fg=C("red1"))
        self._update_actions()

    def update_all(self):
        ups = [it for it in self.items if it.get("update") and it["enabled"]]
        if not ups or self._updating:
            return
        self._updating = True
        self.btn_check.setEnabled(False)
        self.btn_update.setEnabled(False)
        self.status.configure(text=_t("正在更新模组 ..."), fg=C("muted"))
        _run_bg(lambda: self._do_updates(ups), self._on_update_ok,
                self._on_update_err)

    def _do_updates(self, ups):
        total = len(ups)
        for idx, it in enumerate(ups, 1):
            f = _primary_file(it["update"])
            url, fname = f.get("url"), f.get("filename")
            if not url or not fname:
                continue
            dst = os.path.join(os.path.dirname(it["file"]), fname)
            _ui_call(self._set_status,
                     _t("正在更新 %d/%d：%s") % (idx, total, fname))
            _download_to(url, dst)
            old = it["file"]
            if os.path.abspath(dst) != os.path.abspath(old) and os.path.isfile(old):
                os.remove(old)
        return total

    def _set_status(self, text):
        try:
            self.status.configure(text=text, fg=C("text2"))
        except Exception:
            pass

    def _on_update_ok(self, total):
        self._updating = False
        self.status.configure(text=_t("已更新 %d 个模组") % total, fg=C("log_fg"))
        self.btn_check.setEnabled(True)
        self.refresh()

    def _on_update_err(self, err):
        self._updating = False
        self.btn_check.setEnabled(True)
        self.status.configure(text=_t("更新失败：") + str(err), fg=C("red1"))
        self._update_actions()

    def toggle_selected(self, enable):
        it = self.selected
        if not it:
            return
        dest_dir = self.mods_dir if enable else self.disabled_dir
        try:
            os.makedirs(dest_dir, exist_ok=True)
            dst = os.path.join(dest_dir, os.path.basename(it["file"]))
            if os.path.exists(dst):
                alert(self, _t("操作失败"), _t("目标位置已存在同名文件。"), "error")
                return
            shutil.move(it["file"], dst)
        except Exception as e:
            alert(self, _t("操作失败"), str(e), "error")
            return
        self.refresh()


# ============ 整合包识别 / 文件拖放 ============
def detect_pack_type(path):
    """按压缩包内容识别整合包类型，不依赖扩展名。

    返回 (kind, manifest)：
      ("mrpack", {})  Modrinth 整合包（存在 modrinth.index.json）
      ("zip", dict)   CurseForge 整合包（manifest.json）
      ("", {})        不是有效整合包
    """
    import zipfile
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "modrinth.index.json" in names:
                return "mrpack", {}
            if "manifest.json" in names:
                raw = zf.read("manifest.json")
                mf = json.loads(raw.decode("utf-8-sig", "replace"))
                if isinstance(mf, dict) and (
                        mf.get("manifestType") == "minecraftModpack"
                        or ("minecraft" in mf and "files" in mf)):
                    return "zip", mf
    except Exception:
        return "", {}
    return "", {}


_DROP_PROCS = []  # 保持 WNDPROC 引用，避免被回收


def enable_file_drop(root, callback):
    """Windows 原生文件拖放；其他平台返回 False（点击选择仍可用）。"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        root.update_idletasks()
        hwnd = int(root.winfo_id())
        parent = user32.GetParent(wintypes.HWND(hwnd))
        top = parent or hwnd
        shell32.DragAcceptFiles(wintypes.HWND(top), True)

        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND,
                                     wintypes.UINT, wintypes.WPARAM,
                                     wintypes.LPARAM)
        GWL_WNDPROC = -4
        set_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        get_long = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
        set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        set_long.restype = ctypes.c_void_p
        get_long.argtypes = [wintypes.HWND, ctypes.c_int]
        get_long.restype = ctypes.c_void_p
        user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND,
                                           wintypes.UINT, wintypes.WPARAM,
                                           wintypes.LPARAM]
        user32.CallWindowProcW.restype = ctypes.c_ssize_t
        old = get_long(wintypes.HWND(top), GWL_WNDPROC)

        def _proc(h, msg, wp, lp):
            if msg == 0x0233:  # WM_DROPFILES
                try:
                    hdrop = wintypes.HANDLE(wp)
                    count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
                    paths = []
                    for i in range(count):
                        n = shell32.DragQueryFileW(hdrop, i, None, 0)
                        buf = ctypes.create_unicode_buffer(n + 1)
                        shell32.DragQueryFileW(hdrop, i, buf, n + 1)
                        paths.append(buf.value)
                    shell32.DragFinish(hdrop)
                    if paths:
                        root.after(0, lambda p=list(paths): callback(p))
                except Exception:
                    pass
                return 0
            return user32.CallWindowProcW(old, h, msg, wp, lp)

        proc = WNDPROC(_proc)
        set_long(wintypes.HWND(top), GWL_WNDPROC, ctypes.cast(proc, ctypes.c_void_p))
        _DROP_PROCS.append((proc, old))
        return True
    except Exception:
        return False


# ============ 下载游戏 / 导入整合包 ============
class DownloadPage(Page):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.worker = None
        self.tasks = []
        self._all_versions = []
        self._versions_loaded = False
        self._versions_loading = False
        self._vworker = None
        self._loader_workers = []
        self._loader_error_prompted = set()
        self._suppress_dialogs = False
        self._task_seq = 0
        self._run_tids = []
        self._cancel_ui = False
        self._cancel_poll_job = None
        self._fa_workers = []
        self._fabric_api_ver = ""
        self._fa_list = []
        self._loader_ids = [None]
        self._loader_labels = [_t("原版")]
        self._sub = 0
        self._tab_import = False
        self.imp_worker = None
        self._pack_path = ""
        self._pack_kind = ""
        self._pack_manifest = {}
        self._pack_launch = ""
        self._pack_mc = ""
        self._import_name = ""
        self._build_ui()

    # ---------- 界面 ----------
    def _build_ui(self):
        root = tk.Frame(self, bg=C("bg"))
        root.pack(fill="both", expand=True, padx=20, pady=(12, 14))
        tabs = tk.Frame(root, bg=C("bg")); tabs.pack(fill="x")
        self.tab_dl = FlatButton(tabs, _t("游戏下载"), lambda: self._switch_sub(0),
                                 bg="accent", fg="#1e1e2e", hover="accent_hover",
                                 padx=18, pady=6, font_size=13, bold=True)
        self.tab_dl.pack(side="left", padx=(0, 8))
        self.tab_import = FlatButton(tabs, _t("导入整合包"), lambda: self._switch_sub(1),
                                     bg="card", fg="text", hover="border",
                                     padx=18, pady=6, font_size=13)
        self.tab_import.pack(side="left")
        self.tab_mod = FlatButton(tabs, _t("下载整合包"), lambda: self._switch_sub(2),
                                  bg="card", fg="text", hover="border",
                                  padx=18, pady=6, font_size=13)
        self.tab_mod.pack(side="left", padx=(8, 0))
        self.tab_shader = FlatButton(tabs, _t("模组下载"), lambda: self._switch_sub(3),
                                     bg="card", fg="text", hover="border",
                                     padx=18, pady=6, font_size=13)
        self.tab_shader.pack(side="left", padx=(8, 0))
        self.tab_shader2 = FlatButton(tabs, _t("光影下载"), lambda: self._switch_sub(4),
                                      bg="card", fg="text", hover="border",
                                      padx=18, pady=6, font_size=13)
        self.tab_shader2.pack(side="left", padx=(8, 0))

        body = tk.Frame(root, bg=C("bg"))
        body.pack(fill="both", expand=True, pady=(10, 0))
        self.holder = tk.Frame(body, bg=C("bg"))
        self.holder.pack(side="left", fill="both", expand=True)
        self.dl_view = self._build_download_view(self.holder)
        self.imp_view = self._build_import_view(self.holder)
        self.pack_view = StoreView(self.holder, self.app, "modpack")
        self.mod_view = StoreView(self.holder, self.app, "mod")
        self.shader_view = StoreView(self.holder, self.app, "shader")
        self._views = [self.dl_view, self.imp_view, self.pack_view,
                       self.mod_view, self.shader_view]
        self._tabs = [self.tab_dl, self.tab_import, self.tab_mod,
                      self.tab_shader, self.tab_shader2]
        self.dl_view.pack(fill="both", expand=True)
        # 任务列表常驻在下载页右侧：切换任意子页都能看到全部下载任务
        right = tk.Frame(body, bg=C("bg"), width=300)
        right.pack(side="right", fill="y", padx=(12, 0))
        right.pack_propagate(False)
        tk.Label(right, text=_t("任务列表："), bg=C("bg"), fg=C("text2"),
                 font=F(12)).pack(anchor="w")
        self.active_scroll = ScrollFrame(right, bg="panel")
        self.active_scroll.pack(fill="both", expand=True)
        self._refresh_active_list()
        self.after(300, self._init_drop)

    def _switch_sub(self, i):
        self._sub = i
        for idx, view in enumerate(self._views):
            if idx == i:
                view.pack(fill="both", expand=True)
            else:
                view.pack_forget()
        if i >= 2:
            self._views[i].ensure_loaded()
        self._refresh_tabs()

    def _refresh_tabs(self):
        for idx, tab in enumerate(self._tabs):
            on = idx == self._sub
            set_roles(tab, bg="accent" if on else "card",
                      fg="#1e1e2e" if on else "text")

    def open_mod_project(self, pid):
        self.app.switch_page("download", animate=False)
        self._switch_sub(3)
        # 模组管理中的项目 ID 来自 Modrinth，跳转前强制切回 Modrinth 源
        if self.mod_view.source != "modrinth":
            self.mod_view.set_source("modrinth")
        self.mod_view.open_project(pid)

    def open_mod_search(self, text):
        self.app.switch_page("download", animate=False)
        self._switch_sub(3)
        if self.mod_view.source != "modrinth":
            self.mod_view.set_source("modrinth")
        self.mod_view.back_to_list()
        self.mod_view.search_var.set(text or "")
        self.mod_view.search()

    def _on_theme(self):
        Page._on_theme(self)
        try:
            self._refresh_tabs()
            set_roles(self.queue_list, bg="panel", fg="text",
                      select_bg="card", select_fg="text")
            set_roles(self.imp_opt_list, bg="panel", fg="text",
                      select_bg="accent", select_fg="#1e1e2e")
        except Exception:
            pass

    def _build_download_view(self, parent):
        w = tk.Frame(parent, bg=C("bg"))
        # 第一行：版本类型 + 游戏版本
        r1 = tk.Frame(w, bg=C("bg")); r1.pack(fill="x", pady=(2, 6))
        tk.Label(r1, text=_t("版本类型："), bg=C("bg"), fg=C("text"),
                 font=F(13)).pack(side="left")
        self.type_combo = DropDown(r1, [_t("正式版"), _t("测试版"),
                                        _t("远古版本"), _t("愚人节版")],
                                   width=130, command=lambda i: self._filter_versions())
        self.type_combo.pack(side="left", padx=(4, 20))
        tk.Label(r1, text=_t("游戏版本："), bg=C("bg"), fg=C("text"),
                 font=F(13)).pack(side="left")
        self.version_combo = DropDown(r1, [_t("正在获取版本列表...")], width=240,
                                      command=lambda i: self._on_version_changed())
        self.version_combo.pack(side="left", padx=(4, 0), fill="x", expand=True)

        # 第二行：加载器 + 版本名
        r2 = tk.Frame(w, bg=C("bg")); r2.pack(fill="x", pady=(0, 6))
        tk.Label(r2, text=_t("Mod 加载器："), bg=C("bg"), fg=C("text"),
                 font=F(13)).pack(side="left")
        self.loader_combo = DropDown(r2, [_t("原版")], width=180,
                                     command=lambda i: self._on_loader_changed())
        self.loader_combo.pack(side="left", padx=(4, 20))
        tk.Label(r2, text=_t("版本名："), bg=C("bg"), fg=C("text"),
                 font=F(13)).pack(side="left")
        self.name_var = tk.StringVar()
        ewrap, self.name_edit = rounded_entry(r2, textvariable=self.name_var)
        ewrap.pack(side="left", padx=(4, 0), fill="x", expand=True)

        # Fabric API 行
        self.fabric_row = tk.Frame(w, bg=C("bg"))
        self.fabric_cb = CheckRow(self.fabric_row, _t("下载 Fabric API"), True,
                                  bg="bg", font_size=13)
        self.fabric_cb.pack(side="left")
        self.fabric_api_combo = DropDown(self.fabric_row, [_t("正在获取...")],
                                         width=200, bg="card")
        self.fabric_api_combo.pack(side="left", padx=(8, 8), fill="x", expand=True)
        tk.Label(self.fabric_row, text=_t("许多 Fabric 模组都需要此前置"),
                 bg=C("bg"), fg=C("muted"), font=F(11)).pack(side="left")
        self._set_fabric_visible(False)

        AccentButton(w, _t("加入下载队列"), self._add_task, width=300, height=36,
                     font_size=13).pack(fill="x", pady=(2, 8))

        # 中部：队列 + 任务列表
        mid = tk.Frame(w, bg=C("bg")); mid.pack(fill="both", expand=True)
        left = tk.Frame(mid, bg=C("bg")); left.pack(side="left", fill="both", expand=True)
        tk.Label(left, text=_t("下载队列："), bg=C("bg"), fg=C("text2"),
                 font=F(12)).pack(anchor="w")
        qwrap = tk.Frame(left, bg=C("border"))
        qwrap.pack(fill="both", expand=True)
        self.queue_list = tk.Listbox(qwrap, bg=C("panel"), fg=C("text"),
                                     selectbackground=C("card"), selectforeground=C("text"),
                                     font=F(12), bd=0, highlightthickness=0,
                                     activestyle="none", exportselection=False)
        qsb = _mk_scrollbar(qwrap, self.queue_list.yview)
        self.queue_list.configure(yscrollcommand=qsb.set)
        qsb.pack(side="right", fill="y")
        self.queue_list.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        self.queue_list.bind("<<ListboxSelect>>", self._on_queue_sel)
        qrow = tk.Frame(left, bg=C("bg")); qrow.pack(fill="x", pady=(6, 0))
        self.del_task_btn = FlatButton(qrow, _t("删除该项任务"), self._remove_selected_task,
                                      bg="card", fg="text", hover="border",
                                      padx=12, pady=5, font_size=12)
        self.del_task_btn.pack(side="left")
        self.del_task_btn.pack_forget()
        self.del_all_btn = FlatButton(qrow, _t("删除所有任务"), self._remove_all_tasks,
                                     bg="card", fg="red1", hover="border",
                                     padx=12, pady=5, font_size=12)
        self.del_all_btn.pack(side="left", padx=(8, 0))
        self.del_all_btn.setEnabled(False)

        # 底部：状态 + 步骤 + 进度
        btm = tk.Frame(w, bg=C("bg")); btm.pack(fill="x", pady=(8, 4))
        self.status_label = tk.Label(btm, text=_t("就绪"), bg=C("bg"),
                                     fg=C("muted"), font=F(12), anchor="w")
        self.status_label.pack(side="left", fill="x", expand=True)
        self.step_label = tk.Label(btm, text="", bg=C("bg"), fg=C("accent"),
                                   font=F(12), anchor="e", width=16)
        self.step_label.pack(side="left", padx=(6, 6))
        self.progress = ProgressBar(btm, height=18, bg="card", fill="accent",
                                    bg_role="bg", text=True)
        self.progress.pack(side="left", ipady=0)
        self.progress.configure(width=240)
        self.progress.pack_propagate(False)
        self.progress.setValue(0)

        br = tk.Frame(w, bg=C("bg")); br.pack(fill="x", pady=(2, 0))
        AccentButton(br, _t("开始下载"), self._start_download, width=150, height=38,
                     font_size=13).pack(side="left")
        self.cancel_btn = FlatButton(br, _t("取消下载"), self._cancel_download,
                                     bg="card", fg="text", hover="border",
                                     padx=18, pady=8, font_size=13)
        self.cancel_btn.pack(side="left", padx=(10, 0))
        self.cancel_btn.setEnabled(False)
        return w

    def _build_import_view(self, parent):
        w = tk.Frame(parent, bg=C("bg"))
        self.drop_zone = tk.Canvas(w, height=110, bg=C("panel"),
                                   highlightthickness=0, bd=0, cursor="hand2")
        self.drop_zone.pack(fill="x", pady=(0, 8))
        self.drop_zone.bind("<Button-1>", lambda e: self._pick_pack())
        self.drop_zone.bind("<Configure>", lambda e: self._draw_drop_zone())
        self._drop_text = _t("点击此区域选择整合包，或把文件拖到这里")
        self._draw_drop_zone()
        tk.Label(w, text=_t("按文件内容自动识别 Modrinth / CurseForge 整合包，"
                            "不依赖扩展名"),
                 bg=C("bg"), fg=C("muted"), font=F(12)).pack(anchor="w",
                                                             pady=(0, 6))
        r1 = tk.Frame(w, bg=C("bg")); r1.pack(fill="x", pady=(0, 6))
        FlatButton(r1, _t("选择整合包文件"), self._pick_pack, bg="card",
                   fg="accent", hover="border", padx=14, pady=6,
                   font_size=12).pack(side="left")
        self.imp_file_lbl = tk.Label(r1, text=_t("未选择文件"), bg=C("bg"),
                                     fg=C("text2"), font=F(12), anchor="w")
        self.imp_file_lbl.pack(side="left", padx=10, fill="x", expand=True)
        self.imp_info = tk.Label(w, text="", bg=C("bg"), fg=C("text"), font=F(12),
                                 justify="left", anchor="w", wraplength=700)
        self.imp_info.pack(fill="x", pady=(0, 6))
        r2 = tk.Frame(w, bg=C("bg")); r2.pack(fill="x", pady=(0, 6))
        tk.Label(r2, text=_t("实例名称："), bg=C("bg"), fg=C("text"),
                 font=F(12)).pack(side="left")
        self.imp_name_var = tk.StringVar()
        ewrap, imp_ent = rounded_entry(r2, textvariable=self.imp_name_var)
        ewrap.pack(side="left", fill="x", expand=True, padx=(6, 0))
        r3 = tk.Frame(w, bg=C("bg")); r3.pack(fill="x", pady=(0, 6))
        tk.Label(r3, text=_t("CurseForge API Key（可选）："), bg=C("bg"),
                 fg=C("text"), font=F(12)).pack(side="left")
        self.imp_key_var = tk.StringVar(
            value=load_config().get("curseforge_api_key", "") or "")
        ewrap, imp_key = rounded_entry(r3, textvariable=self.imp_key_var,
                                       show="*")
        ewrap.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.imp_opt_lbl = tk.Label(w, text=_t("可选文件（默认不勾选，选中后一并安装）"),
                                    bg=C("bg"), fg=C("muted"), font=F(12))
        self.imp_opt_lbl.pack(anchor="w")
        self.imp_opt_list = tk.Listbox(w, bg=C("panel"), fg=C("text"),
                                       selectbackground=C("accent"),
                                       selectforeground="#1e1e2e", font=F(12),
                                       height=4, bd=0, highlightthickness=1,
                                       highlightbackground=C("border"),
                                       selectmode="multiple", exportselection=False)
        self.imp_opt_lbl.pack_forget()
        self.imp_opt_list.pack_forget()
        self.imp_log = LogText(w, height=5)
        self.imp_log.pack_forget()
        bt = tk.Frame(w, bg=C("bg")); bt.pack(fill="x", pady=(8, 4))
        self.imp_status = tk.Label(bt, text=_t("就绪"), bg=C("bg"), fg=C("muted"),
                                   font=F(12), anchor="w")
        self.imp_status.pack(side="left", fill="x", expand=True)
        self.imp_progress = ProgressBar(bt, height=18, bg="card", fill="accent",
                                        bg_role="bg", text=True)
        self.imp_progress.pack(side="left")
        self.imp_progress.configure(width=240)
        self.imp_progress.pack_propagate(False)
        br = tk.Frame(w, bg=C("bg")); br.pack(fill="x", pady=(4, 0))
        AccentButton(br, _t("开始导入"), self._start_import, width=150, height=38,
                     font_size=13).pack(side="left")
        self.imp_cancel_btn = FlatButton(br, _t("取消导入"), self._cancel_import,
                                        bg="card", fg="text", hover="border",
                                        padx=18, pady=8, font_size=13)
        self.imp_cancel_btn.pack(side="left", padx=(10, 0))
        self.imp_cancel_btn.setEnabled(False)
        return w

    def _set_fabric_visible(self, ok):
        if ok:
            self.fabric_row.pack(fill="x", pady=(0, 6))
        else:
            self.fabric_row.pack_forget()

    # ---------- 版本 ----------
    def on_show(self):
        if not self._versions_loaded and not self._versions_loading:
            self._load_versions()

    def _load_versions(self):
        if self._versions_loaded or self._versions_loading:
            return
        self._versions_loading = True
        self.version_combo.set_values([_t("正在获取版本列表...")])
        self._vworker = VersionListWorker()
        self._vworker.done.connect(self._on_versions_loaded)
        self._vworker.start()

    def _on_versions_loaded(self, lst):
        self._all_versions = lst
        self._versions_loading = False
        self._versions_loaded = bool(lst)
        if not lst:
            self.version_combo.set_values([_t("获取版本列表失败，切换页面可重试")])
            self.status_label.configure(text=_t("版本列表加载失败"))
            return
        self._filter_versions()

    def _filter_versions(self):
        idx = self.type_combo.current()
        if idx == 0:
            names = [v["id"] for v in self._all_versions if v.get("type") == "release"]
        elif idx == 1:
            names = [v["id"] for v in self._all_versions
                     if v.get("type") == "snapshot" and not _is_april_fools_version(v)]
        elif idx == 2:
            names = [v["id"] for v in self._all_versions
                     if v.get("type") in ("old_beta", "old_alpha")]
        else:
            names = [v["id"] for v in self._all_versions if _is_april_fools_version(v)]
        names.sort(key=_natural_version_key, reverse=True)
        self.version_combo.set_values(names)
        self._on_version_changed()

    def _on_version_changed(self):
        v = self.version_combo.current_text()
        valid = v in {x.get("id") for x in self._all_versions}
        self.loader_combo.set_values([_t("检测中...") if valid else _t("原版")])
        self._loader_ids = [None]
        self.loader_combo.setEnabled(not valid)
        self._set_fabric_visible(False)
        self._fabric_api_ver = ""
        self._update_default_name()
        if not valid:
            return
        w = LoaderQueryWorker(v)
        w.done.connect(self._on_loader_done)

        def _cleanup(worker=w):
            if worker in self._loader_workers:
                self._loader_workers.remove(worker)
        w.finished.connect(_cleanup)
        self._loader_workers.append(w)
        w.start()

    def _on_loader_done(self, version, loaders, errors):
        if version != self.version_combo.current_text():
            return
        labels = [_t("原版")]
        ids = [None]
        for lid, disp in loaders:
            labels.append(disp)
            ids.append(lid)
        if errors:
            labels.append(_t("部分加载器检测失败"))
            ids.append("__error__")
        self.loader_combo.set_values(labels, 0)
        self._loader_labels = labels
        self._loader_ids = ids
        self.loader_combo.setEnabled(True)
        if errors:
            self.status_label.configure(
                text=_t("加载器检测失败：") + "、".join(errors)
                + _t("（可挂代理后重试）"))
            if version not in self._loader_error_prompted:
                self._loader_error_prompted.add(version)
                alert(self, _t("检测失败"),
                      _t("部分 Mod 加载器检测失败：\n") + "\n".join(errors)
                      + "\n\n" + _t("可能是网络问题，可挂代理后重试。\n原版下载不受影响。"),
                      "warn")
        elif not loaders:
            self.status_label.configure(text=_t("该版本未检测到支持的 Mod 加载器"))
        self._on_loader_changed()

    def _current_loader(self):
        idx = self.loader_combo.current()
        if 0 <= idx < len(self._loader_ids):
            lid = self._loader_ids[idx]
        else:
            lid = None
        if lid in ("__detecting__", "__error__"):
            lid = None
        return lid

    def _update_default_name(self):
        v = self.version_combo.current_text()
        lid = self._current_loader()
        if v not in {x.get("id") for x in self._all_versions}:
            self.name_var.set("")
            return
        if lid:
            self.name_var.set("%s-%s" % (v, self.loader_combo.current_text()))
        else:
            self.name_var.set(v)

    def _on_loader_changed(self):
        self._update_default_name()
        show = self._current_loader() == "fabric"
        self._set_fabric_visible(show)
        if show:
            v = self.version_combo.current_text()
            if v and v != self._fabric_api_ver:
                self._fetch_fabric_api(v)

    def _fetch_fabric_api(self, v):
        self._fabric_api_ver = v
        self._fa_list = []
        self.fabric_api_combo.set_values([_t("正在获取...")])
        wk = FabricApiQueryWorker(v)
        wk.done.connect(self._on_fabric_api_done)

        def _cleanup():
            if wk in self._fa_workers:
                self._fa_workers.remove(wk)
        wk.finished.connect(_cleanup)
        self._fa_workers.append(wk)
        wk.start()

    def _on_fabric_api_done(self, ver, lst):
        if ver != self.version_combo.current_text():
            return
        if self._current_loader() != "fabric":
            return
        if not lst:
            self._fa_list = []
            self.fabric_api_combo.set_values([_t("获取失败，可稍后手动下载")])
            return
        self._fa_list = lst
        self.fabric_api_combo.set_values([d["version_number"] for d in lst], 0)

    # ---------- 队列 ----------
    def _add_task(self):
        v = self.version_combo.current_text()
        if not v or v not in {x.get("id") for x in self._all_versions}:
            alert(self, _t("提示"), _t("请先选择一个有效的游戏版本。"), "warn")
            return
        lid = self._current_loader()
        name = self.name_var.get().strip() or v
        ok, err = _validate_version_name(name)
        if not ok:
            alert(self, _t("提示"), _t("版本名非法：") + str(err), "warn")
            return
        if any(t.get("name") == name and not t.get("_removed") for t in self.tasks):
            alert(self, _t("提示"), _t("队列中已存在版本名“%s”。") % name, "warn")
            return
        fa = None
        if lid == "fabric" and self.fabric_cb.isChecked() and self._fa_list:
            i = self.fabric_api_combo.current()
            if 0 <= i < len(self._fa_list):
                fa = self._fa_list[i]
        task = {"version": v, "loader": lid, "name": name,
                "status": "等待中", "_tid": self._task_seq}
        self._task_seq += 1
        if fa:
            task["fabric_api"] = fa
        self.tasks.append(task)
        self.queue_list.insert("end", "%s    —    %s" % (name, _t("等待中")))
        self.del_all_btn.setEnabled(True)
        self._refresh_active_list()
        if self.worker and self.worker.isRunning():
            self.status_label.configure(text=_t("已追加到下载队列"))

    def _on_queue_sel(self, _=None):
        has = bool(self.queue_list.curselection())
        if has:
            self.del_task_btn.pack(side="left")
        else:
            self.del_task_btn.pack_forget()

    def _task_by_tid(self, tid):
        for t in self.tasks:
            if t.get("_tid") == tid:
                return t
        return None

    def _queue_index(self, tid):
        for i in range(self.queue_list.size()):
            if i < len(self.tasks) and self.tasks[i].get("_tid") == tid:
                return i
        return -1

    def _remove_queue_item(self, tid):
        for i, t in enumerate(self.tasks):
            if t.get("_tid") == tid:
                if 0 <= i < self.queue_list.size():
                    self.queue_list.delete(i)
                break

    def _set_queue_text(self, tid, text):
        for i, t in enumerate(self.tasks):
            if t.get("_tid") == tid:
                if 0 <= i < self.queue_list.size():
                    self.queue_list.delete(i)
                    self.queue_list.insert(i, text)
                break

    def _refresh_active_list(self):
        """任务列表常驻显示全部任务（等待/下载中/已完成/失败/已取消）。"""
        self.active_scroll.clear()
        visible = [t for t in self.tasks if not t.get("_removed")]
        if not visible:
            tk.Label(self.active_scroll.inner, text=_t("暂无下载任务"),
                     bg=C("panel"), fg=C("muted"), font=F(11),
                     anchor="w").pack(fill="x", padx=10, pady=8)
            return
        styles = {"下载中": ("下载中", "accent"),
                  "等待中": ("等待下载", "muted"),
                  "已完成": ("已完成", "teal"),
                  "失败": ("失败", "red1"),
                  "已取消": ("已取消", "muted")}
        for t in visible:
            st, color = styles.get(t["status"], (t["status"], "text2"))
            row = tk.Frame(self.active_scroll.inner, bg=C("panel"))
            row.pack(fill="x", pady=1, padx=2)
            IconButton(row, "x", "", lambda tid=t["_tid"]: self._remove_task(tid),
                       bg="panel", fg="red2", hover="card", size=22,
                       icon_size=11).pack(side="right", padx=(0, 2))
            tk.Label(row, text=_t(st), bg=C("panel"), fg=C(color),
                     font=F(10), width=6, anchor="e").pack(side="right")
            tk.Label(row, text=t["name"], bg=C("panel"), fg=C("text"),
                     font=F(11), anchor="w").pack(side="left", fill="x",
                                                  expand=True, padx=(6, 2),
                                                  pady=3)

    def _remove_selected_task(self):
        sel = self.queue_list.curselection()
        if not sel:
            return
        if sel[0] < len(self.tasks):
            self._remove_task(self.tasks[sel[0]].get("_tid"))

    def _remove_task(self, tid):
        t = self._task_by_tid(tid)
        if not t:
            return
        if t["status"] == "下载中" and self.worker and self.worker.isRunning():
            t["_pending_remove"] = True
            self.worker.cancel_task(tid)
        else:
            t["status"] = "已取消"
            t["_removed"] = True
            self._remove_queue_item(tid)
            self._refresh_active_list()
            if not any(not x.get("_removed") for x in self.tasks):
                self.del_all_btn.setEnabled(False)

    def _remove_all_tasks(self):
        if not self.tasks:
            return
        if not confirm(self, _t("删除所有任务"),
                       _t("确定要删除所有下载任务吗？\n"
                          "正在下载的任务会被取消，下载到一半的版本文件夹会被删除。"),
                       danger=True):
            return
        for t in self.tasks:
            if t["status"] != "已完成":
                t["status"] = "已取消"
            t["_removed"] = True
        if self.worker and self.worker.isRunning():
            self._cancel_ui = True
            self.worker.cancel()
        else:
            self.tasks.clear()
        self.queue_list.delete(0, "end")
        self._refresh_active_list()
        self.del_all_btn.setEnabled(False)
        self.del_task_btn.pack_forget()

    # ---------- 下载 ----------
    def _start_download(self):
        self._suppress_dialogs = False
        if self.worker and self.worker.isRunning():
            return
        self._cancel_ui = False
        if self._cancel_poll_job:
            try:
                self.after_cancel(self._cancel_poll_job)
            except Exception:
                pass
            self._cancel_poll_job = None
        # 已取消/失败的任务允许重新开始，已完成的不再重复下载
        pending = [t for t in self.tasks
                   if not t.get("_removed") and t["status"] != "已完成"]
        if not pending:
            alert(self, _t("提示"), _t("队列中没有待下载任务。"))
            return
        for t in pending:
            t["status"] = "等待中"
            self._set_queue_text(t["_tid"], "%s    —    %s" % (t["name"], _t("等待中")))
        self._run_tids = [t["_tid"] for t in pending]
        self.progress.setValue(0)
        self.step_label.configure(text="")
        self._refresh_active_list()
        self.worker = DownloadWorker(self.tasks)
        self.worker.status.connect(lambda s: self.status_label.configure(text=s))
        self.worker.step.connect(lambda s: self.step_label.configure(text=s))
        self.worker.progress.connect(lambda v: self.progress.setValue(v))
        self.worker.task_started.connect(self._on_started)
        self.worker.finished_one.connect(self._on_done)
        self.worker.finished.connect(self._on_all_done)
        self.worker.start()
        self.cancel_btn.setEnabled(True)

    def _cancel_download(self):
        """取消下载：立即更新界面，后台线程是否需要收尾都不再阻塞操作。"""
        if not (self.worker and self.worker.isRunning()):
            self.cancel_btn.setEnabled(False)
            self.status_label.configure(text=_t("没有正在进行的下载。"))
            return
        self._cancel_ui = True
        self.cancel_btn.setEnabled(False)
        self.status_label.configure(text=_t("已取消，正在停止后台任务 ..."))
        self.step_label.configure(text="")
        self.worker.cancel()
        # 立刻把未完成的任务标记为已取消，避免界面停留在「下载中」
        for t in self.tasks:
            if not t.get("_removed") and t["status"] in ("等待中", "下载中"):
                t["status"] = "已取消"
                self._set_queue_text(t["_tid"],
                                     "%s    —    %s" % (t["name"], _t("已取消")))
        self._refresh_active_list()
        if self._cancel_poll_job:
            try:
                self.after_cancel(self._cancel_poll_job)
            except Exception:
                pass
        self._cancel_poll_job = self.after(200, self._poll_cancel_done)

    def _poll_cancel_done(self):
        """取消后轮询工作线程是否退出，保证按钮状态最终一定恢复。"""
        self._cancel_poll_job = None
        if self.worker and self.worker.isRunning():
            self._cancel_poll_job = self.after(200, self._poll_cancel_done)
            return
        if self._cancel_ui:
            self.status_label.configure(text=_t("已取消下载"))
            self.progress.setValue(0)
            self._refresh_active_list()

    def _on_started(self, tid, name):
        if self._cancel_ui:
            return
        t = self._task_by_tid(tid)
        if t:
            t["status"] = "下载中"
        self._set_queue_text(tid, "%s    —    %s" % (name, _t("下载中...")))
        self._refresh_active_list()

    def _on_done(self, tid, ok, msg):
        t = self._task_by_tid(tid)
        if not t:
            return
        name = t["name"]
        if ok:
            t["status"] = "已完成"
            self._set_queue_text(tid, "%s    —    %s" % (name, _t("已完成")))
        elif (t["status"] == "已取消"
              or (self.worker and self.worker.is_cancelled())
              or (self.worker and tid in self.worker._cancel_tids)):
            t["status"] = "已取消"
            self._set_queue_text(tid, "%s    —    %s" % (name, _t("已取消")))
        else:
            t["status"] = "失败"
            self._set_queue_text(tid, "%s    —    %s" % (name, _t("失败")))
            if not self._suppress_dialogs:
                alert(self, _t("下载失败"),
                      _t("网络有问题，下载失败！\n原因：") + str(msg), "error")
        if t.get("_pending_remove"):
            t["_removed"] = True
            self._remove_queue_item(tid)
        self._refresh_active_list()

    def _on_all_done(self):
        reset_download_cancel()
        self.cancel_btn.setEnabled(False)
        was_cancelled = self._cancel_ui
        self._cancel_ui = False
        if self._cancel_poll_job:
            try:
                self.after_cancel(self._cancel_poll_job)
            except Exception:
                pass
            self._cancel_poll_job = None
        self.step_label.configure(text="")
        run = set(getattr(self, "_run_tids", []))

        def _in_run(t):
            return not run or t.get("_tid") in run
        ok = sum(1 for t in self.tasks if _in_run(t) and t["status"] == "已完成")
        cancelled = sum(1 for t in self.tasks
                        if _in_run(t) and t["status"] == "已取消")
        total = sum(1 for t in self.tasks if _in_run(t))
        self.tasks[:] = [t for t in self.tasks if not t.get("_removed")]
        self._refresh_active_list()
        if was_cancelled:
            # 用户主动取消：只更新状态，不再弹完成/取消对话框
            self.status_label.configure(text=_t("已取消下载"))
            self.progress.setValue(0)
            return
        self.progress.setValue(100)
        if self._suppress_dialogs:
            return
        if total > 0 and ok > 0:
            alert(self, _t("下载完成"),
                  _t("共 %d/%d 个任务下载成功。") % (ok, total))
        elif total > 0 and cancelled > 0:
            alert(self, _t("已取消"), _t("已取消 %d 个任务。") % cancelled)
        elif total > 0:
            alert(self, _t("下载完成"), _t("本次 %d 个任务均失败。") % total, "error")

    # ---------- 导入 ----------
    def _init_drop(self):
        try:
            enable_file_drop(self.winfo_toplevel(), self._on_files_dropped)
        except Exception:
            pass

    def _draw_drop_zone(self):
        cv = self.drop_zone
        try:
            cv.delete("all")
            wd = cv.winfo_width() or 760
            ht = int(cv.cget("height"))
            cv.create_rectangle(6, 6, wd - 6, ht - 6, dash=(6, 4),
                                outline=C("border"), width=2)
            cv.create_text(wd / 2, ht / 2 - 12, text=self._drop_text,
                           fill=C("text"), font=F(13, True))
            cv.create_text(wd / 2, ht / 2 + 12,
                           text=_t("支持 Modrinth 与 CurseForge 整合包"),
                           fill=C("muted"), font=F(10))
        except Exception:
            pass

    def _set_drop_text(self, text):
        self._drop_text = text
        self._draw_drop_zone()

    def _on_files_dropped(self, paths):
        """拖放回调：仅下载页接受，自动切到导入页并解析。"""
        try:
            if getattr(self.app, "_key", "") != "download":
                return
            for p in paths:
                if os.path.isfile(p):
                    self._switch_sub(1)
                    self._parse_pack(p)
                    break
        except Exception:
            pass

    def open_import_pack(self, path):
        """下载完成后自动进入导入流程。"""
        self.app.switch_page("download", animate=False)
        self._switch_sub(1)
        self._parse_pack(path)
        if self._pack_path:
            self.imp_status.configure(
                text=_t("已下载整合包，请确认信息后点击“开始导入”。"))

    def _pick_pack(self):
        path = filedialog.askopenfilename(
            parent=self, title=_t("选择整合包文件"),
            filetypes=[(_t("整合包"), "*.mrpack *.zip"),
                       ("Modrinth", "*.mrpack"), ("CurseForge", "*.zip"),
                       (_t("所有文件"), "*.*")])
        if path:
            self._parse_pack(path)

    @staticmethod
    def _safe_pack_name(name):
        n = re.sub(r'[\\/:*?"<>|]', "_", (name or "").strip())
        return n.strip(". ") or "modpack"

    def _parse_pack(self, path):
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            alert(self, _t("提示"), _t("文件不存在。"), "warn")
            return
        # 按内容（而非后缀）识别：Modrinth 看 modrinth.index.json，
        # CurseForge 看 manifest.json 的 manifestType。
        kind, manifest = detect_pack_type(path)
        if not kind:
            self._pack_path = ""
            self._pack_kind = ""
            self.imp_info.configure(text="")
            self.imp_file_lbl.configure(text=_t("未选择文件"))
            self._set_drop_text(_t("点击此区域选择整合包，或把文件拖到这里"))
            self.imp_status.configure(text=_t("未选择整合包"))
            alert(self, _t("无法识别整合包"),
                  _t("这不是有效的 Modrinth 或 CurseForge 整合包。\n"
                     "已检查压缩包内容：未找到 modrinth.index.json 或 "
                     "manifest.json。"), "error")
            return
        self._pack_path = path
        self._pack_kind = kind
        self._pack_manifest = manifest or {}
        self.imp_opt_list.delete(0, "end")
        self.imp_opt_lbl.pack_forget()
        self.imp_opt_list.pack_forget()
        self.imp_file_lbl.configure(text=os.path.basename(path))
        self._set_drop_text(_t("已选择：%s（%s 整合包）") % (
            os.path.basename(path),
            "Modrinth" if kind == "mrpack" else "CurseForge"))
        self.imp_status.configure(text=_t("正在解析整合包..."))
        try:
            if self._pack_kind == "mrpack":
                import minecraft_launcher_lib as ml
                if not hasattr(ml, "mrpack"):
                    raise RuntimeError("minecraft-launcher-lib 版本过低（需 8.0+），请先升级")
                info = ml.mrpack.get_mrpack_information(path)
                self._pack_launch = ml.mrpack.get_mrpack_launch_version(path)
                self._pack_mc = info.get("minecraftVersion", "")
                opts = info.get("optionalFiles", []) or []
                for p in opts:
                    self.imp_opt_list.insert("end", p)
                if opts:
                    self.imp_opt_lbl.pack(anchor="w", pady=(6, 2))
                    self.imp_opt_list.pack(fill="x", pady=(0, 4))
                lines = [
                    "%s：%s" % (_t("名称"), info.get("name", "")),
                    "%s：%s    Minecraft：%s" % (_t("版本"), info.get("versionId", ""),
                                                info.get("minecraftVersion", "")),
                    "%s：%s" % (_t("启动版本"), self._pack_launch),
                ]
                if info.get("summary"):
                    lines.append("%s：%s" % (_t("摘要"), info.get("summary")))
                lines.append("%s：%d" % (_t("可选文件"), len(opts)))
                self.imp_info.configure(text="\n".join(lines))
                default = info.get("name") or os.path.splitext(
                    os.path.basename(path))[0]
            else:
                mf = self._pack_manifest or {}
                if not mf:
                    raise RuntimeError(_t("缺少有效的 manifest.json"))
                self._pack_manifest = mf
                mc_meta = mf.get("minecraft") or {}
                loaders = mc_meta.get("modLoaders") or []
                primary = [x for x in loaders if x.get("primary")] or loaders
                loader_id = primary[0].get("id", "") if primary else "-"
                self._pack_mc = mc_meta.get("version", "")
                self._pack_launch = ""
                lines = [
                    "%s：%s" % (_t("名称"), mf.get("name", "")),
                    "%s：%s    Minecraft：%s" % (_t("版本"), mf.get("version", ""),
                                                mc_meta.get("version", "")),
                    "%s：%s" % (_t("Mod 加载器"), loader_id),
                    "%s：%d" % (_t("Mod 文件"), len(mf.get("files") or [])),
                ]
                if mf.get("author"):
                    lines.append("%s：%s" % (_t("作者"), mf.get("author")))
                self.imp_info.configure(text="\n".join(lines))
                default = mf.get("name") or os.path.splitext(
                    os.path.basename(path))[0]
        except Exception as e:
            self._pack_path = ""
            self.imp_info.configure(text="")
            self.imp_status.configure(text=_t("整合包解析失败"))
            alert(self, _t("整合包解析失败"), _err_text(e), "error")
            return
        self.imp_name_var.set(self._safe_pack_name(default))
        self.imp_status.configure(text=_t("就绪"))

    def _start_import(self):
        if not self._pack_path:
            alert(self, _t("提示"), _t("请先选择整合包文件"), "warn")
            return
        name = self.imp_name_var.get().strip()
        ok, err = _validate_version_name(name)
        if not ok:
            alert(self, _t("提示"), _t("版本名非法：") + str(err), "warn")
            return
        mc_dir = load_config()["minecraft_directory"]
        target = os.path.join(mc_dir, "versions", name)
        if os.path.isdir(target) and os.listdir(target):
            if not confirm(self, _t("实例已存在"),
                           _t("实例“%s”已存在，是否覆盖安装？") % name):
                return
        optional = [self.imp_opt_list.get(i)
                    for i in self.imp_opt_list.curselection()]
        self._import_name = name
        self.imp_progress.setValue(0)
        self.imp_log.clear()
        self.imp_log.pack(fill="x", pady=(4, 4))
        self.imp_status.configure(text=_t("正在安装整合包..."))
        cf_key = self.imp_key_var.get().strip()
        try:
            # 导入时填写的 Key 同步保存到设置，供模组下载复用
            update_config(curseforge_api_key=cf_key)
        except Exception:
            pass
        self.imp_worker = ModpackInstallThread(
            self._pack_path, mc_dir, name, kind=self._pack_kind,
            manifest=self._pack_manifest, optional=optional,
            cf_key=cf_key)
        self.imp_worker.status.connect(self._on_import_status)
        self.imp_worker.progress.connect(lambda v: self.imp_progress.setValue(v))
        self.imp_worker.done.connect(self._on_import_done)
        self.imp_worker.start()
        self.imp_cancel_btn.setEnabled(True)

    def _cancel_import(self):
        if self.imp_worker and self.imp_worker.isRunning():
            self.imp_worker.cancel()
            self.imp_cancel_btn.setEnabled(False)
            self.imp_status.configure(text=_t("正在取消..."))

    def _on_import_status(self, text):
        self.imp_status.configure(text=text)
        ts = datetime.now().strftime("%H:%M:%S")
        self.imp_log.append("[%s] %s" % (ts, text))

    def _on_import_done(self, ok, msg, info):
        reset_download_cancel()
        self.imp_cancel_btn.setEnabled(False)
        if not ok:
            self.imp_progress.setValue(0)
            cancelled = bool(self.imp_worker and self.imp_worker.is_cancelled())
            self.imp_status.configure(
                text=_t("已取消导入") if cancelled else _t("导入失败"))
            if not cancelled and not self._suppress_dialogs:
                alert(self, _t("导入失败"), msg, "error")
            return
        name = self._import_name
        cfg = load_config()
        insts = dict(cfg.get("modpack_instances") or {})
        insts[name] = {"launch_version": info.get("launch_version", ""),
                       "mc_version": info.get("mc_version", ""),
                       "dir": os.path.join("versions", name)}
        update_config(modpack_instances=insts)
        vd = load_versions()
        vs = vd.setdefault("versions", [])
        if name not in vs:
            vs.append(name)
            save_versions(vd)
        self.imp_progress.setValue(100)
        self.imp_status.configure(text=_t("导入完成"))
        if self._suppress_dialogs:
            return
        text = "%s：%s" % (_t("导入完成"), name)
        missing = info.get("missing") or []
        if missing:
            text += "\n%s %d 个（可稍后手动放入 mods 文件夹）：\n" % (
                _t("缺失 Mod"), len(missing))
            text += "、".join(missing[:10])
            if len(missing) > 10:
                text += " ..."
        alert(self, _t("导入完成"), text)

# ============ 个性化 ============
class PersonalizePage(Page):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.config = load_config()
        self._theme = self.config.get("theme", "deep_blue")
        self._build_ui()

    def _build_ui(self):
        root = tk.Frame(self, bg=C("bg"))
        root.pack(fill="both", expand=True, padx=22, pady=(12, 16))
        tk.Label(root, text=_t("个性化"), bg=C("bg"), fg=C("text"),
                 font=F(22, True)).pack(anchor="w")
        tk.Label(root, text=_t("调整界面主题、动画效果与游戏窗口标题"), bg=C("bg"),
                 fg=C("muted"), font=F(12)).pack(anchor="w", pady=(2, 14))

        # 主题
        tk.Label(root, text=_t("主题"), bg=C("bg"), fg=C("text"),
                 font=F(14, True)).pack(anchor="w")
        trow = tk.Frame(root, bg=C("bg")); trow.pack(anchor="w", pady=(6, 14))
        self._theme_btns = {}
        for tid, name in (("deep_blue", _t("深蓝主题")), ("black", _t("黑色主题")),
                          ("white", _t("白色主题"))):
            b = FlatButton(trow, name, lambda t=tid: self._pick_theme(t),
                           bg="card", fg="text", hover="border", padx=18, pady=8,
                           font_size=13)
            b.pack(side="left", padx=(0, 8))
            self._theme_btns[tid] = b
        self._sync_theme_buttons()

        # 游戏窗口标题
        tk.Label(root, text=_t("游戏窗口标题"), bg=C("bg"), fg=C("text"),
                 font=F(14, True)).pack(anchor="w", pady=(2, 0))
        self.title_var = tk.StringVar(
            value=self.config.get("game_window_title", ""))
        ewrap, self.title_edit = rounded_entry(root, textvariable=self.title_var,
                                               font_size=12)
        ewrap.pack(fill="x", pady=(6, 2))
        tk.Label(root,
                 text=_t("留空则沿用游戏默认标题；可用占位符："
                        "{player} 玩家名 · {version} 实例名称 · "
                        "{real_version} 实际游戏版本 · {account_type} 账号类型 · "
                        "{launcher} 启动器名称"),
                 bg=C("bg"), fg=C("muted"), font=F(10), anchor="w",
                 justify="left", wraplength=640).pack(anchor="w")

        # 动画 & 语言
        row2 = tk.Frame(root, bg=C("bg")); row2.pack(fill="x", pady=(10, 6))
        self.anim_cb = CheckRow(row2, _t("启用界面切换动画"),
                                self.config.get("enable_animations", True),
                                bg="bg", font_size=13)
        self.anim_cb.pack(side="left")
        row3 = tk.Frame(root, bg=C("bg")); row3.pack(fill="x", pady=(6, 6))
        tk.Label(row3, text=_t("语言"), bg=C("bg"), fg=C("text"),
                 font=F(13)).pack(side="left")
        langs = ["简体中文", "English"]
        self.lang_combo = DropDown(row3, langs, width=150, bg="card")
        self.lang_combo.pack(side="left", padx=10)
        self.lang_combo.set_current_index(
            0 if self.config.get("launcher_language", "zh_CN") != "en_US" else 1)

        tk.Frame(root, bg=C("bg"), height=10).pack(fill="x")
        frow = tk.Frame(root, bg=C("bg")); frow.pack(fill="x", pady=(10, 0))
        AccentButton(frow, _t("应用"), self._apply_now, width=130, height=38,
                     font_size=13).pack(side="left")
        FlatButton(frow, _t("恢复默认"), self._reset_default, bg="card",
                   fg="text2", hover="border", padx=16, pady=8,
                   font_size=12).pack(side="left", padx=10)

    def _pick_theme(self, tid):
        self._theme = tid
        self._sync_theme_buttons()

    def _sync_theme_buttons(self):
        for tid, b in getattr(self, "_theme_btns", {}).items():
            on = tid == self._theme
            set_roles(b, bg="accent" if on else "card",
                      fg="#1e1e2e" if on else "text")

    def _on_theme(self):
        Page._on_theme(self)
        try:
            self._sync_theme_buttons()
        except Exception:
            pass

    def _apply_now(self):
        try:
            set_theme_name(self._theme)
            update_config(
                theme=self._theme,
                game_window_title=self.title_var.get().strip(),
                enable_animations=bool(self.anim_cb.isChecked()),
                launcher_language=("en_US" if self.lang_combo.current() == 1
                                   else "zh_CN"))
            self.app.apply_theme()
            alert(self, _t("已应用"),
                  _t("个性化设置已应用。\n语言变更需重启启动器后生效。"))
        except Exception as e:
            alert(self, _t("应用失败"), str(e), "error")

    def _reset_default(self):
        if not confirm(self, _t("恢复默认"),
                       _t("确定把所有个性化设置恢复为默认值吗？")):
            return
        self._theme = "deep_blue"
        self.title_var.set("")
        self.anim_cb.setChecked(True)
        self.lang_combo.set_current_index(0)
        self._sync_theme_buttons()
        update_config(theme="deep_blue", game_window_title="",
                      enable_animations=True, launcher_language="zh_CN")
        set_theme_name("deep_blue")
        self.app.apply_theme()


# ============ 设置 ============
class SettingsPage(Page):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.config = load_config()
        self._groups = {}
        self._group_enabled = {}
        self._scope = "global"
        self._version = str(self.config.get("selected_version") or "")
        self._adv_names = self._version_names()
        if self._version not in self._adv_names:
            self._version = self._adv_names[0] if self._adv_names else ""
        self._gc_adv_open = False
        self._legacy_imported = False
        self._build_ui()

    # ---------- 工具 ----------
    @staticmethod
    def _set_ctrl_enabled(w, ok):
        try:
            if isinstance(w, tk.Entry):
                w.configure(state="normal" if ok else "readonly")
            elif isinstance(w, CheckRow):
                w.cb.configure(state="normal" if ok else "disabled")
            elif hasattr(w, "setEnabled"):
                w.setEnabled(ok)
        except Exception:
            pass

    def _version_names(self):
        mc = (self.config.get("minecraft_directory")
              or DEFAULT_CONFIG["minecraft_directory"])
        vr = os.path.join(mc, "versions")
        try:
            names = sorted(n for n in os.listdir(vr)
                           if os.path.isdir(os.path.join(vr, n)))
        except Exception:
            names = []
        return names

    def _current_java(self):
        p = self.var_java_path.get().strip()
        if not p and self._version:
            try:
                p = resolve_java(self._version,
                                 self.config.get("java_path", ""))[0]
            except Exception:
                p = ""
        return p

    def _hint(self, parent, text="", fg="muted", size=10, wrap=760):
        lb = tk.Label(parent, text=text, bg=C("bg"), fg=C(fg), font=F(size),
                      anchor="w", justify="left", wraplength=wrap)
        lb.pack(fill="x", pady=(0, 4))
        return lb

    # ---------- 界面 ----------
    def _build_ui(self):
        self.scroll = ScrollFrame(self, bg="bg")
        self.scroll.pack(fill="both", expand=True, padx=18, pady=(10, 12))
        root = self.scroll.inner
        tk.Label(root, text=_t("设置"), bg=C("bg"), fg=C("text"),
                 font=F(22, True)).pack(anchor="w", pady=(0, 12))
        self._field(root, _t("Java 路径"), "java_path",
                    _t("留空则自动检测；Forge/NeoForge 安装也需要 Java"))

        # ---- 旧版参数（未启用高级参数时生效，保持向后兼容）----
        self.legacy_box = tk.Frame(root, bg=C("bg"))
        self.legacy_box.pack(fill="x")
        self._field(self.legacy_box, _t("JVM 参数"), "jvm_args", "")
        memo = total_memory_gb()
        row = tk.Frame(self.legacy_box, bg=C("bg"))
        row.pack(fill="x", pady=(0, 10))
        FlatButton(row, _t("根据内存生成推荐 JVM 参数"), self._recommend_jvm,
                   bg="card", fg="accent", hover="border", padx=12, pady=5,
                   font_size=12).pack(side="left")
        tk.Label(row, text=_t("检测到系统内存约 %d GB") % memo, bg=C("bg"),
                 fg=C("muted"), font=F(11)).pack(side="left", padx=8)
        self._field(self.legacy_box, _t("额外游戏参数"), "extra_game_args", "")
        self._hint(self.legacy_box,
                   _t("以上为旧版参数，仅在未启用高级参数时生效。"))

        self._dir_field(root, _t("游戏目录"), "minecraft_directory")
        self._field(root, _t("微软 Client ID"), "microsoft_client_id",
                    _t("留空则使用内置 Client ID"))
        self._field(root, _t("authlib-injector 路径"), "authlib_injector_path", "")
        self._field(root, "CurseForge API Key", "curseforge_api_key",
                    _t("留空则使用公共代理"))
        self._hint(root, _t("启动页“智能分析”使用 LogShare 免费接口："
                            "日志会自动脱敏并生成分享链接，无需任何 Key。"))
        src_row = tk.Frame(root, bg=C("bg")); src_row.pack(fill="x", pady=(2, 8))
        tk.Label(src_row, text=_t("下载源"), bg=C("bg"), fg=C("text"),
                 font=F(13), width=18, anchor="w").pack(side="left")
        self.source_combo = DropDown(src_row, [_t("国内镜像（推荐）"), _t("官方源")],
                                     width=220, bg="card")
        self.source_combo.pack(side="left")
        self.source_combo.set_current_index(
            0 if self.config.get("download_source", "mirror") == "mirror" else 1)
        self.lang_cn = CheckRow(
            root, _t("首次启动游戏时把游戏语言设为中文"),
            self.config.get("set_chinese_on_first_launch", True), bg="bg",
            font_size=13)
        self.lang_cn.pack(anchor="w", pady=(8, 4))

        # ---- 高级参数 ----
        tk.Frame(root, bg=C("border"), height=1).pack(fill="x", pady=(8, 6))
        self.adv_check = CheckRow(
            root, _t("启用高级参数（内存 / GC / 参数分区 / 启动前命令）"),
            bool(self.config.get("advanced_enabled")), bg="bg", font_size=13,
            command=self._toggle_advanced)
        self.adv_check.pack(anchor="w", pady=(0, 4))
        self.adv_box = tk.Frame(root, bg=C("bg"))
        self._build_advanced(self.adv_box)

        self.save_bar = tk.Frame(root, bg=C("bg"))
        self.save_bar.pack(fill="x", pady=(12, 0))
        AccentButton(self.save_bar, _t("保存设置"), self._save, width=140,
                     height=38, font_size=13).pack(side="left")
        FlatButton(self.save_bar, _t("打开游戏目录"), self._open_mc_dir,
                   bg="card", fg="text", hover="border", padx=14, pady=7,
                   font_size=12).pack(side="left", padx=10)
        self._toggle_advanced()

    def _build_advanced(self, parent):
        # 作用域
        scope_row = tk.Frame(parent, bg=C("bg"))
        scope_row.pack(fill="x", pady=(4, 6))
        tk.Label(scope_row, text=_t("作用域"), bg=C("bg"), fg=C("text"),
                 font=F(13), width=18, anchor="w").pack(side="left")
        self.btn_scope_global = FlatButton(
            scope_row, _t("全局设置"), lambda: self._set_scope("global"),
            bg="accent", fg="#1e1e2e", hover="accent_hover", padx=14, pady=5,
            font_size=12, bold=True)
        self.btn_scope_global.pack(side="left")
        self.btn_scope_version = FlatButton(
            scope_row, _t("版本独立设置"), lambda: self._set_scope("version"),
            bg="card", fg="text", hover="border", padx=14, pady=5, font_size=12,
            bold=True)
        self.btn_scope_version.pack(side="left", padx=8)
        self.version_combo = DropDown(
            scope_row, self._adv_names or [_t("（未安装版本）")], width=200,
            command=self._on_version_change)
        self.version_combo.pack(side="left", padx=(10, 0))
        if self._version in self._adv_names:
            self.version_combo.set_current_text(self._version)

        # ---------------- 内存 ----------------
        box, head, state = self._group(parent, "memory", _t("内存管理"))
        mrow = tk.Frame(box, bg=C("bg")); mrow.pack(fill="x", pady=(2, 0))
        tk.Label(mrow, text=_t("模式"), bg=C("bg"), fg=C("text2"),
                 font=F(12)).pack(side="left")
        self.mem_mode = DropDown(
            mrow, [_t("自动配置（推荐）"), _t("自定义配置")], width=180,
            command=self._on_mem_mode)
        self.mem_mode.pack(side="left", padx=8)
        self.auto_mem_label = tk.Label(
            mrow, text="", bg=C("bg"), fg=C("accent2"), font=F(11),
            anchor="w")
        self.auto_mem_label.pack(side="left", padx=6)
        xrow = tk.Frame(box, bg=C("bg")); xrow.pack(fill="x", pady=(6, 0))
        tk.Label(xrow, text="-Xms", bg=C("bg"), fg=C("text2"),
                 font=F(12)).pack(side="left")
        self.v_xms = tk.StringVar()
        w1, self.e_xms = rounded_entry(xrow, textvariable=self.v_xms)
        w1.pack(side="left", padx=(6, 14))
        tk.Label(xrow, text="-Xmx", bg=C("bg"), fg=C("text2"),
                 font=F(12)).pack(side="left")
        self.v_xmx = tk.StringVar()
        w2, self.e_xmx = rounded_entry(xrow, textvariable=self.v_xmx)
        w2.pack(side="left", padx=(6, 14))
        self.btn_auto_mem = FlatButton(
            xrow, _t("恢复自动配置"), self._restore_auto_memory, bg="card",
            fg="accent", hover="border", padx=12, pady=4, font_size=11)
        self.btn_auto_mem.pack(side="left")
        self.mem_warn = self._hint(box, "", "yellow")
        self._reg_group("memory", state,
                        [self.mem_mode, self.e_xms, self.e_xmx,
                         self.btn_auto_mem])

        # ---------------- 垃圾回收器 ----------------
        box, head, state = self._group(parent, "gc", _t("垃圾回收器"))
        grow = tk.Frame(box, bg=C("bg")); grow.pack(fill="x", pady=(2, 0))
        tk.Label(grow, text="GC", bg=C("bg"), fg=C("text2"),
                 font=F(12)).pack(side="left")
        self.gc_mode = DropDown(
            grow, [_t("自动（推荐）"), "G1GC", "ZGC"], width=180,
            command=self._on_gc_change)
        self.gc_mode.pack(side="left", padx=8)
        self.gc_hint = tk.Label(grow, text="", bg=C("bg"), fg=C("muted"),
                                font=F(11), anchor="w")
        self.gc_hint.pack(side="left", padx=6)
        self.btn_gc_adv = FlatButton(
            box, _t("高级 GC 微调 ▸"), self._toggle_gc_adv, bg="card",
            fg="text", hover="border", padx=10, pady=4, font_size=11)
        self.btn_gc_adv.pack(anchor="w", pady=(6, 0))
        self.gc_adv_box = tk.Frame(box, bg=C("bg"))
        self.v_gc_tuning = tk.StringVar()
        w3, self.e_gc_tuning = rounded_entry(self.gc_adv_box,
                                             textvariable=self.v_gc_tuning)
        w3.pack(fill="x", pady=(4, 0))
        self._hint(self.gc_adv_box,
                   _t("示例：-XX:MaxGCPauseMillis=200 -XX:G1NewSizePercent=30"))
        self._reg_group("gc", state, [self.gc_mode, self.e_gc_tuning,
                                      self.btn_gc_adv])

        # ---------------- JVM 参数 ----------------
        box, head, state = self._group(
            parent, "jvm_args", _t("JVM 参数（java 与主类之间）"))
        self.v_jvm = tk.StringVar()
        w4, self.e_jvm = rounded_entry(box, textvariable=self.v_jvm)
        w4.pack(fill="x", pady=(2, 2))
        self.jvm_warn = self._hint(
            box, _t("游戏参数（--demo、--width、--server 等）不能填在这里。"),
            "muted")
        self._reg_group("jvm_args", state, [self.e_jvm])

        # ---------------- 游戏参数 ----------------
        box, head, state = self._group(
            parent, "game_args", _t("游戏参数（主类之后）"))
        self.v_game = tk.StringVar()
        w5, self.e_game = rounded_entry(box, textvariable=self.v_game)
        w5.pack(fill="x", pady=(2, 2))
        self._hint(box,
                   _t("--demo 是直接传给 Minecraft 主程序的游戏参数，"
                      "不是 JVM 参数；--server / --port 也属于游戏参数。"))
        self._reg_group("game_args", state, [self.e_game])

        # ---------------- 窗口尺寸 ----------------
        box, head, state = self._group(parent, "window", _t("窗口尺寸"))
        wrow = tk.Frame(box, bg=C("bg")); wrow.pack(fill="x", pady=(2, 0))
        tk.Label(wrow, text=_t("宽度"), bg=C("bg"), fg=C("text2"),
                 font=F(12)).pack(side="left")
        self.v_width = tk.StringVar()
        w6, self.e_width = rounded_entry(wrow, textvariable=self.v_width)
        w6.configure(width=120)
        w6.pack(side="left", padx=(6, 14))
        tk.Label(wrow, text=_t("高度"), bg=C("bg"), fg=C("text2"),
                 font=F(12)).pack(side="left")
        self.v_height = tk.StringVar()
        w7, self.e_height = rounded_entry(wrow, textvariable=self.v_height)
        w7.configure(width=120)
        w7.pack(side="left", padx=(6, 0))
        self._hint(box, _t("留空则不生成 --width / --height，仅接受正整数。"))
        self._reg_group("window", state, [self.e_width, self.e_height])

        # ---------------- 启动前命令 ----------------
        box, head, state = self._group(parent, "pre_launch",
                                       _t("启动前命令"))
        self.v_pre = tk.StringVar()
        w8, self.e_pre = rounded_entry(box, textvariable=self.v_pre)
        w8.pack(fill="x", pady=(2, 2))
        self.pre_wait = CheckRow(
            box, _t("等待命令执行完成后再继续启动"), False, bg="bg",
            font_size=12)
        self.pre_wait.pack(anchor="w", pady=(4, 0))
        self._hint(box,
                   _t("命令按原样执行，不做隐藏截断；版本独立设置下默认"
                      "“跟随全局”或“禁用”，不会静默继承全局命令。"))
        self._reg_group("pre_launch", state, [self.e_pre, self.pre_wait])

        # ---------------- 进程优先级 ----------------
        box, head, state = self._group(parent, "priority", _t("进程优先级"))
        prow = tk.Frame(box, bg=C("bg")); prow.pack(fill="x", pady=(2, 0))
        self.priority_combo = DropDown(
            prow, [_t("正常"), _t("高")], width=160)
        self.priority_combo.pack(side="left")
        self._hint(box,
                   _t("“高”仅在系统资源紧张时提供有限帮助，不建议默认开启。"))
        self._reg_group("priority", state, [self.priority_combo])

        # ---------------- 显卡调度 ----------------
        box, head, state = self._group(parent, "gpu", _t("显卡调度"))
        grow2 = tk.Frame(box, bg=C("bg")); grow2.pack(fill="x", pady=(2, 0))
        self.gpu_combo = DropDown(
            grow2, [_t("关闭"), _t("高性能显卡 / 强制使用独立显卡")],
            width=300)
        self.gpu_combo.pack(side="left")
        gpu_tip = (_t("Windows 下写入显卡偏好；其他平台会提示手动配置。")
                   if sys.platform == "win32"
                   else _t("当前平台不支持自动设置，仅提供配置提示。"))
        self._hint(box, gpu_tip)
        self._reg_group("gpu", state, [self.gpu_combo])

        # ---------------- 渲染器（只读说明）----------------
        tk.Label(parent, text=_t("渲染器 / 图形 API"), bg=C("bg"),
                 fg=C("text"), font=F(13, True)).pack(anchor="w", pady=(12, 2))
        self._hint(parent,
                   _t("渲染后端由游戏与已安装模组决定，原生 Minecraft 没有可在"
                      "启动器侧切换的 OpenGL / Vulkan / DirectX 开关，"
                      "因此不提供无法真正生效的选项。KryptonWrapper、"
                      "DirectX 12 等属于实验性方案，本启动器不注入。"))

        # ---------------- 命令行预览 ----------------
        prev_row = tk.Frame(parent, bg=C("bg"))
        prev_row.pack(fill="x", pady=(12, 4))
        tk.Label(prev_row, text=_t("命令行预览"), bg=C("bg"), fg=C("text"),
                 font=F(13, True)).pack(side="left")
        FlatButton(prev_row, _t("刷新预览"), self._refresh_preview, bg="card",
                   fg="accent", hover="border", padx=10, pady=4,
                   font_size=11).pack(side="left", padx=10)
        self.preview = tk.Text(parent, height=7, bg=C("panel"), fg=C("text"),
                               relief="flat", bd=0, highlightthickness=0,
                               font=("Consolas", 10), wrap="word")
        self.preview.pack(fill="x")
        self.preview.tag_config("jvm", foreground=C("accent"))
        self.preview.tag_config("fixed", foreground=C("muted"))
        self.preview.tag_config("game", foreground=C("yellow"))
        self.preview.tag_config("warn", foreground=C("red1"))
        self.preview.configure(state="disabled")

        self._apply_scope()
        self._populate()

    def _group(self, parent, key, title):
        box = tk.Frame(parent, bg=C("bg"))
        box.pack(fill="x", pady=(10, 2))
        head = tk.Frame(box, bg=C("bg"))
        head.pack(fill="x")
        tk.Label(head, text=title, bg=C("bg"), fg=C("text"),
                 font=F(13, True)).pack(side="left")
        state = DropDown(head, [_t("跟随全局"), _t("启用并自定义"),
                                _t("禁用")], width=150,
                         command=lambda i, k=key: self._on_state_change(k, i))
        state.pack(side="right")
        return box, head, state

    def _reg_group(self, key, state, widgets):
        self._groups[key] = {"state": state, "widgets": widgets}
        self._group_enabled[key] = True

    # ---------- 作用域 / 状态 ----------
    def _set_scope(self, scope):
        if scope == self._scope:
            return
        self._scope = scope
        self._apply_scope()
        self._populate()

    def _apply_scope(self):
        version_mode = (self._scope == "version")
        for k, g in self._groups.items():
            if version_mode:
                g["state"].pack(side="right")
            else:
                g["state"].pack_forget()
        for b, active in ((self.btn_scope_global, not version_mode),
                          (self.btn_scope_version, version_mode)):
            set_roles(b, bg="accent" if active else "card",
                      fg="#1e1e2e" if active else "text")

    def _on_version_change(self, idx):
        names = self._adv_names
        if 0 <= idx < len(names):
            self._version = names[idx]
            self._populate()
            self._refresh_preview()

    def _populate(self):
        adv = load_advanced()
        ov = (load_version_override(self._version)
              if self._scope == "version" else {})
        for key in ADV_GROUPS:
            item = ov.get(key) if isinstance(ov.get(key), dict) else {}
            st = (item.get("state") or ADV_STATE_FOLLOW
                  if self._scope == "version" else ADV_STATE_CUSTOM)
            if st == ADV_STATE_CUSTOM:
                val = item.get("value") if self._scope == "version" else adv.get(key)
                if val is None:
                    val = adv.get(key)
            else:
                val = adv.get(key)
            self._set_group_widgets(key, val)
            g = self._groups.get(key)
            if g:
                g["state"].set_current_index(
                    {"follow": 0, "custom": 1, "off": 2}.get(st, 0))
            self._set_group_enabled(key, st == ADV_STATE_CUSTOM)
        self._refresh_auto_memory()
        self._check_gc_java()
        self._check_jvm_partition()
        self._refresh_preview()

    def _set_group_enabled(self, key, ok):
        self._group_enabled[key] = bool(ok)
        g = self._groups.get(key)
        if not g:
            return
        for w in g["widgets"]:
            self._set_ctrl_enabled(w, ok)
        if key == "memory":
            # 自动模式下 Xms/Xmx 只读，但仍展示当前值
            if ok and self.mem_mode.current() == 0:
                self._set_ctrl_enabled(self.e_xms, False)
                self._set_ctrl_enabled(self.e_xmx, False)
        if key == "gc":
            self._update_gc_adv_visibility()

    def _on_state_change(self, key, idx):
        st = ("follow", "custom", "off")[idx]
        if self._scope != "version":
            st = "custom"
        if st == "follow":
            self._set_group_widgets(key, load_advanced().get(key))
        self._set_group_enabled(key, st == "custom")
        self._refresh_auto_memory()
        self._refresh_preview()

    def _set_group_widgets(self, key, val):
        try:
            if key == "memory":
                v = val if isinstance(val, dict) else {}
                self.mem_mode.set_current_index(
                    0 if (v.get("mode") or "auto") == "auto" else 1)
                self.v_xms.set(str(v.get("xms") or ""))
                self.v_xmx.set(str(v.get("xmx") or ""))
            elif key == "gc":
                v = val if isinstance(val, dict) else {}
                self.gc_mode.set_current_index(
                    {"auto": 0, "g1": 1, "zgc": 2}.get(v.get("mode") or "auto", 0))
                self.v_gc_tuning.set(str(v.get("tuning") or ""))
            elif key == "jvm_args":
                self.v_jvm.set(str(val or ""))
            elif key == "game_args":
                self.v_game.set(str(val or ""))
            elif key == "window":
                v = val if isinstance(val, dict) else {}
                self.v_width.set(str(v.get("width") or ""))
                self.v_height.set(str(v.get("height") or ""))
            elif key == "pre_launch":
                if isinstance(val, dict):
                    self.v_pre.set(str(val.get("text") or ""))
                    self.pre_wait.setChecked(bool(val.get("wait")))
                else:
                    self.v_pre.set(str(val or ""))
                    self.pre_wait.setChecked(False)
            elif key == "priority":
                self.priority_combo.set_current_index(
                    1 if str(val or "normal") == "high" else 0)
            elif key == "gpu":
                self.gpu_combo.set_current_index(
                    1 if str(val or "off") == "high" else 0)
        except Exception:
            pass

    def _collect_group(self, key):
        if key == "memory":
            return {"mode": "auto" if self.mem_mode.current() == 0 else "custom",
                    "xms": self.v_xms.get().strip(),
                    "xmx": self.v_xmx.get().strip()}
        if key == "gc":
            return {"mode": ["auto", "g1", "zgc"][self.gc_mode.current()],
                    "tuning": self.v_gc_tuning.get().strip()}
        if key == "jvm_args":
            return self.v_jvm.get().strip()
        if key == "game_args":
            return self.v_game.get().strip()
        if key == "window":
            return {"width": self.v_width.get().strip(),
                    "height": self.v_height.get().strip()}
        if key == "pre_launch":
            return {"text": self.v_pre.get().strip(),
                    "wait": bool(self.pre_wait.isChecked())}
        if key == "priority":
            return "high" if self.priority_combo.current() == 1 else "normal"
        if key == "gpu":
            return "high" if self.gpu_combo.current() == 1 else "off"
        return None

    # ---------- 交互 ----------
    def _on_theme(self):
        Page._on_theme(self)
        for e in (self.e_xms, self.e_xmx, self.e_jvm, self.e_game,
                  self.e_width, self.e_height, self.e_pre, self.e_gc_tuning):
            try:
                e.configure(disabledbackground=C("card"),
                            disabledforeground=C("muted"),
                            readonlybackground=C("card"))
            except Exception:
                pass
        try:
            self.preview.configure(bg=C("panel"), fg=C("text"))
            self.preview.tag_config("jvm", foreground=C("accent"))
            self.preview.tag_config("fixed", foreground=C("muted"))
            self.preview.tag_config("game", foreground=C("yellow"))
            self.preview.tag_config("warn", foreground=C("red1"))
        except Exception:
            pass

    def _toggle_advanced(self):
        on = bool(self.adv_check.isChecked())
        if on:
            self.legacy_box.pack_forget()
            self.adv_box.pack(fill="x", before=self.save_bar, pady=(4, 0))
            self._import_legacy()
            self._refresh_auto_memory()
            self._refresh_preview()
        else:
            self.adv_box.pack_forget()
            self.legacy_box.pack_forget()
            self.legacy_box.pack(fill="x", before=self.adv_check)

    def _on_mem_mode(self, idx):
        custom = (idx == 1)
        if self._group_enabled.get("memory"):
            self._set_ctrl_enabled(self.e_xms, custom)
            self._set_ctrl_enabled(self.e_xmx, custom)
        self._refresh_auto_memory()
        self._refresh_preview()

    def _restore_auto_memory(self):
        self.mem_mode.set_current_index(0)
        self._on_mem_mode(0)

    def _refresh_auto_memory(self):
        mc = (self.var_minecraft_directory.get().strip()
              or DEFAULT_CONFIG["minecraft_directory"])
        v = self._version
        try:
            auto = compute_auto_memory(v, mc, self._current_java())
            kind = (_t("模组/整合包，%d 个模组") % auto["mods"]
                    if auto["modded"] else _t("原版"))
            self.auto_mem_label.configure(
                text=_t("自动建议：-Xms%s -Xmx%s（%s）")
                % (_fmt_mb(auto["xms"]), _fmt_mb(auto["xmx"]), kind))
        except Exception:
            self.auto_mem_label.configure(text="")
        self._validate_memory()

    def _validate_memory(self):
        """校验自定义 -Xmx，返回 (是否可保存, 警告文本)。"""
        total = total_memory_gb() * 1024
        if self.mem_mode.current() != 1:
            self.mem_warn.configure(text="")
            return True, ""
        xmx = _mb_from_text(self.v_xmx.get())
        if xmx <= 0:
            self.mem_warn.configure(text="")
            return True, ""
        if total and xmx > total:
            txt = _t("最大堆超过物理内存（%d GB），无法保存。") % (total // 1024)
            self.mem_warn.configure(text=txt, fg=C("red1"))
            return False, txt
        if total and xmx > total * 0.7:
            txt = (_t("最大堆已超过物理内存的 70%%，可能触发系统页面文件，"
                      "导致比内存不足更严重的卡顿。"))
            self.mem_warn.configure(text=txt, fg=C("yellow"))
            return True, txt
        self.mem_warn.configure(text="")
        return True, ""

    def _on_gc_change(self, idx):
        self._check_gc_java()
        self._refresh_preview()

    def _check_gc_java(self):
        idx = self.gc_mode.current()
        if idx == 0:
            java = java_major_from_path(self._current_java())
            if java:
                auto = auto_gc_mode(java, _mb_from_text(self.v_xmx.get()) or
                                    compute_auto_memory(
                                        self._version,
                                        self.var_minecraft_directory.get().strip()
                                        or DEFAULT_CONFIG["minecraft_directory"],
                                        self._current_java())["xmx"])
                self.gc_hint.configure(
                    text=_t("将自动使用 %s（Java %d）")
                    % (auto.upper(), java), fg=C("muted"))
            else:
                self.gc_hint.configure(text="", fg=C("muted"))
            return
        mode = "zgc" if idx == 2 else "g1"
        need = gc_java_requirement(mode)
        java = java_major_from_path(self._current_java())
        if java and java < need:
            self.gc_hint.configure(
                text=_t("%s 需要 Java %d 及以上，当前为 Java %d")
                % (mode.upper(), need, java), fg=C("red1"))
        else:
            self.gc_hint.configure(
                text=_t("%s 需要 Java %d 及以上") % (mode.upper(), need),
                fg=C("muted"))

    def _toggle_gc_adv(self):
        self._gc_adv_open = not self._gc_adv_open
        self._update_gc_adv_visibility()

    def _update_gc_adv_visibility(self):
        if self._gc_adv_open and self._group_enabled.get("gc"):
            self.gc_adv_box.pack(fill="x", pady=(2, 0))
            self.btn_gc_adv.setText(_t("高级 GC 微调 ▾"))
        else:
            self.gc_adv_box.pack_forget()
            self.btn_gc_adv.setText(_t("高级 GC 微调 ▸"))

    def _check_jvm_partition(self):
        txt = self.v_jvm.get().strip()
        bad = [x for x in split_args(txt) if x.startswith("--")]
        if bad:
            self.jvm_warn.configure(
                text=_t("检测到游戏参数 %s 被填进了 JVM 参数区，"
                        "请移动到下方“游戏参数”区。") % " ".join(bad),
                fg=C("red1"))
        else:
            self.jvm_warn.configure(
                text=_t("游戏参数（--demo、--width、--server 等）不能填在这里。"),
                fg=C("muted"))

    def _refresh_preview(self):
        mc = (self.var_minecraft_directory.get().strip()
              or DEFAULT_CONFIG["minecraft_directory"])
        try:
            pv = command_preview(self._version, mc, self._current_java())
        except Exception as e:
            pv = {"jvm": "", "fixed": str(e), "game": "", "warnings": []}
        self._check_jvm_partition()
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("end", _t("[JVM 参数]  "), "jvm")
        self.preview.insert("end", (pv["jvm"] or _t("（无）")) + "\n", "jvm")
        self.preview.insert("end", _t("[启动器固定] "), "fixed")
        self.preview.insert("end", pv["fixed"] + "\n", "fixed")
        self.preview.insert("end", _t("[游戏参数]  "), "game")
        self.preview.insert("end", (pv["game"] or _t("（无）")) + "\n", "game")
        for w in pv.get("warnings") or []:
            self.preview.insert("end", _t("[提示] ") + w + "\n", "warn")
        self.preview.configure(state="disabled")

    # ---------- 基础字段 ----------
    def _field(self, parent, label, key, hint=""):
        row = tk.Frame(parent, bg=C("bg")); row.pack(fill="x", pady=(0, 10))
        tk.Label(row, text=label, bg=C("bg"), fg=C("text"), font=F(13),
                 width=18, anchor="w").pack(side="left")
        var = tk.StringVar(value=str(self.config.get(key, "") or ""))
        ewrap, ent = rounded_entry(row, textvariable=var, font_size=13)
        ewrap.pack(side="left", fill="x", expand=True)
        setattr(self, "var_" + key, var)
        if hint:
            tk.Label(row, text=hint, bg=C("bg"), fg=C("muted"),
                     font=F(10)).pack(side="left", padx=6)
        return var

    def _dir_field(self, parent, label, key):
        row = tk.Frame(parent, bg=C("bg")); row.pack(fill="x", pady=(0, 10))
        tk.Label(row, text=label, bg=C("bg"), fg=C("text"), font=F(13),
                 width=18, anchor="w").pack(side="left")
        var = tk.StringVar(value=str(self.config.get(key, "") or ""))
        ewrap, ent = rounded_entry(row, textvariable=var, font_size=13)
        ewrap.pack(side="left", fill="x", expand=True)
        setattr(self, "var_" + key, var)
        FlatButton(row, _t("浏览"), lambda: self._pick_dir(var), bg="card",
                   fg="text", hover="border", padx=10, pady=4,
                   font_size=11).pack(side="left", padx=(6, 0))

    def _pick_dir(self, var):
        path = filedialog.askdirectory(parent=self, title=_t("选择游戏目录"))
        if path:
            var.set(path)

    def _recommend_jvm(self):
        memo = total_memory_gb()
        args = recommend_jvm_args("default", memo)
        if isinstance(args, list):
            txt = " ".join(args)
        else:
            txt = str(args)
        self.var_jvm_args.set(txt)
        alert(self, _t("推荐 JVM 参数"), _t("已填入推荐参数：\n") + txt)

    def _open_mc_dir(self):
        p = self.var_minecraft_directory.get().strip()
        if p and os.path.isdir(p):
            _open_path(p)
        else:
            alert(self, _t("提示"), _t("游戏目录不存在。"), "warn")

    def _import_legacy(self):
        """首次启用高级参数时，把旧版 JVM 参数拆解到新模型。"""
        if self.config.get("advanced_enabled") or self._legacy_imported:
            return
        adv = load_advanced()
        if adv.get("jvm_args"):
            return
        jvm_txt = self.var_jvm_args.get().strip()
        if not jvm_txt:
            return
        keep, xms, xmx, gc = [], "", "", "auto"
        for tok in split_args(jvm_txt):
            if tok.startswith("-Xms"):
                xms = tok[4:]; continue
            if tok.startswith("-Xmx"):
                xmx = tok[4:]; continue
            if tok == "-XX:+UseG1GC":
                gc = "g1"; continue
            if tok == "-XX:+UseZGC":
                gc = "zgc"; continue
            keep.append(tok)
        self._set_group_widgets(
            "memory", {"mode": "custom" if (xms or xmx) else "auto",
                       "xms": xms, "xmx": xmx})
        self._set_group_widgets("gc", {"mode": gc, "tuning": ""})
        self._set_group_widgets("jvm_args", " ".join(keep))
        self._set_group_widgets("game_args",
                                self.var_extra_game_args.get().strip())
        # 迁移结果立即写入配置，避免切换作用域时丢失
        adv["memory"] = {"mode": "custom" if (xms or xmx) else "auto",
                         "xms": xms, "xmx": xmx}
        adv["gc"] = {"mode": gc, "tuning": ""}
        adv["jvm_args"] = " ".join(keep)
        adv["game_args"] = self.var_extra_game_args.get().strip()
        try:
            update_config(advanced=adv)
            self.config = load_config()
        except Exception:
            pass
        self._legacy_imported = True
        # 迁移后按“自定义”刷新可编辑状态，避免输入框停留在只读态
        self._on_mem_mode(self.mem_mode.current())

    def _validate_advanced(self):
        """返回 True 表示可以保存；不通过时已弹出提示。"""
        # 窗口尺寸必须是正整数
        for name, var in ((_t("宽度"), self.v_width),
                          (_t("高度"), self.v_height)):
            txt = var.get().strip()
            if txt and not (txt.isdigit() and int(txt) > 0):
                alert(self, _t("参数无效"),
                      _t("%s只能是正整数，或留空不生成对应参数。") % name,
                      "warn")
                return False
        # 内存阈值
        ok, warn = self._validate_memory()
        if not ok:
            alert(self, _t("内存设置过高"), warn, "error")
            return False
        if warn:
            if not confirm(self, _t("内存安全警告"),
                           warn + "\n\n" + _t("仍要保存吗？"), danger=True):
                return False
        # JVM 区误填游戏参数：针对性提示，但不阻止保存
        bad = [x for x in split_args(self.v_jvm.get()) if x.startswith("--")]
        if bad:
            alert(self, _t("参数分区提示"),
                  _t("检测到游戏参数 %s 被填进了 JVM 参数区。\n"
                     "--demo / --width / --height / --server / --port 属于"
                     "游戏参数，应填到“游戏参数”区。")
                  % " ".join(bad), "warn")
        # GC 与 Java 版本匹配
        idx = self.gc_mode.current()
        if idx in (1, 2):
            mode = "g1" if idx == 1 else "zgc"
            java = java_major_from_path(self._current_java())
            need = gc_java_requirement(mode)
            if java and java < need:
                if not confirm(self, _t("GC 兼容性提示"),
                               _t("%s 需要 Java %d 及以上，当前为 Java %d，"
                                  "该参数可能无法生效。仍要保存吗？")
                               % (mode.upper(), need, java)):
                    return False
        return True

    def _save(self):
        try:
            if not self._validate_advanced():
                return
            cfg = load_config()
            adv_enabled = bool(self.adv_check.isChecked())
            adv = load_advanced()
            overrides = dict(cfg.get("version_overrides") or {})
            if adv_enabled:
                if self._scope == "global":
                    adv = {k: self._collect_group(k) for k in ADV_GROUPS}
                else:
                    ent = {}
                    for k in ADV_GROUPS:
                        idx = self._groups[k]["state"].current()
                        st = ("follow", "custom", "off")[idx]
                        if st == "custom":
                            ent[k] = {"state": "custom",
                                      "value": self._collect_group(k)}
                        elif st == "off":
                            ent[k] = {"state": "off"}
                        else:
                            ent[k] = {"state": "follow"}
                    overrides[self._version] = ent
            update_config(
                java_path=self.var_java_path.get().strip(),
                jvm_args=self.var_jvm_args.get().strip(),
                extra_game_args=self.var_extra_game_args.get().strip(),
                minecraft_directory=self.var_minecraft_directory.get().strip(),
                microsoft_client_id=self.var_microsoft_client_id.get().strip(),
                authlib_injector_path=
                self.var_authlib_injector_path.get().strip(),
                curseforge_api_key=self.var_curseforge_api_key.get().strip(),
                set_chinese_on_first_launch=bool(self.lang_cn.isChecked()),
                advanced_enabled=adv_enabled,
                advanced=adv,
                version_overrides=overrides,
                download_source=("mirror" if self.source_combo.current() == 0
                                 else "official"))
            self.config = load_config()
            mc = self.config["minecraft_directory"]
            try:
                os.makedirs(mc, exist_ok=True)
                ensure_minecraft_dir(mc)
            except Exception:
                pass
            self._refresh_preview()
            alert(self, _t("已保存"), _t("设置已保存。"))
        except Exception as e:
            alert(self, _t("保存失败"), str(e), "error")

# ============ 联机 ============
class MultiplayerPage(Page):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.config = load_config()
        self._mode = "terra"
        self._task = None
        self._state_thread = None
        self._frpc = None
        self._frpc_exe = ""
        self._tunnels = []
        self._login_thread = None
        self._info_thread = None
        self._tunnel_thread = None
        self._stellar_refreshing = 0
        self._terra_port = None
        self._ctl_thread = None
        self._build_ui()
        self._refresh_terra()

    def _build_ui(self):
        root = tk.Frame(self, bg=C("bg"))
        root.pack(fill="both", expand=True, padx=18, pady=(10, 14))
        tabs = tk.Frame(root, bg=C("bg")); tabs.pack(fill="x")
        self.tab_terra = FlatButton(tabs, _t("陶瓦联机"), lambda: self._select_mode("terra"),
                                    bg="accent", fg="#1e1e2e", hover="accent_hover",
                                    padx=18, pady=6, font_size=13, bold=True)
        self.tab_terra.pack(side="left", padx=(0, 8))
        self.tab_stellar = FlatButton(tabs, _t("恒星映射"), lambda: self._select_mode("stellar"),
                                      bg="card", fg="text", hover="border",
                                      padx=18, pady=6, font_size=13)
        self.tab_stellar.pack(side="left")

        self.holder = tk.Frame(root, bg=C("bg"))
        self.holder.pack(fill="both", expand=True, pady=(10, 0))
        self.terra_view = self._build_terra_view(self.holder)
        self.stellar_view = self._build_stellar_view(self.holder)
        self.terra_view.pack(fill="both", expand=True)
        self._select_mode("terra")

    def _select_mode(self, mode):
        self._mode = mode
        if mode == "terra":
            self.stellar_view.pack_forget()
            self.terra_view.pack(fill="both", expand=True)
        else:
            self.terra_view.pack_forget()
            self.stellar_view.pack(fill="both", expand=True)
            self._stellar_on_show()
        self._refresh_tabs()

    def _refresh_tabs(self):
        on = self._mode == "terra"
        set_roles(self.tab_terra, bg="accent" if on else "card",
                  fg="#1e1e2e" if on else "text")
        set_roles(self.tab_stellar, bg="card" if on else "accent",
                  fg="text" if on else "#1e1e2e")

    def _on_theme(self):
        Page._on_theme(self)
        try:
            self._refresh_tabs()
        except Exception:
            pass

    # ---------- 陶瓦 ----------
    def _build_terra_view(self, parent):
        w = tk.Frame(parent, bg=C("bg"))
        # ---- 标题栏 ----
        head = tk.Frame(w, bg=C("bg")); head.pack(fill="x")
        tt = tk.Frame(head, bg=C("bg")); tt.pack(side="left", fill="x", expand=True)
        tk.Label(tt, text=_t("陶瓦联机"), bg=C("bg"), fg=C("text"),
                 font=F(20, True)).pack(anchor="w")
        tk.Label(tt, text=_t("与好友组成虚拟局域网，无需公网 IP。\n"
                             "首次使用会下载约 30 MB 的陶瓦客户端。"),
                 bg=C("bg"), fg=C("muted"), font=F(11), justify="left",
                 anchor="w").pack(anchor="w", pady=(2, 0))
        self.terra_refresh_btn = FlatButton(head, _t("刷新"), self._terra_refresh,
                                            bg="card", fg="text", hover="border",
                                            padx=16, pady=7, font_size=12)
        self.terra_refresh_btn.pack(side="right", anchor="n", pady=(2, 0))

        # ---- 客户端卡片 ----
        cli_card = RoundedCard(w, radius=10, bg="card", border="border",
                               padx=14, pady=12)
        cli_card.pack(fill="x", pady=(12, 0))
        ci = cli_card.inner
        srow = tk.Frame(ci, bg=C("card")); srow.pack(fill="x")
        self.terra_state_icon = IconCanvas(srow, "dot_o", "muted", 14, bg="card")
        self.terra_state_icon.pack(side="left", padx=(0, 8))
        tk.Label(srow, text=_t("客户端状态"), bg=C("card"), fg=C("text"),
                 font=F(13, True), anchor="w").pack(side="left")
        self.terra_status = tk.Label(srow, text=_t("正在检查客户端 ..."),
                                     bg=C("card"), fg=C("text2"), font=F(12),
                                     anchor="w", justify="left", wraplength=430)
        self.terra_status.pack(side="left", fill="x", expand=True, padx=(10, 0))
        self.terra_bar = ProgressBar(ci, height=14, bg="border", fill="accent",
                                     bg_role="card", text=True)
        self.terra_bar.pack(fill="x", pady=(10, 0))
        self.terra_bar.pack_forget()
        brow = tk.Frame(ci, bg=C("card")); brow.pack(fill="x", pady=(10, 0))
        self._terra_btn_row = brow
        self.terra_dl_btn = FlatButton(brow, _t("下载客户端"), self._terra_download,
                                       bg="accent", fg="#1e1e2e",
                                       hover="accent_hover", padx=14, pady=6,
                                       font_size=12)
        self.terra_dl_btn.pack(side="left")
        self.terra_start_btn = FlatButton(brow, _t("启动联机"), self._terra_start,
                                          bg="accent", fg="#1e1e2e",
                                          hover="accent_hover", padx=14, pady=6,
                                          font_size=12)
        self.terra_start_btn.pack(side="left")
        self.terra_stop_btn = FlatButton(brow, _t("停止联机"), self._terra_stop,
                                         bg="bg", fg="red1", hover="border",
                                         padx=14, pady=6, font_size=12)
        self.terra_stop_btn.pack(side="left", padx=8)
        self.terra_stop_btn.setEnabled(False)
        FlatButton(brow, _t("查看许可协议"),
                   lambda: webbrowser.open(TERRA_LICENSE_URL), bg="bg",
                   fg="text2", hover="border", padx=14, pady=6,
                   font_size=12).pack(side="right")

        # ---- 房间卡片 ----
        room_card = RoundedCard(w, radius=10, bg="card", border="border",
                                padx=14, pady=12)
        room_card.pack(fill="x", pady=(10, 0))
        ri = room_card.inner
        rhead = tk.Frame(ri, bg=C("card")); rhead.pack(fill="x")
        tk.Label(rhead, text=_t("房间码"), bg=C("card"), fg=C("text"),
                 font=F(13, True), anchor="w").pack(side="left")
        self.terra_players = tk.Label(rhead, text="", bg=C("card"),
                                      fg=C("muted"), font=F(12))
        self.terra_players.pack(side="right")
        body = tk.Frame(ri, bg=C("card")); body.pack(fill="x", pady=(6, 0))
        self.terra_room = tk.Label(body, text=_t("尚未联机"), bg=C("card"),
                                   fg=C("muted"), font=F(26, True), anchor="w")
        self.terra_room.pack(side="left", fill="x", expand=True)
        self.terra_copy_btn = FlatButton(body, _t("复制房间码"), self._copy_room,
                                         bg="bg", fg="accent", hover="border",
                                         padx=14, pady=6, font_size=12)
        self.terra_copy_btn.pack(side="right")
        self.terra_copy_btn.setEnabled(False)
        self.terra_hint = tk.Label(ri, text=_t("把房间码发给好友，对方在陶瓦联机中填入即可加入。"),
                                   bg=C("card"), fg=C("muted"), font=F(11),
                                   anchor="w", justify="left")
        self.terra_hint.pack(fill="x", pady=(6, 0))

        jrow = tk.Frame(ri, bg=C("card")); jrow.pack(fill="x", pady=(10, 0))
        tk.Label(jrow, text=_t("加入房间："), bg=C("card"), fg=C("muted"),
                 font=F(11)).pack(side="left", padx=(0, 6))
        self.terra_join_var = tk.StringVar()
        jwrap, self.terra_join_ent = rounded_entry(jrow,
                                                   textvariable=self.terra_join_var,
                                                   bg="bg")
        jwrap.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.terra_join_ent.bind("<Return>", lambda _e: self._terra_join())
        self.terra_join_btn = FlatButton(jrow, _t("加入房间"), self._terra_join,
                                         bg="bg", fg="accent", hover="border",
                                         padx=14, pady=6, font_size=12)
        self.terra_join_btn.pack(side="right")
        self.terra_host_btn = FlatButton(jrow, _t("创建房间"), self._terra_host,
                                         bg="accent", fg="#1e1e2e",
                                         hover="accent_hover", padx=14, pady=6,
                                         font_size=12)
        self.terra_host_btn.pack(side="right", padx=(0, 8))
        self.terra_host_btn.setEnabled(False)
        self.terra_join_btn.setEnabled(False)

        self.terra_log = LogText(w, height=6)
        self.terra_log.pack(fill="both", expand=True, pady=(10, 0))
        return w

    def _terra_set_status(self, text, fg="text2", dot="dot_o", dot_fg="muted"):
        self.terra_status.configure(text=text, fg=C(fg))
        try:
            self.terra_state_icon.set_icon(dot, dot_fg)
        except Exception:
            pass

    def _terra_set_room_actions(self, can_host):
        """待机状态才允许创建/加入房间。"""
        for b in (self.terra_host_btn, self.terra_join_btn):
            b.setEnabled(bool(can_host))

    def _terra_refresh(self):
        if self._task and self._task.isRunning():
            return
        self._refresh_terra()
        self.terra_log.append(_t("已刷新客户端状态。"))

    def _refresh_terra(self):
        exe = find_terracotta()
        running = bool(self._terra_port)
        if exe:
            self.terra_dl_btn.pack_forget()
            if running:
                self.terra_start_btn.pack_forget()
            else:
                self.terra_start_btn.pack(side="left", padx=(0, 8),
                                          before=self.terra_stop_btn)
            if not running:
                self._terra_set_status(_t("客户端已就绪：") + exe, "text2",
                                       "dot", "teal")
        else:
            self.terra_dl_btn.pack(side="left")
            self.terra_start_btn.pack_forget()
            if not running:
                self._terra_set_status(_t("尚未下载陶瓦客户端。"), "text2",
                                       "dot_o", "muted")

    def _terra_download(self):
        if self._task and self._task.isRunning():
            return
        self.terra_bar.pack(fill="x", pady=(10, 0), before=self._terra_btn_row)
        self.terra_bar.setValue(0)
        self._terra_set_status(_t("正在下载陶瓦客户端 ..."), "text2")
        self.terra_dl_btn.setEnabled(False)
        th = TerraDownloadThread()
        self._task = th
        th.progress.connect(lambda v: self.terra_bar.setValue(v))
        th.done.connect(self._terra_dl_done)
        th.failed.connect(self._terra_dl_failed)
        th.start()

    def _terra_dl_done(self, exe):
        self.terra_bar.setValue(100)
        self.terra_log.append(_t("客户端下载完成：") + exe)
        self.terra_dl_btn.setEnabled(True)
        self._refresh_terra()
        self.terra_bar.pack_forget()

    def _terra_dl_failed(self, msg):
        self.terra_bar.setValue(0)
        self.terra_bar.pack_forget()
        self.terra_dl_btn.setEnabled(True)
        self._terra_set_status(_t("下载失败：") + msg, "red1", "warn", "red1")
        alert(self, _t("下载失败"), msg, "error")

    def _terra_start(self):
        exe = find_terracotta()
        if not exe:
            alert(self, _t("提示"), _t("请先下载陶瓦客户端。"), "warn")
            return
        self._terra_set_status(_t("正在启动陶瓦联机 ..."), "text2")
        th = TerraStartThread(exe, _dp("terracotta_port.json"))
        self._task = th
        th.done.connect(self._terra_started)
        th.failed.connect(lambda m: self._terra_set_status(
            _t("启动失败：") + m, "red1", "warn", "red1"))
        th.start()

    def _terra_started(self, port):
        self._terra_port = int(port)
        self.terra_log.append(_t("陶瓦联机已启动，端口：") + str(port))
        self.terra_start_btn.pack_forget()
        self.terra_stop_btn.setEnabled(True)
        self._terra_set_room_actions(True)
        self._terra_set_status(_t("已启动，正在等待创建或加入房间 ..."),
                               "teal", "dot", "teal")
        st = TerraStateThread(self._terra_port)
        self._state_thread = st
        st.changed.connect(self._terra_state)
        st.lost.connect(self._terra_lost)
        st.start()

    def _terra_lost(self):
        """陶瓦进程已退出（连续轮询失败）。"""
        self._state_thread = None
        self._terra_port = None
        self.terra_room.configure(text=_t("尚未联机"), fg=C("muted"))
        self.terra_players.configure(text="")
        self.terra_copy_btn.setEnabled(False)
        self.terra_stop_btn.setEnabled(False)
        self._terra_set_room_actions(False)
        self.terra_log.append(_t("陶瓦进程已退出。"))
        self._refresh_terra()
        self._terra_set_status(_t("陶瓦进程已退出，可重新启动联机。"),
                               "red1", "warn", "red1")

    # ---- 房间状态机 ----
    def _terra_state(self, d):
        state = (d.get("state") or "").strip()
        room = d.get("room") or ""
        profiles = d.get("profiles") or []
        if room:
            self.terra_room.configure(text=str(room), fg=C("accent"))
            self.terra_copy_btn.setEnabled(True)
        else:
            self.terra_room.configure(text=_t("尚未联机"), fg=C("muted"))
            self.terra_copy_btn.setEnabled(False)
        if isinstance(profiles, list) and profiles:
            self.terra_players.configure(text=_t("当前玩家：%s") % len(profiles))
        else:
            self.terra_players.configure(text="")
        if state == "waiting":
            self._terra_set_status(_t("待机中：可创建房间或输入房间码加入。"),
                                   "text2", "dot", "teal")
            self._terra_set_room_actions(True)
        elif state == "host-scanning":
            self._terra_set_status(_t("正在扫描本机 Minecraft 端口：请进入存档并"
                                      "「对局域网开放」，然后等待房间创建 ..."),
                                   "yellow", "dot", "yellow")
            self._terra_set_room_actions(False)
        elif state == "host-starting":
            self._terra_set_status(_t("正在创建房间 ..."), "yellow",
                                   "dot", "yellow")
            self._terra_set_room_actions(False)
        elif state == "host-ok":
            self._terra_set_status(_t("房间已就绪：把房间码发给好友即可加入。"),
                                   "teal", "dot", "teal")
            self._terra_set_room_actions(False)
        elif state in ("guest-connecting", "guest-starting"):
            diff = d.get("difficulty")
            extra = ("（NAT 难度：%s）" % diff) if diff else ""
            self._terra_set_status(_t("正在加入房间 ...") + extra, "yellow",
                                   "dot", "yellow")
            self._terra_set_room_actions(False)
        elif state == "guest-ok":
            url = d.get("url") or ""
            extra = ("，直连地址：" + str(url)) if url else ""
            self._terra_set_status(_t("已加入房间") + extra
                                   + _t("；多人游戏列表中会出现「陶瓦联机大厅」。"),
                                   "teal", "dot", "teal")
            self._terra_set_room_actions(False)
        elif state == "exception":
            reasons = {0: _t("无法连接房主（房间已关闭或网络不稳定）"),
                       1: _t("与房主的连接中断"),
                       2: _t("加入方组网组件崩溃"),
                       3: _t("房主组网组件崩溃"),
                       4: _t("房主已退出存档，房间自动关闭"),
                       5: _t("协议错误，请更新陶瓦客户端")}
            reason = reasons.get(d.get("type"), _t("未知异常"))
            self._terra_set_status(_t("联机异常：") + reason
                                   + _t("。可点击「停止联机」后重试。"),
                                   "red1", "warn", "red1")
            self._terra_set_room_actions(False)

    def _terra_control(self, path, params=None, on_done=None):
        if not self._terra_port:
            alert(self, _t("提示"), _t("请先启动陶瓦联机。"), "warn")
            return
        th = TerraControlThread(self._terra_port, path, params or {})
        self._ctl_thread = th
        if on_done:
            th.done.connect(on_done)
        th.failed.connect(lambda m: self.terra_log.append(str(m)))
        th.start()

    def _terra_player_name(self):
        try:
            name = load_config().get("selected_account", "") or ""
        except Exception:
            name = ""
        return name

    def _terra_host(self):
        self._terra_control("/state/scanning",
                            {"player": self._terra_player_name()})
        self._terra_set_status(_t("正在创建房间 ..."), "yellow", "dot", "yellow")

    def _terra_join(self):
        code = normalize_room_code(self.terra_join_var.get())
        if not code:
            alert(self, _t("提示"),
                  _t("房间码格式不正确，应形如 U/XXXX-XXXX-XXXX-XXXX。"), "warn")
            return
        self._terra_control("/state/guesting",
                            {"room": code, "player": self._terra_player_name()})
        self._terra_set_status(_t("正在加入房间 ..."), "yellow", "dot", "yellow")

    def _terra_stop(self):
        if self._state_thread:
            self._state_thread.stop()
            self._state_thread = None
        port = self._terra_port
        self._terra_port = None
        thr = self._task
        if port:
            # 按官方建议先回到待机，再让进程优雅退出
            try:
                TerraControlThread(port, "/state/ide").start()
            except Exception:
                pass

            def _peaceful_exit():
                # /panic 会直接断开连接后退出，请求报错属正常现象
                try:
                    import requests
                    requests.get("http://127.0.0.1:%d/panic" % port,
                                 params={"peaceful": "true"}, timeout=3)
                except Exception:
                    pass
            threading.Thread(target=_peaceful_exit, daemon=True).start()

            def _force_exit():
                # 优雅退出没生效时兜底结束进程（避免下次启动出现两个实例）
                if self._terra_port is None and isinstance(thr, TerraStartThread):
                    if thr.proc is not None and thr.proc.poll() is None:
                        thr.terminate()
            self.after(2500, _force_exit)
        self.terra_room.configure(text=_t("尚未联机"), fg=C("muted"))
        self.terra_players.configure(text="")
        self.terra_copy_btn.setEnabled(False)
        self.terra_stop_btn.setEnabled(False)
        self._terra_set_room_actions(False)
        self._refresh_terra()
        self._terra_set_status(_t("已停止陶瓦联机。"), "text2", "dot_o", "muted")

    def _copy_room(self):
        text = self.terra_room.cget("text")
        if text and text != _t("尚未联机"):
            self.clipboard_clear()
            self.clipboard_append(text)
            self.terra_log.append(_t("已复制房间码：") + text)

    # ---------- 恒星 ----------
    def _build_stellar_view(self, parent):
        w = tk.Frame(parent, bg=C("bg"))
        # ---- 标题栏：标题 + 副标题 + 统一刷新按钮 ----
        head = tk.Frame(w, bg=C("bg")); head.pack(fill="x")
        tt = tk.Frame(head, bg=C("bg")); tt.pack(side="left", fill="x", expand=True)
        tk.Label(tt, text=_t("恒星内网穿透"), bg=C("bg"), fg=C("text"),
                 font=F(20, True)).pack(anchor="w")
        tk.Label(tt, text=_t("基于 StellarFRP 把本地游戏映射到公网，好友可直接加入。"),
                 bg=C("bg"), fg=C("muted"), font=F(11),
                 anchor="w").pack(anchor="w", pady=(2, 0))
        self.stellar_refresh_btn = FlatButton(head, _t("刷新"),
                                              self._stellar_refresh_all,
                                              bg="card", fg="text",
                                              hover="border", padx=16, pady=7,
                                              font_size=12)
        self.stellar_refresh_btn.pack(side="right", anchor="n", pady=(2, 0))

        # ---- 账号卡片 ----
        acc_card = RoundedCard(w, radius=10, bg="card", border="border",
                               padx=14, pady=12)
        acc_card.pack(fill="x", pady=(12, 0))
        ai = acc_card.inner
        # 未登录视图
        self.stellar_guest = tk.Frame(ai, bg=C("card"))
        tk.Label(self.stellar_guest,
                 text=_t("登录恒星映射账号以查看流量与签到"),
                 bg=C("card"), fg=C("muted"), font=F(12),
                 anchor="w").pack(side="left", fill="x", expand=True)
        self.stellar_login_btn = FlatButton(self.stellar_guest, _t("登录恒星账号"),
                                            self._stellar_login_dlg, bg="accent",
                                            fg="#1e1e2e", hover="accent_hover",
                                            padx=14, pady=6, font_size=12)
        self.stellar_login_btn.pack(side="right")
        # 已登录视图
        self.stellar_profile = tk.Frame(ai, bg=C("card"))
        prow = tk.Frame(self.stellar_profile, bg=C("card"))
        prow.pack(fill="x")
        self.stellar_avatar = IconCanvas(prow, "user", "accent", 22, bg="card")
        self.stellar_avatar.pack(side="left", padx=(0, 8))
        self.stellar_name = tk.Label(prow, text="", bg=C("card"), fg=C("text"),
                                     font=F(14, True), anchor="w")
        self.stellar_name.pack(side="left", fill="x", expand=True)
        self.checkin_badge = tk.Label(prow, text="", bg=C("card"),
                                      fg=C("muted"), font=F(11))
        self.checkin_badge.pack(side="right")
        tfrow = tk.Frame(self.stellar_profile, bg=C("card"))
        tfrow.pack(fill="x", pady=(10, 0))
        tk.Label(tfrow, text=_t("流量"), bg=C("card"), fg=C("muted"),
                 font=F(11), width=4, anchor="w").pack(side="left")
        self.traffic_bar = ProgressBar(tfrow, height=12, bg="border",
                                       fill="accent", bg_role="card", text=False)
        self.traffic_bar.pack(side="left", fill="x", expand=True,
                              padx=(6, 10), pady=2)
        self.traffic_text = tk.Label(tfrow, text="--", bg=C("card"),
                                     fg=C("text2"), font=F(11))
        self.traffic_text.pack(side="right")
        brow = tk.Frame(self.stellar_profile, bg=C("card"))
        brow.pack(fill="x", pady=(10, 0))
        self.stellar_logout_btn = FlatButton(brow, _t("退出登录"),
                                             self._stellar_logout, bg="bg",
                                             fg="red1", hover="border",
                                             padx=14, pady=5, font_size=11)
        self.stellar_logout_btn.pack(side="right")
        FlatButton(brow, _t("打开控制台"),
                   lambda: webbrowser.open(STELLAR_REGISTER), bg="bg",
                   fg="accent", hover="border", padx=14, pady=5,
                   font_size=11).pack(side="right", padx=(0, 8))

        # ---- 隧道卡片 ----
        tun_card = RoundedCard(w, radius=10, bg="card", border="border",
                               padx=14, pady=12)
        tun_card.pack(fill="x", pady=(10, 0))
        ti = tun_card.inner
        tk.Label(ti, text=_t("隧道"), bg=C("card"), fg=C("text"),
                 font=F(13, True), anchor="w").pack(fill="x")
        srow = tk.Frame(ti, bg=C("card")); srow.pack(fill="x", pady=(8, 0))
        self.tunnel_combo = DropDown(srow, [_t("尚未获取隧道")], width=340,
                                     bg="bg")
        self.tunnel_combo.pack(side="left")
        FlatButton(srow, _t("新建隧道"),
                   lambda: webbrowser.open(STELLAR_TUNNELS), bg="bg",
                   fg="accent", hover="border", padx=12, pady=5,
                   font_size=11).pack(side="left", padx=8)
        crow = tk.Frame(ti, bg=C("card")); crow.pack(fill="x", pady=(10, 0))
        self.connect_btn = FlatButton(crow, _t("连接隧道"), self._stellar_connect,
                                      bg="accent", fg="#1e1e2e",
                                      hover="accent_hover", padx=14, pady=6,
                                      font_size=12)
        self.connect_btn.pack(side="left")
        self.disconnect_btn = FlatButton(crow, _t("断开连接"),
                                         self._stellar_disconnect, bg="bg",
                                         fg="red1", hover="border",
                                         padx=14, pady=6, font_size=12)
        self.disconnect_btn.pack(side="left", padx=8)
        self.frpc_bar = ProgressBar(crow, height=16, bg="bg", fill="accent",
                                    bg_role="card", text=True)
        self.frpc_bar.configure(width=220)
        self.frpc_bar.pack(side="left", padx=8)
        self.frpc_bar.pack_forget()

        # ---- 状态行与日志 ----
        self.stellar_status = tk.Label(w, text="", bg=C("bg"), fg=C("text2"),
                                       font=F(11), anchor="w", justify="left",
                                       wraplength=700)
        self.stellar_status.pack(fill="x", pady=(8, 0))
        self.stellar_log = LogText(w, height=8)
        self.stellar_log.pack(fill="both", expand=True, pady=(6, 0))
        return w

    def _stellar_on_show(self):
        c = load_config()
        token = c.get("stellar_token", "") or ""
        acc = c.get("stellar_account", "") or ""
        if token:
            self.stellar_guest.pack_forget()
            self.stellar_profile.pack(fill="x")
            self.stellar_name.configure(text=acc or _t("（未知账号）"))
            self.stellar_refresh_btn.setEnabled(True)
            self._stellar_refresh_all()
        else:
            self.stellar_profile.pack_forget()
            self.stellar_guest.pack(fill="x")
            self.stellar_refresh_btn.setEnabled(False)
            self.stellar_status.configure(text="")

    def _stellar_login_dlg(self):
        c = load_config()
        d = TextInputDialog(self, _t("登录恒星账号"), width=420)
        d.add_field("acc", _t("账号："), c.get("stellar_account", ""))
        d.add_field("pwd", _t("密码："), c.get("stellar_password", ""), password=True)
        st = tk.Label(d._body, text="", bg=C("bg"), fg=C("red1"), font=F(12),
                      anchor="w")
        st.pack(fill="x", pady=(6, 0))

        def do_login():
            acc, pwd = d.value("acc").strip(), d.value("pwd")
            if not acc or not pwd:
                st.configure(text=_t("请输入账号和密码。"))
                return
            st.configure(text=_t("正在登录 ..."), fg=C("text2"))
            th = StellarLoginThread(acc, pwd)
            self._login_thread = th

            def ok(token, account):
                update_config(stellar_account=account, stellar_password=pwd,
                              stellar_token=token)
                self.stellar_log.append(_t("登录成功：") + account)
                if d.winfo_exists():
                    d.destroy()
                self._stellar_on_show()

            def fail(msg):
                st.configure(text=_t("登录失败：") + msg, fg=C("red1"))
            th.ok.connect(ok)
            th.failed.connect(fail)
            th.start()

        d.button_row([(_t("取消"), lambda: d.destroy(), False),
                      (_t("登录"), do_login, True)])
        d.show()

    def _stellar_refresh_all(self):
        """统一刷新：账号信息 + 签到状态 + 隧道列表（带忙碌状态）。"""
        token = load_config().get("stellar_token", "")
        if not token or self._stellar_refreshing:
            return
        self._stellar_refreshing = 2  # 信息线程 + 隧道线程各计一次
        self.stellar_refresh_btn.setEnabled(False)
        self.stellar_refresh_btn.setText(_t("刷新中..."))
        self._stellar_refresh()

    def _refresh_one_done(self):
        if self._stellar_refreshing > 0:
            self._stellar_refreshing -= 1
        if self._stellar_refreshing == 0:
            try:
                self.stellar_refresh_btn.setEnabled(True)
                self.stellar_refresh_btn.setText(_t("刷新"))
            except Exception:
                pass

    def _stellar_refresh(self):
        token = load_config().get("stellar_token", "")
        if not token:
            return
        th = StellarInfoThread(token, do_checkin=True)
        self._info_thread = th
        th.info.connect(self._stellar_info)
        th.checkin.connect(self._stellar_checkin)
        th.checked.connect(lambda s: self.stellar_log.append(s))
        th.failed.connect(self._stellar_info_failed)
        th.start()
        self._stellar_load_tunnels()

    def _stellar_info_failed(self, s):
        self.stellar_status.configure(text=_t("获取信息失败：") + s)
        self._refresh_one_done()

    def _stellar_info(self, info):
        name = info.get("username") or info.get("name") or info.get("account") or ""
        if name:
            self.stellar_name.configure(text=name)
        used = info.get("used_traffic") or info.get("usedTraffic") or 0
        total = info.get("total_traffic") or info.get("traffic") or 0
        try:
            used_f, total_f = float(used or 0), float(total or 0)
        except Exception:
            used_f = total_f = 0.0
        if total_f > 0:
            self.traffic_bar.setRange(0, 100)
            self.traffic_bar.setValue(min(100, used_f * 100.0 / total_f))
            self.traffic_text.configure(
                text=_fmt_traffic(used_f) + " / " + _fmt_traffic(total_f))
        else:
            self.traffic_bar.setValue(0)
            self.traffic_text.configure(text=_t("流量信息不可用"))
        self.stellar_status.configure(text="")
        self._refresh_one_done()

    def _stellar_checkin(self, cs):
        if cs.get("has_checked"):
            self.checkin_badge.configure(text="✓ " + _t("已签到"),
                                         fg=C("teal"))
            self.stellar_log.append(_t("今日已签到"))
        else:
            self.checkin_badge.configure(text=_t("未签到"), fg=C("yellow"))
            self.stellar_log.append(_t("尚未签到"))

    def _stellar_load_tunnels(self):
        token = load_config().get("stellar_token", "")
        if not token:
            return
        th = StellarTunnelsThread(token)
        self._tunnel_thread = th
        th.done.connect(self._stellar_tunnels_done)
        th.failed.connect(self._stellar_tunnels_failed)
        th.start()

    def _stellar_tunnels_failed(self, s):
        self.stellar_log.append(_t("获取隧道失败：") + s)
        self._refresh_one_done()

    def _stellar_tunnels_done(self, tunnels):
        self._tunnels = tunnels
        labels = []
        for t in tunnels:
            name = t.get("Name") or t.get("name") or t.get("_key") or ""
            online = _t("在线") if t.get("_online") else _t("离线")
            labels.append("%s  [%s]" % (name, online))
        if not labels:
            labels = [_t("没有可用隧道，请先在控制台创建")]
        self.tunnel_combo.set_values(labels, 0)
        self.stellar_log.append(_t("已获取 %d 个隧道。") % len(tunnels))
        self._refresh_one_done()

    def _stellar_connect(self):
        token = load_config().get("stellar_token", "")
        if not token:
            alert(self, _t("提示"), _t("请先登录恒星账号。"), "warn")
            return
        idx = self.tunnel_combo.current()
        if not self._tunnels or idx >= len(self._tunnels):
            alert(self, _t("提示"), _t("请先创建并刷新隧道。"), "warn")
            return
        tid = self._tunnels[idx].get("_key") or self._tunnels[idx].get("Id")
        exe = self._frpc_exe
        if exe and os.path.isfile(exe):
            self._start_frpc(exe, token, tid)
            return
        self.frpc_bar.pack(side="left", padx=8)
        self.frpc_bar.setValue(0)
        th = StellarDownloadThread()
        def on_progress(v):
            self.frpc_bar.setValue(v)
        def on_done(path):
            self._frpc_exe = path
            self.frpc_bar.pack_forget()
            self.stellar_log.append(_t("客户端已就绪：") + path)
            self._start_frpc(path, token, tid)
        def on_fail(msg):
            self.frpc_bar.pack_forget()
            alert(self, _t("下载失败"), msg, "error")
        th.progress.connect(on_progress)
        th.done.connect(on_done)
        th.failed.connect(on_fail)
        th.start()

    def _start_frpc(self, exe, token, tid):
        self.stellar_log.append(_t("正在连接隧道 ..."))
        proc = StellarFrpcProcess(exe, token, tid)
        self._frpc = proc
        proc.output.connect(lambda s: self.stellar_log.append(s))
        proc.finished_code.connect(self._frpc_finished)
        proc.start()

    def _frpc_finished(self, code, out):
        if code == StellarFrpcProcess.PROBE_FAILED:
            self.stellar_log.append(_t("客户端无法运行：") + str(out))
        else:
            self.stellar_log.append(_t("FRPC 已退出（错误码 %d）") % code)

    def _stellar_disconnect(self):
        if self._frpc and self._frpc.isRunning():
            try:
                self._frpc.stop()
            except Exception:
                pass
        self._frpc = None
        self.stellar_log.append(_t("已断开隧道连接。"))

    def _stellar_logout(self):
        if not confirm(self, _t("退出登录"), _t("确定退出恒星账号登录吗？")):
            return
        c = load_config()
        c.pop("stellar_token", None)
        save_config(c)
        self._stellar_on_show()

# ============ 关于 ============
ABOUT_LINKS = [
    ("GitHub", "https://github.com/" + GITHUB_REPO),
]


# ---------- 启动器自身更新（GitHub Releases） ----------
def parse_version(text):
    """"v1.2.3" → (1, 2, 3)，用于版本新旧比较。"""
    nums = re.findall(r"\d+", str(text or ""))
    return tuple(int(n) for n in nums[:4]) or (0,)


def version_newer(latest, current):
    a, b = parse_version(latest), parse_version(current)
    n = max(len(a), len(b))
    a += (0,) * (n - len(a))
    b += (0,) * (n - len(b))
    return a > b


def _pick_update_asset(assets):
    """按运行方式选择更新包：源码运行优先 .py，打包运行优先 .exe。"""
    prefer = ((".exe", ".py", ".zip") if getattr(sys, "frozen", False)
              else (".py", ".exe", ".zip"))

    def rank(a):
        name = str(a.get("name") or "").lower()
        for i, ext in enumerate(prefer):
            if name.endswith(ext):
                return i
        return 9
    usable = [a for a in assets if a.get("browser_download_url")]
    return sorted(usable, key=rank)[0] if usable else None


class UpdateCheckThread(QThread):
    """向 GitHub 查询最新 Release，比较 tag 与当前版本。"""
    found = pyqtSignal(dict)
    latest = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, api_url=None, parent=None):
        super().__init__(parent)
        self._api = api_url or GITHUB_RELEASES_API

    def run(self):
        import requests
        try:
            r = requests.get(self._api, timeout=(10, 20), headers={
                "Accept": "application/vnd.github+json"})
            if r.status_code == 404:
                self.failed.emit(_t("未找到 GitHub 仓库或还没有发布任何版本。"))
                return
            if r.status_code in (403, 429):
                # 公共出口 IP 共享未认证配额时容易触发
                self.failed.emit(_t("GitHub 访问频率受限，请稍后再试。"))
                return
            r.raise_for_status()
            data = r.json() or {}
            tag = str(data.get("tag_name") or data.get("name") or "").strip()
            if not tag:
                self.failed.emit(_t("发布信息中没有版本号。"))
                return
            ver = tag.lstrip("vV")
            if not version_newer(ver, APP_VERSION):
                self.latest.emit(ver)
                return
            asset = _pick_update_asset(data.get("assets") or [])
            if not asset:
                self.failed.emit(_t("新版本没有提供可用的更新文件。"))
                return
            self.found.emit({
                "version": ver,
                "tag": tag,
                "notes": str(data.get("body") or "").strip(),
                "url": asset.get("browser_download_url") or "",
                "name": asset.get("name") or "update",
                "size": int(asset.get("size") or 0),
                "page": data.get("html_url") or GITHUB_RELEASES_PAGE,
            })
        except Exception as e:
            self.failed.emit(_err_text(e))


class UpdateDownloadThread(QThread):
    """下载更新文件到临时目录，实时回报进度。"""
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, info, parent=None):
        super().__init__(parent)
        self._info = info
        self._cancel = False

    def cancel(self):
        self._cancel = True
        interrupt_active_downloads()

    def run(self):
        import requests, tempfile
        reset_download_cancel()
        info = self._info or {}
        name = os.path.basename(str(info.get("name") or "LightningCraftLauncher.py"))
        folder = os.path.join(tempfile.gettempdir(), "lcl_update")
        tmp = ""
        try:
            os.makedirs(folder, exist_ok=True)
            dest = os.path.join(folder, name)
            tmp = dest + ".part"
            self.status.emit(_t("正在下载更新 ..."))
            with requests.get(str(info.get("url") or ""), stream=True,
                              timeout=(10, 60),
                              headers={"Accept": "application/octet-stream"}) as r:
                r.raise_for_status()
                total = int(r.headers.get("Content-Length") or info.get("size") or 0)
                got = 0
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(65536):
                        if self._cancel:
                            raise RuntimeError("cancelled")
                        if not chunk:
                            continue
                        f.write(chunk)
                        got += len(chunk)
                        if total:
                            self.progress.emit(min(99, int(got * 100 / total)))
                            self.status.emit(
                                _t("正在下载更新 ... %d%%（%.1f / %.1f MB）")
                                % (min(99, int(got * 100 / total)),
                                   got / 1048576.0, total / 1048576.0))
                        else:
                            self.status.emit(
                                _t("正在下载更新 ... %.1f MB") % (got / 1048576.0))
            if not os.path.getsize(tmp):
                raise RuntimeError("更新文件为空")
            os.replace(tmp, dest)
            self.progress.emit(100)
            self.done.emit(dest)
        except Exception as e:
            try:
                if tmp and os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            self.failed.emit(_t("已取消") if self._cancel else _err_text(e))


def launcher_entry_path():
    """当前正在运行的启动器文件（源码 .py 或打包后的 exe）。"""
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    return os.path.abspath(__file__)


def _resource_path(*parts):
    """打包后资源在 sys._MEIPASS 下，源码运行时在脚本同级目录。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


_UPDATE_HELPER = r'''
import os, sys, time, shutil, subprocess


def alive(pid):
    try:
        if os.name == "nt":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not h:
                return False
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        os.kill(pid, 0)
        return True
    except Exception:
        return False


src, dst, pid = sys.argv[1], sys.argv[2], int(sys.argv[3])
deadline = time.time() + 300
while time.time() < deadline and alive(pid):
    time.sleep(0.1)
for _ in range(50):
    try:
        shutil.copyfile(src, dst)
        break
    except Exception:
        time.sleep(0.2)
else:
    sys.exit(1)
os.chdir(os.path.dirname(dst) or ".")
try:
    subprocess.Popen([sys.executable, dst] if dst.lower().endswith(".py") else [dst])
except Exception:
    pass
try:
    os.remove(src)
except Exception:
    pass
try:
    os.remove(os.path.abspath(__file__))
except Exception:
    pass
'''


def spawn_update_restart(new_file):
    """让旧进程退出后由独立小脚本替换文件并重新启动；返回后调用方应尽快退出。"""
    target = launcher_entry_path()
    if not target.lower().endswith(".py"):
        if sys.platform == "win32" and target.lower().endswith(".exe"):
            bat = os.path.join(os.path.dirname(new_file), "_lcl_apply_update.bat")
            with open(bat, "w", encoding="utf-8") as f:
                f.write("@echo off\r\n:wait\r\n"
                        "tasklist /FI \"PID eq %~3\" 2>nul | findstr /C:\"%~3\" >nul\r\n"
                        "if not errorlevel 1 (\r\n  ping -n 2 127.0.0.1 >nul\r\n"
                        "  goto wait\r\n)\r\n"
                        "copy /Y \"%~1\" \"%~2\" >nul\r\n"
                        "start \"\" \"%~2\"\r\n"
                        "del \"%~f0\"\r\n")
            subprocess.Popen(["cmd", "/c", bat, new_file, target, str(os.getpid())],
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
                             close_fds=True)
            return
        raise RuntimeError(_t("该版本只提供 .py 文件，请手动下载替换。"))
    helper = os.path.join(os.path.dirname(new_file), "_lcl_apply_update.py")
    with open(helper, "w", encoding="utf-8") as f:
        f.write(_UPDATE_HELPER)
    subprocess.Popen([sys.executable, helper, new_file, target, str(os.getpid())],
                     cwd=os.path.dirname(target) or None,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
                     close_fds=True)


class UpdateDialog(BaseDialog):
    """发现新版本：展示版本号与说明，点击「立即更新」显示下载进度。"""

    def __init__(self, app, info):
        super().__init__(app, _t("发现新版本"), width=540)
        self.app = app
        self.info = info
        self._thread = None
        self._done_file = ""
        self.add_label(_t("当前版本：v%s  →  最新版本：v%s")
                       % (APP_VERSION, info.get("version", "")), size=14, bold=True)
        notes = (info.get("notes") or "").strip() or _t("（该版本没有填写更新说明）")
        if len(notes) > 1200:
            notes = notes[:1200] + " ..."
        text = tk.Text(self._body, height=9, bg=C("card"), fg=C("text2"),
                       font=F(11), relief="flat", wrap="word", bd=0,
                       padx=10, pady=8, highlightthickness=0)
        text.insert("1.0", notes)
        text.configure(state="disabled")
        text.pack(fill="both", expand=True, pady=(0, 8))
        self.bar = ProgressBar(self._body, height=18, bg="card", fill="accent",
                               bg_role="bg", text=True)
        self.bar.pack(fill="x", pady=(0, 6))
        self.bar.pack_forget()
        self.status = tk.Label(self._body, text="", bg=C("bg"), fg=C("text2"),
                               font=F(11), anchor="w", wraplength=500,
                               justify="left")
        self.status.pack(fill="x")
        row = tk.Frame(self._body, bg=C("bg"))
        row.pack(side="bottom", fill="x", pady=(12, 0))
        inner = tk.Frame(row, bg=C("bg"))
        inner.pack(side="right")
        self.btn_later = FlatButton(inner, _t("稍后"), self.destroy, bg="border",
                                    fg="text", hover="border2", padx=18, pady=6)
        self.btn_later.pack(side="left", padx=(0, 6))
        self.btn_now = AccentButton(inner, _t("立即更新"), self.start_download,
                                    width=110, height=34, font_size=13,
                                    bg_role="bg")
        self.btn_now.pack(side="left")

    def start_download(self):
        if self._thread is not None:
            return
        self.btn_now.setEnabled(False)
        self.btn_later.setEnabled(False)
        self.bar.pack(fill="x", pady=(0, 6), before=self.status)
        self.bar.setValue(0)
        self.status.configure(text=_t("正在下载更新 ..."), fg=C("text2"))
        th = UpdateDownloadThread(self.info)
        self._thread = th
        th.progress.connect(self.bar.setValue)
        th.status.connect(lambda s: self.status.configure(text=str(s)))
        th.done.connect(self._on_done)
        th.failed.connect(self._on_failed)
        th.start()

    def _on_done(self, path):
        self._thread = None
        self._done_file = path
        self.bar.setValue(100)
        self.status.configure(text=_t("下载完成，正在准备重启 ..."), fg=C("teal"))
        self.after(500, self._apply)

    def _on_failed(self, msg):
        self._thread = None
        self.bar.pack_forget()
        self.btn_now.setEnabled(True)
        self.btn_later.setEnabled(True)
        self.status.configure(text=_t("下载失败：%s") % msg, fg=C("red1"))

    def _apply(self):
        path = self._done_file
        if not path:
            return
        self.destroy()
        self.app.apply_update(path)


class AboutPage(Page):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self._check_thread = None
        root = tk.Frame(self, bg=C("bg"))
        root.pack(fill="both", expand=True)
        box = tk.Frame(root, bg=C("bg"))
        box.place(relx=0.5, rely=0.42, anchor="center")
        IconCanvas(box, "bolt", "accent", 96, bg="bg").pack()
        tk.Label(box, text="Lightning Craft Launcher", bg=C("bg"), fg=C("text"),
                 font=F(26, True)).pack(pady=(10, 2))
        tk.Label(box, text=_t("版本 %s  ·  Python + tkinter") % APP_VERSION,
                 bg=C("bg"), fg=C("muted"), font=F(12)).pack()
        tk.Label(box, text=_t("一个简洁的 Minecraft 启动器\n支持多账号、模组加载器与联机"),
                 bg=C("bg"), fg=C("text2"), font=F(12),
                 justify="center").pack(pady=(14, 10))
        row = tk.Frame(box, bg=C("bg")); row.pack()
        for name, url in ABOUT_LINKS:
            FlatButton(row, name, lambda u=url: webbrowser.open(u), bg="card",
                       fg="accent", hover="border", padx=18, pady=6,
                       font_size=12).pack(side="left", padx=4)
        self.btn_check = FlatButton(box, _t("检查更新"), self.check_updates,
                                    bg="card", fg="accent", hover="border",
                                    padx=18, pady=6, font_size=12)
        self.btn_check.pack(pady=(12, 0))
        self.upd_status = tk.Label(box, text="", bg=C("bg"), fg=C("muted"),
                                   font=F(11), wraplength=460,
                                   justify="center")
        self.upd_status.pack(pady=(6, 0))

    # ---------- 检查启动器更新 ----------
    def check_updates(self):
        if self._check_thread is not None:
            return
        self.btn_check.setEnabled(False)
        self.upd_status.configure(text=_t("正在检查更新 ..."), fg=C("muted"))
        th = UpdateCheckThread()
        self._check_thread = th
        th.latest.connect(self._on_latest)
        th.found.connect(self._on_found)
        th.failed.connect(self._on_check_failed)
        th.start()

    def _check_finished(self):
        self._check_thread = None
        try:
            self.btn_check.setEnabled(True)
        except Exception:
            pass

    def _on_latest(self, ver):
        self._check_finished()
        self.upd_status.configure(
            text=_t("此版本已是最新（v%s）") % (ver or APP_VERSION),
            fg=C("teal"))

    def _on_found(self, info):
        self._check_finished()
        self.upd_status.configure(
            text=_t("发现新版本：v%s") % info.get("version", ""),
            fg=C("yellow"))
        UpdateDialog(self.app, info).show()

    def _on_check_failed(self, msg):
        self._check_finished()
        self.upd_status.configure(text=_t("检查更新失败：%s") % msg,
                                  fg=C("red1"))


# ============ 主窗口 ============
class NavItem(tk.Frame):
    def __init__(self, parent, icon, text, command):
        super().__init__(parent, bg=C("panel"), height=46, cursor="hand2")
        self.pack_propagate(False)
        self._bg_role = "panel"
        self._active = False
        self.bar = tk.Frame(self, bg=C("panel"), width=3)
        self.bar.pack(side="left", fill="y")
        self.icon = IconCanvas(self, icon, "text2", 16, bg="panel")
        self.icon.pack(side="left", padx=(13, 8))
        self.label = tk.Label(self, text=text, bg=C("panel"), fg=C("text2"),
                              font=F(13), anchor="w")
        self.label.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self._command = command
        for w in (self, self.icon, self.label):
            w.bind("<Button-1>", self._click)
            w.bind("<Enter>", lambda e: self._hover(True))
            w.bind("<Leave>", lambda e: self._hover(False))

    def _hover(self, ok):
        if self._active:
            return
        role = "card" if ok else "panel"
        self.configure(bg=C(role))
        self.icon.set_bg(role)
        self.label.configure(bg=C(role))

    def _click(self, _):
        if self._command:
            self._command()

    def set_active(self, ok):
        self._active = ok
        role = "card" if ok else "panel"
        self.configure(bg=C(role))
        self.icon.set_bg(role)
        self.label.configure(bg=C(role),
                             fg=C("text" if ok else "text2"))
        self.bar.configure(bg=C("accent" if ok else "panel"))

    def _on_theme(self):
        self.set_active(self._active)


class JavaMissingDialog(BaseDialog):
    def __init__(self, parent, launcher, version, need):
        super().__init__(parent, _t("缺少 Java"), width=500, height=250)
        self.launcher = launcher
        self.add_label(
            _t("启动该版本需要 Java %d，但本机没有找到可用的 Java。\n"
               "可以让启动器自动下载，或手动指定一个 Java 路径。") % need,
            size=13)
        self.bar = ProgressBar(self._body, height=18, bg="card", fill="accent",
                              bg_role="bg", text=True)
        self.bar.pack(fill="x", pady=(6, 6))
        self.status = tk.Label(self._body, text="", bg=C("bg"), fg=C("text2"),
                               font=F(11), anchor="w", wraplength=440,
                               justify="left")
        self.status.pack(fill="x")
        self._need = need
        self._version = version

        def auto():
            self.bar.setValue(0)
            self.status.configure(text=_t("正在下载并安装 Java %d ...") % need)
            th = JavaInstallThread(version, need,
                                   load_config()["minecraft_directory"])
            self._thread = th
            th.progress.connect(lambda v: self.bar.setValue(v))
            th.status.connect(lambda s: self.status.configure(text=str(s)))
            th.done.connect(self._done)
            th.failed.connect(self._failed)
            th.start()

        def manual():
            path = filedialog.askopenfilename(
                parent=self, title=_t("选择 Java 可执行文件"),
                filetypes=[("Java", "java.exe javaw java"),
                           (_t("所有文件"), "*.*")])
            if path:
                self.launcher._force_java = path
                self.status.configure(text=_t("已选择：") + path)
                self.destroy()

        d = self
        d.button_row([(_t("取消"), d.destroy, False),
                      (_t("手动选择"), manual, False),
                      (_t("自动下载"), auto, True)])

    def _done(self, path):
        self.bar.setValue(100)
        self.status.configure(text=_t("Java 安装完成：") + path)
        self.launcher._force_java = path
        self.after(900, self.destroy)

    def _failed(self, msg):
        self.bar.setValue(0)
        self.status.configure(text=_t("安装失败：") + msg, fg=C("red1"))


PAGE_DEFS = [
    ("launch", "power", "启动游戏"),
    ("multiplayer", "link", "联机"),
    ("download", "download", "下载"),
    ("manage", "folder", "管理版本"),
    ("personalize", "palette", "个性化"),
    ("settings", "gear", "设置"),
    ("about", "info", "关于"),
]


class MainWindow(tk.Tk):
    def __init__(self):
        global _UI_ROOT
        super().__init__()
        _UI_ROOT = self
        cfg = load_config()
        set_theme_name(cfg.get("theme", "deep_blue"))
        self.config_data = cfg
        self.title(cfg.get("title") or "Lightning Craft Launcher")
        self.geometry("1000x660")
        self.minsize(1000, 660)
        self.maxsize(1000, 660)
        self.configure(bg=C("bg"))
        self._apply_app_icon()
        self._anim = bool(cfg.get("enable_animations", True))
        self._key = ""

        # 左侧导航
        nav = tk.Frame(self, bg=C("panel"), width=126)
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)
        logo = tk.Frame(nav, bg=C("panel"), height=76)
        logo.pack(fill="x")
        logo.pack_propagate(False)
        IconCanvas(logo, "bolt", "accent", 26, bg="panel").pack(pady=(16, 2))
        tk.Label(logo, text="Lightning Craft", bg=C("panel"), fg=C("text"),
                 font=F(11, True)).pack()
        self.nav_items = {}
        for key, icon, label in PAGE_DEFS:
            item = NavItem(nav, icon, _t(label),
                           lambda k=key: self.switch_page(k))
            item.pack(fill="x", pady=1)
            self.nav_items[key] = item
        spacer = tk.Frame(nav, bg=C("panel"))
        spacer.pack(fill="both", expand=True)
        tk.Label(nav, text="v" + APP_VERSION, bg=C("panel"), fg=C("muted"),
                 font=F(10)).pack(pady=(0, 10))

        # 内容区
        self.content = tk.Frame(self, bg=C("bg"))
        self.content.pack(side="left", fill="both", expand=True)
        self.pages = {
            "launch": LaunchPage(self.content, self),
            "multiplayer": MultiplayerPage(self.content, self),
            "download": DownloadPage(self.content, self),
            "manage": ManagePage(self.content, self),
            "personalize": PersonalizePage(self.content, self),
            "settings": SettingsPage(self.content, self),
            "about": AboutPage(self.content, self),
        }
        self.launch_page = self.pages["launch"]
        self.launch_page.on_java_missing = self._on_java_missing
        self._pending_update = ""
        for k, p in self.pages.items():
            p.place(x=0, y=0, relwidth=1, relheight=1)
            p.place_forget()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.switch_page("launch", animate=False)
        self.after(15, _pump_ui)

    def _apply_app_icon(self):
        ico = _resource_path("assets", "icon.ico")
        png = _resource_path("assets", "icon.png")
        try:
            if sys.platform == "win32" and os.path.isfile(ico):
                self.iconbitmap(ico)
        except Exception:
            pass
        try:
            if os.path.isfile(png):
                self._icon_photo = tk.PhotoImage(file=png)
                self.iconphoto(True, self._icon_photo)
        except Exception:
            pass

    def switch_page(self, key, animate=True):
        if key == self._key or key not in self.pages:
            return
        old = self.pages.get(self._key)
        new = self.pages[key]
        self._key = key
        for k, item in self.nav_items.items():
            item.set_active(k == key)
        if old is not None and animate and self._anim:
            self._animate_slide(old, new)
        else:
            if old is not None:
                old.place_forget()
            new.place(x=0, y=0, relwidth=1, relheight=1)
        page = self.pages[key]
        if hasattr(page, "on_show"):
            try:
                page.on_show()
            except Exception:
                traceback.print_exc()

    def _animate_slide(self, old, new):
        w = self.content.winfo_width() or 874
        old.place(x=0, y=0, relwidth=1, relheight=1)
        new.place(x=w, y=0, relwidth=1, relheight=1)
        steps = 8

        def step(i):
            try:
                if i >= steps:
                    new.place_configure(x=0)
                    old.place_forget()
                    return
                new.place_configure(x=int(w * (1 - i / float(steps))))
                self.after(16, step, i + 1)
            except Exception:
                pass
        step(1)

    def _on_java_missing(self, version, need):
        d = JavaMissingDialog(self, self.launch_page, version, need)
        d.show()

    def apply_theme(self):
        try:
            restyle_all()
            for item in self.nav_items.values():
                item._on_theme()
            self.configure(bg=C("bg"))
            self.content.configure(bg=C("bg"))
        except Exception:
            traceback.print_exc()

    def apply_update(self, new_file):
        """更新文件已下载：空闲时立即重启，游戏运行/启动中则等它结束后再重启。"""
        if self.launch_page.is_game_running():
            self._pending_update = new_file
            alert(self, _t("更新已下载"),
                  _t("检测到 Minecraft 正在启动或运行，更新将在游戏结束后"
                     "自动应用并重启启动器。"))
            return
        self._restart_with_update(new_file)

    def on_game_exited(self):
        # 由启动页在进程退出后调用；留一点缓冲等线程状态收尾
        if self._pending_update:
            self.after(1200, self._try_apply_pending)

    def _try_apply_pending(self):
        if not self._pending_update:
            return
        if self.launch_page.is_game_running():
            self.after(1500, self._try_apply_pending)
            return
        path, self._pending_update = self._pending_update, ""
        self._restart_with_update(path)

    def _restart_with_update(self, new_file):
        try:
            spawn_update_restart(new_file)
        except Exception as e:
            alert(self, _t("更新失败"), _err_text(e), "error")
            return
        try:
            self.launch_page.shutdown()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass
        os._exit(0)

    def on_close(self):
        lp = self.launch_page
        if lp.is_game_running():
            if not confirm(self, _t("关闭启动器"),
                           _t("检测到 Minecraft 正在运行，关闭启动器会一并结束游戏进程。"
                              "\n确定要关闭吗？"), danger=True):
                return
        try:
            lp.kill_game()
            lp.shutdown()
        except Exception:
            pass
        self.destroy()


def main():
    try:
        app = MainWindow()
        app.mainloop()
    except Exception:
        traceback.print_exc()
        try:
            messagebox.showerror("Lightning Craft Launcher",
                                 "启动失败：\n" + traceback.format_exc())
        except Exception:
            pass


if __name__ == "__main__":
    missing_mods, missing_pips = _check_missing()
    if missing_mods:
        if not _dep_window_tk(missing_mods, missing_pips):
            sys.exit(1)
    main()
