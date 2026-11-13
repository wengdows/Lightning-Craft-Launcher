# Lightning Craft Launcher

一个简洁的 Minecraft 启动器，使用 Python + tkinter 编写，单文件即可运行。

## 功能

- 多账号：离线 / 微软正版 / 第三方认证（authlib-injector）
- 多版本管理：原版、Forge、Fabric、Quilt、NeoForge
- 下载与安装：游戏版本、加载器、整合包（.mrpack / .zip）
- 模组管理：启用 / 禁用、依赖检查与一键更新
- 联机：陶瓦（Terracotta）房间、恒星 FRP 隧道
- 高级启动参数：内存、GC、JVM 参数、窗口大小、启动前命令
- 三套主题、中英文界面
- 启动器自动更新（基于 GitHub Releases）

## 运行

```bash
pip install minecraft-launcher-lib requests
python "Lightning Craft Launcher.py"
```

## 自动更新

「关于」页的「检查更新」会读取本仓库的最新 Release：

- 版本号取自 Release 的 `tag_name`（例如 `v1.0.1`）
- 更新包取自该 Release 的 Assets，优先使用 `.py` 文件
- 没有新版本时提示「此版本已是最新」
- 有新版本时展示版本号与更新说明，点击「立即更新」显示下载进度
- 下载完成后自动替换并重启；若 Minecraft 正在启动或运行，则等游戏结束后再自动重启

发布新版本：修改源码顶部的 `APP_VERSION`，推送代码后创建同名 tag 的 Release，
并把 `Lightning Craft Launcher.py` 作为资产上传即可。
