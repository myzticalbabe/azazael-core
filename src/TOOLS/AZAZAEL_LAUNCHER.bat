@echo off
:: ═══════════════════════════════════════════════════════════
::  AZAZAEL Desktop Launcher — Unified Selector Switch
::  Double-click to open the Ultimate Selector dashboard
::  and access all AZAZAEL tools from Windows Desktop
:: ═══════════════════════════════════════════════════════════

chcp 65001 >nul
title AZAZAEL Ultimate Launcher

set AZAZAEL_ROOT=G:\azazael-latest
if not exist "%AZAZAEL_ROOT%" set AZAZAEL_ROOT=C:\azazael
if not exist "%AZAZAEL_ROOT%" set AZAZAEL_ROOT=%USERPROFILE%\azazael

:menu
cls
echo.
echo  ╔═══════════════════════════════════════════════════════════╗
echo  ║     🜁  A Z A Z A E L   —   U L T I M A T E   M E N U   🜁  ║
echo  ╠═══════════════════════════════════════════════════════════╣
echo  ║                                                         ║
echo  ║   [1]  Ultimate Selector Dashboard  (HTML launcher)     ║
echo  ║   [2]  KLINK Status  (Component health check)           ║
echo  ║   [3]  BIBL Crawler Index  (Search 10,416 terms)       ║
echo  ║   [4]  Mermaid Architecture Graphs                     ║
echo  ║   [5]  Animation Showcase  (54 protocols)               ║
echo  ║   [6]  Cinematic Scroll Generator                       ║
echo  ║   [7]  Scroll Video Recorder  (MP4/WebM)                ║
echo  ║   [8]  Structure Visualizer  (Force Graph)              ║
echo  ║   [9]  Emoji Maker  (Dynamic emotional emojis)          ║
echo  ║  [10]  ASCII Flowcharts                                 ║
echo  ║  [11]  Debug Test Runner  (.debug reports)              ║
echo  ║  [12]  Video Compressor  (CRF 28, ~16MB target)        ║
echo  ║  [13]  Rust / Go Interpreter Runners                    ║
echo  ║  [14]  Component Map  (Full ecosystem documentation)    ║
echo  ║  [15]  Archive System  (Cleanup 9.8 GB old files)      ║
echo  ║  [16]  Sync to G:\azazael-latest                        ║
echo  ║                                                         ║
echo  ║   [S]  Setup / Install  (NVIDIA NCG + Python 3.14.5)   ║
echo  ║   [H]  Help / About                                     ║
echo  ║   [Q]  Quit                                             ║
echo  ║                                                         ║
echo  ╚═══════════════════════════════════════════════════════════╝
echo.
echo  Root: %AZAZAEL_ROOT%
echo.

set /p choice="  Select option: "

if "%choice%"=="1" goto ultimate
if "%choice%"=="2" goto klink
if "%choice%"=="3" goto bibl
if "%choice%"=="4" goto mermaid
if "%choice%"=="5" goto animations
if "%choice%"=="6" goto cinematic
if "%choice%"=="7" goto scrollvideo
if "%choice%"=="8" goto structure
if "%choice%"=="9" goto emoji
if "%choice%"=="10" goto ascii
if "%choice%"=="11" goto debug
if "%choice%"=="12" goto compress
if "%choice%"=="13" goto interpreters
if "%choice%"=="14" goto compmap
if "%choice%"=="15" goto archive
if "%choice%"=="16" goto sync
if /i "%choice%"=="S" goto setup
if /i "%choice%"=="H" goto help
if /i "%choice%"=="Q" exit /b 0
echo  Invalid option. Try again.
timeout /t 2 >nul
goto menu

:ultimate
  echo  Opening Ultimate Selector Dashboard...
  start "" "%AZAZAEL_ROOT%\src\CODEX\ULTIMATE_SELECTOR.html"
  goto menu

:klink
  echo  Running KLINK status...
  echo.
  cd /d "%AZAZAEL_ROOT%"
  set AZAZAEL_ROOT=%AZAZAEL_ROOT%
  python scripts\klink.py status
  echo.
  pause
  goto menu

:bibl
  echo  Running BIBL Crawler stats...
  echo.
  cd /d "%AZAZAEL_ROOT%"
  set AZAZAEL_ROOT=%AZAZAEL_ROOT%
  python src\ORCHESTRATION\BIBL\bibl_crawler.py stats
  echo.
  pause
  goto menu

:mermaid
  echo  Opening Mermaid Architecture Graphs...
  start "" "%AZAZAEL_ROOT%\src\CODEX\MERMAID_GRAPHS.html"
  goto menu

:animations
  echo  Opening Animation Showcase...
  start "" "%AZAZAEL_ROOT%\src\CODEX\ANIMATION_SHOWCASE.html"
  goto menu

:cinematic
  echo  Opening Cinematic Scroll Generator...
  start "" "%AZAZAEL_ROOT%\src\CODEX\CINEMATIC_SCROLL.html"
  goto menu

:scrollvideo
  echo  Opening Scroll Video Recorder...
  start "" "%AZAZAEL_ROOT%\src\CODEX\SCROLL_VIDEO_GENERATOR.html"
  goto menu

:structure
  echo  Opening Structure Visualizer...
  start "" "%AZAZAEL_ROOT%\src\CODEX\STRUCTURE_VISUALIZER.html"
  goto menu

:emoji
  echo  Running Emoji Maker...
  echo.
  cd /d "%AZAZAEL_ROOT%"
  set AZAZAEL_ROOT=%AZAZAEL_ROOT%
  python scripts\emoji_demo.py
  echo.
  pause
  goto menu

:ascii
  echo  Running ASCII Flowchart Generator...
  echo.
  cd /d "%AZAZAEL_ROOT%"
  set AZAZAEL_ROOT=%AZAZAEL_ROOT%
  python scripts\ascii_demo.py
  echo.
  pause
  goto menu

:debug
  echo  Running Debug Test Runner...
  echo.
  cd /d "%AZAZAEL_ROOT%"
  set AZAZAEL_ROOT=%AZAZAEL_ROOT%
  python -m TOOLS.debug_runner --all
  echo.
  pause
  goto menu

:compress
  echo  Video Compressor
  echo  Target: CRF 28, ~16 MB output
  echo.
  cd /d "%AZAZAEL_ROOT%"
  set AZAZAEL_ROOT=%AZAZAEL_ROOT%
  python src\CODEX\VIDEOS\compress_video.py formats
  echo.
  echo  Usage: python src\CODEX\VIDEOS\compress_video.py compress ^<input.mp4^> ^<output.mp4^>
  echo  Or:    python src\CODEX\VIDEOS\compress_video.py compress ^<input.mp4^> ^<output.wmv^> --format wmv
  echo.
  pause
  goto menu

:interpreters
  echo  Rust / Go Interpreter Status
  echo.
  cd /d "%AZAZAEL_ROOT%"
  set AZAZAEL_ROOT=%AZAZAEL_ROOT%
  python -c "import sys; sys.path.insert(0,'src'); from INTERPRETERS.rust_runner import RustRunner; r=RustRunner(); print(f'Rust: rustc={r.has_rustc}, cargo={r.has_cargo}')" 2>nul
  python -c "import sys; sys.path.insert(0,'src'); from INTERPRETERS.go_runner import GoRunner; g=GoRunner(); print(f'Go: available={g.has_go}')" 2>nul
  echo.
  echo  If not found: install Rust (https://rustup.rs) and Go (https://go.dev/dl)
  pause
  goto menu

:compmap
  echo  Opening Component Map...
  start "" "%AZAZAEL_ROOT%\output\AZAZAEL_COMPONENT_MAP.html"
  goto menu

:archive
  echo  Archive System — Cleaning up old files...
  echo.
  cd /d "%AZAZAEL_ROOT%"
  set AZAZAEL_ROOT=%AZAZAEL_ROOT%
  echo  DRY RUN (preview):
  python src\TOOLS\archive_system.py --dry --all
  echo.
  echo  To archive: python src\TOOLS\archive_system.py --all
  echo.
  pause
  goto menu

:sync
  echo  Syncing to G:\azazael-latest...
  echo.
  if not exist "G:\" (
    echo  ERROR: G: drive not mounted. Please mount it first.
    pause
    goto menu
  )
  echo  Source: %AZAZAEL_ROOT%
  echo  Target: G:\azazael-latest\
  echo.
  xcopy "%AZAZAEL_ROOT%\src" "G:\azazael-latest\src" /E /I /Y /D >nul 2>&1
  xcopy "%AZAZAEL_ROOT%\scripts" "G:\azazael-latest\scripts" /E /I /Y /D >nul 2>&1
  xcopy "%AZAZAEL_ROOT%\output" "G:\azazael-latest\output" /E /I /Y /D >nul 2>&1
  xcopy "%AZAZAEL_ROOT%\*.bat" "G:\azazael-latest\" /Y /D >nul 2>&1
  copy "%~f0" "G:\azazael-latest\AZAZAEL_LAUNCHER.bat" /Y >nul 2>&1
  echo  Sync complete to G:\azazael-latest\
  echo.
  pause
  goto menu

:setup
  echo  Running NVIDIA NCG + Python 3.15.0 free-threaded installer...
  echo.
  if exist "%AZAZAEL_ROOT%\src\TOOLS\install_nvidia_ncg_python315.bat" (
    call "%AZAZAEL_ROOT%\src\TOOLS\install_nvidia_ncg_python315.bat"
  ) else (
    echo  Installer script not found. Download from:
    echo  %AZAZAEL_ROOT%\src\TOOLS\install_nvidia_ncg_python315.bat
  )
  echo.
  pause
  goto menu

:help
  cls
  echo.
  echo  ╔═══════════════════════════════════════════════════════════╗
  echo  ║               🜁  A Z A Z A E L   H E L P   🜁              ║
  echo  ╠═══════════════════════════════════════════════════════════╣
  echo  ║                                                         ║
  echo  ║  AZAZAEL is a distributed computing framework with:     ║
  echo  ║                                                         ║
  echo  ║   • 247 Python modules across 7 architecture tiers     ║
  echo  ║   • 21 HTML templates for visualization                  ║
  echo  ║   • 54 CSS animation protocols                          ║
  echo  ║   • 25 KLINK CLI commands                               ║
  echo  ║   • 11 WORMZ core system agents                         ║
  echo  ║   • Rust 1.95 + Go 1.26 interpreter support             ║
  echo  ║   • BIBL crawler: 10,416 terms across 69 files          ║
  echo  ║   • Video compression: MP4 + WMV ~16 MB target          ║
  echo  ║                                                         ║
  echo  ║  Quick Start:                                          ║
  echo  ║   • Double-click AZAZAEL_LAUNCHER.bat on Desktop       ║
  echo  ║   • Or: python scripts\klink.py status                  ║
  echo  ║   • Docs: %AZAZAEL_ROOT%\output\AZAZAEL_COMPONENT_MAP.html║
  echo  ║                                                         ║
  echo  ╚═══════════════════════════════════════════════════════════╝
  echo.
  pause
  goto menu
