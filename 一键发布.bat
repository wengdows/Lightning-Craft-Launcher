@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title LCL 一键发布新版

rem 用法: 双击运行，按提示输入 GitHub Token（https://github.com/settings/tokens）
rem       Token 需要 repo 权限（classic PAT）或 Contents 读写权限（fine-grained）
rem       可选: 一键发布.bat 1.0.2   指定版本号，默认在线上最新版本末尾 +1
rem       也可先 set GH_TOKEN=xxx 再运行，跳过输入
rem 说明: 版本号会写入源码、重新打包，并作为 Release 标签；
rem       启动器「检查更新」读取 releases/latest 的标签与 EXE 附件，自动更新即生效。

set "REPO=wengdows/Lightning-Craft-Launcher"
set "API=https://api.github.com/repos/%REPO%"
set "BRANCH=main"
set "EXE_NAME=LightningCraftLauncher.exe"
set "PY_NAME=Lightning.Craft.Launcher.py"
set "PY_SRC=Lightning Craft Launcher.py"
set "PYW_SRC=Lightning Craft Launcher.pyw"
set "SPEC=LightningCraftLauncher.spec"
set "NOTES=%TEMP%\lcl_release_notes.md"

echo ============================================================
echo    LCL 一键发布新版
echo    仓库: %REPO%
echo ============================================================
echo.

rem ---------- 1. Token ----------
set "TOKEN=%GH_TOKEN%"
if "%TOKEN%"=="" (
  echo 请输入 GitHub Token（需 repo 权限；输入后不会保存到磁盘）:
  set /p TOKEN=
)
if "%TOKEN%"=="" (
  echo [错误] 未提供 Token，已退出。
  goto :fail
)

set "WHO="
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "try{$u=Invoke-RestMethod -Uri 'https://api.github.com/user' -Headers @{Authorization='Bearer %TOKEN%'} -TimeoutSec 20; Write-Output $u.login}catch{Write-Output ''}"`) do set "WHO=%%i"
if "!WHO!"=="" (
  echo [错误] Token 无效或网络不通，请检查后重试。
  goto :fail
)
echo 已登录 GitHub 账号: !WHO!
echo.

rem ---------- 2. 计算版本号 ----------
set "LATEST="
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "try{(Invoke-RestMethod -Uri '%API%/releases/latest' -Headers @{Authorization='Bearer %TOKEN%';Accept='application/vnd.github+json'} -TimeoutSec 20).tag_name}catch{Write-Output ''}"`) do set "LATEST=%%i"

if "!LATEST!"=="" (
  echo 未找到已有 Release，将读取本地源码中的版本号作为基准。
  for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "try{$t=Get-Content -LiteralPath '%PY_SRC%' -Raw -Encoding UTF8; $q=[char]34; if($t -match ('APP_VERSION = '+$q+'([0-9.]+)')){Write-Output ('v'+$matches[1])}}catch{}"`) do set "LATEST=%%i"
)
echo 当前线上最新版本: !LATEST!

set "VER=!LATEST!"
set "VER=!VER:v=!"
set "VER=!VER:V=!"
set "VER=!VER:-=!"
set "MAJ=1"
set "MIN=0"
set "PAT=0"
for /f "tokens=1,2,3 delims=." %%a in ("!VER!") do (
  if not "%%a"=="" set "MAJ=%%a"
  if not "%%b"=="" set "MIN=%%b"
  if not "%%c"=="" set "PAT=%%c"
)
set /a PAT+=1
set "NEWVER=!MAJ!.!MIN!.!PAT!"

rem 允许用命令行参数指定版本：一键发布.bat 1.0.2
if not "%~1"=="" set "NEWVER=%~1"
set "NEWVER=!NEWVER:v=!"
set "NEWVER=!NEWVER:V=!"

echo 本次将发布: v!NEWVER!
echo.
echo 说明: 版本号必须比 EXE 内置版本更新，启动器的「检查更新」才会提示。
set "OK="
set /p OK=确认发布 v!NEWVER! ? (Y/N):
if /i not "!OK!"=="Y" (
  echo 已取消。
  goto :end
)
echo.

rem ---------- 3. 同步源码副本 ----------
if exist "%PYW_SRC%" (
  copy /y "%PYW_SRC%" "%PY_SRC%" >nul
  echo [1/6] 已同步 .pyw 到 .py
) else (
  echo [1/6] 未找到 .pyw，跳过同步
)

rem ---------- 4. 写入新版本号 ----------
powershell -NoProfile -Command "$q=[char]34; $f='%PY_SRC%'; $t=Get-Content -LiteralPath $f -Raw -Encoding UTF8; $t=$t -replace ('APP_VERSION = '+$q+'[0-9.]+'+$q), ('APP_VERSION = '+$q+'%NEWVER%'+$q); [IO.File]::WriteAllText((Resolve-Path -LiteralPath $f).Path,$t,(New-Object System.Text.UTF8Encoding $false))" >nul
if errorlevel 1 (
  echo [错误] 写入版本号失败。
  goto :fail
)
if exist "%PYW_SRC%" (
  powershell -NoProfile -Command "$q=[char]34; $f='%PYW_SRC%'; $t=Get-Content -LiteralPath $f -Raw -Encoding UTF8; $t=$t -replace ('APP_VERSION = '+$q+'[0-9.]+'+$q), ('APP_VERSION = '+$q+'%NEWVER%'+$q); [IO.File]::WriteAllText((Resolve-Path -LiteralPath $f).Path,$t,(New-Object System.Text.UTF8Encoding $false))" >nul
)
findstr /b /c:"APP_VERSION = " "%PY_SRC%"
echo [2/6] 源码版本号已更新

rem ---------- 5. 准备 EXE ----------
set "EXEPATH=dist\%EXE_NAME%"
set "REBUILD=Y"
if exist "!EXEPATH!" (
  echo.
  echo 已存在 !EXEPATH!
  set /p REBUILD=是否重新打包? Y=重新打包 N=直接使用该 EXE:
)
if /i not "!REBUILD!"=="Y" (
  echo [3/6] 跳过打包，直接使用已有 EXE
  echo       注意: 该 EXE 内置版本可能不是 v!NEWVER!，但自动更新以新标签为准
) else (
  where pyinstaller >nul 2>nul
  if errorlevel 1 (
    where python >nul 2>nul
    if errorlevel 1 (
      echo [错误] 未找到 python 或 pyinstaller，无法打包。
      goto :fail
    )
    echo 未检测到 PyInstaller，正在安装 ...
    python -m pip install -q "pyinstaller>=6.3,<7"
    if errorlevel 1 (
      echo [错误] PyInstaller 安装失败。
      goto :fail
    )
    set "BUILDCMD=python -m PyInstaller"
  ) else (
    set "BUILDCMD=pyinstaller"
  )
  echo [3/6] 正在打包 EXE（可能需要几分钟）...
  if exist "%SPEC%" (
    !BUILDCMD! --noconfirm --clean "%SPEC%"
  ) else (
    !BUILDCMD! --noconfirm --clean --onefile --windowed --name LightningCraftLauncher --icon assets\icon.ico "%PY_SRC%"
  )
  if errorlevel 1 (
    echo [错误] 打包失败，请查看上面的日志。
    goto :fail
  )
)
if not exist "!EXEPATH!" (
  echo [错误] 未找到 !EXEPATH!，请先打包或把 EXE 放到该路径。
  goto :fail
)
echo [4/6] EXE 就绪: !EXEPATH!

rem ---------- 6. 提交并推送源码 ----------
echo [5/6] 提交源码 ...
git add -A >nul 2>nul
git commit -m "release: v!NEWVER!" >nul 2>nul
git push origin HEAD:%BRANCH% >nul 2>nul
if errorlevel 1 (
  echo       警告: 源码推送失败（可稍后手动 git push），不影响 EXE 发布。
) else (
  echo       源码已推送到 %BRANCH%
)

rem ---------- 7. 生成发布说明 ----------
> "%NOTES%" echo LCL v!NEWVER!
>> "%NOTES%" echo.
>> "%NOTES%" echo 本次更新:
>> "%NOTES%" echo - 布局与界面细节优化
>> "%NOTES%" echo - 功能修复与稳定性改进
>> "%NOTES%" echo.
>> "%NOTES%" echo 下载 `%EXE_NAME%` 覆盖旧文件即可；启动器内「关于 - 检查更新」也可自动更新。

rem ---------- 8. 创建 Release 并上传 ----------
echo [6/6] 创建 Release 并上传附件 ...
set "RID="
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "$h=@{Authorization='Bearer %TOKEN%';Accept='application/vnd.github+json'}; $tag='v%NEWVER%'; try{$r=Invoke-RestMethod -Uri ('%API%/releases/tags/'+$tag) -Headers $h -TimeoutSec 30}catch{$notes=Get-Content -LiteralPath '%NOTES%' -Raw -Encoding UTF8; $b=@{tag_name=$tag;name=$tag;target_commitish='%BRANCH%';body=$notes;draft=$false;prerelease=$false}; $json=ConvertTo-Json -InputObject $b; $r=Invoke-RestMethod -Method Post -Uri '%API%/releases' -Headers $h -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($json)) -TimeoutSec 60}; Write-Output $r.id"`) do set "RID=%%i"

if "!RID!"=="" (
  echo [错误] 创建 Release 失败（可能是 Token 权限不足或标签冲突）。
  goto :fail
)
echo       Release ID: !RID!

rem 同名附件先删掉，避免重复
powershell -NoProfile -Command "$h=@{Authorization='Bearer %TOKEN%';Accept='application/vnd.github+json'}; try{$a=Invoke-RestMethod -Uri ('%API%/releases/%RID%/assets') -Headers $h -TimeoutSec 30; foreach($x in $a){ if($x.name -eq '%EXE_NAME%' -or $x.name -eq '%PY_NAME%'){ Invoke-RestMethod -Method Delete -Uri $x.url -Headers $h } }}catch{}" >nul

copy /y "%PY_SRC%" "%TEMP%\%PY_NAME%" >nul

echo       上传 %EXE_NAME% ...
powershell -NoProfile -Command "$h=@{Authorization='Bearer %TOKEN%';Accept='application/vnd.github+json'}; try{$r=Invoke-RestMethod -Method Post -Uri ('https://uploads.github.com/repos/%REPO%/releases/%RID%/assets?name=' + [uri]::EscapeDataString('%EXE_NAME%')) -Headers $h -ContentType 'application/octet-stream' -InFile ((Resolve-Path -LiteralPath '%EXEPATH%').Path) -TimeoutSec 1800; Write-Output ('OK ' + $r.name + '  ' + [math]::Round($r.size/1MB,1) + ' MB')}catch{Write-Output ('FAIL ' + $_.Exception.Message)}"

echo       上传 %PY_NAME% ...
powershell -NoProfile -Command "$h=@{Authorization='Bearer %TOKEN%';Accept='application/vnd.github+json'}; try{$r=Invoke-RestMethod -Method Post -Uri ('https://uploads.github.com/repos/%REPO%/releases/%RID%/assets?name=' + [uri]::EscapeDataString('%PY_NAME%')) -Headers $h -ContentType 'application/octet-stream' -InFile ('%TEMP%\%PY_NAME%') -TimeoutSec 600; Write-Output ('OK ' + $r.name + '  ' + [math]::Round($r.size/1KB,1) + ' KB')}catch{Write-Output ('FAIL ' + $_.Exception.Message)}"

echo.
echo ============================================================
echo   发布完成
echo   版本页: https://github.com/%REPO%/releases/tag/v!NEWVER!
echo   最新版: https://github.com/%REPO%/releases/latest
echo ============================================================
echo.
echo 提示: 启动器通过 releases/latest 读取版本号与 EXE 附件，
echo       请勿删除已发布的旧版本，否则部分用户无法回滚。
goto :end

:fail
echo.
echo 发布未完成，请根据上面的提示处理后重试。
pause
exit /b 1

:end
echo.
pause
endlocal
