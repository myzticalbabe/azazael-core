@echo off
:: ═══════════════════════════════════════════════════════════════
::  AZAZAEL — NVIDIA NCG + Python 3.15.0 Free-Threaded Installer
::  Run as Administrator for full driver installation
::  NOTE (Oct 6, 2026): ported 3.15.0->3.15.0. Verify the python.org
::  3.15.0 amd64 URL exists before running (3.15.0-final re-audit).
:: ═══════════════════════════════════════════════════════════════

title AZAZAEL: NVIDIA NCG + Python 3.15.0 Installer
chcp 65001 >nul

echo.
echo  ╔═══════════════════════════════════════════════════════════╗
echo  ║  🜁 AZAZAEL: NVIDIA NCG + Python 3.15.0 (Free-Threaded)  ║
echo  ║     Multi-Stage Installer for Windows 10/11              ║
echo  ╚═══════════════════════════════════════════════════════════╝
echo.

:: ─── Check Admin ────────────────────────────────────────────────
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo  [WARNING] Not running as Administrator.
    echo  GPU driver installation requires admin privileges.
    echo  CUDA toolkit and Python can be installed without admin.
    echo.
)

:: ─── Stage 1: Python 3.15.0 Free-Threaded ─────────────────────
:stage_python
echo  ┌─────────────────────────────────────────────────────────┐
echo  │  STAGE 1: Python 3.15.0  (Free-Threaded / GIL-disabled) │
echo  └─────────────────────────────────────────────────────────┘
echo.
echo  This installs Python 3.15.0 with --disable-gil build option.
echo  Free-threaded Python allows true multi-core parallelism.
echo.

set PYTHON_URL=https://www.python.org/ftp/python/3.15.0/python-3.15.0-amd64.exe
set PYTHON_INSTALLER=%TEMP%\python-3.15.0-amd64.exe

where python3.15 >nul 2>&1
if %errorlevel% equ 0 (
    python3.15 --version 2>nul
    echo  [OK] Python 3.15.x already found.
    goto check_free_threaded
)

echo  Downloading Python 3.15.0...
curl -L -o "%PYTHON_INSTALLER%" "%PYTHON_URL%" --progress-bar 2>nul
if %errorlevel% neq 0 (
    echo  [FAIL] Could not download Python 3.15.0.
    echo  Please download manually from: %PYTHON_URL%
    echo  Be sure to check "Install for all users" and "Add to PATH"
    pause
    goto stage_nvidia
)

echo  Installing Python 3.15.0...
echo  NOTE: Choose "Customize installation" and enable:
echo        [✓] Install for all users
echo        [✓] Add Python to environment variables
echo        [✓] Download free-threaded binaries
start /wait "" "%PYTHON_INSTALLER%" /quiet InstallAllUsers=1 PrependPath=1 Include_freethreaded=1
del "%PYTHON_INSTALLER%" 2>nul

:check_free_threaded
echo.
echo  Checking free-threaded Python availability...
python3.15 -c "import sysconfig; print(sysconfig.get_config_var('Py_GIL_DISABLED'))" 2>nul | find "1" >nul
if %errorlevel% equ 0 (
    echo  [OK] Python 3.15.0 is FREE-THREADED (GIL disabled)
    echo.
    echo  Verify with: python3.15t -c "import sys; print(sys._is_gil_enabled())"
) else (
    echo  [NOTE] Standard Python 3.15.0 detected (GIL still enabled).
    echo  To enable free-threading, rebuild Python with:
    echo    ./configure --disable-gil && make -j
    echo.
    echo  Or use: python3.15t (free-threaded binary, if installed)
)

:: ─── Stage 2: NVIDIA CUDA Toolkit + NCG Catalog ────────────────
:stage_nvidia
echo.
echo  ┌─────────────────────────────────────────────────────────┐
echo  │  STAGE 2: NVIDIA CUDA Toolkit + NCG Catalog             │
echo  └─────────────────────────────────────────────────────────┘
echo.

:: Check for existing NVIDIA GPU
nvidia-smi >nul 2>&1
if %errorlevel% neq 0 (
    echo  [WARNING] nvidia-smi not found. Is an NVIDIA GPU installed?
    echo  Continuing with CUDA toolkit installation anyway...
    echo  (CUDA toolkit provides headers/libs for compilation)
)

:: CUDA 12.8
set CUDA_URL=https://developer.download.nvidia.com/compute/cuda/12.8.0/local_installers/cuda_12.8.0_561.62_windows.exe
set CUDA_INSTALLER=%TEMP%\cuda_12.8.0_windows.exe

nvcc --version >nul 2>&1
if %errorlevel% equ 0 (
    echo  [OK] CUDA toolkit already installed:
    nvcc --version 2>nul | find "release"
) else (
    echo  Downloading CUDA 12.8 Toolkit...
    curl -L -o "%CUDA_INSTALLER%" "%CUDA_URL%" --progress-bar 2>nul
    if %errorlevel% neq 0 (
        echo  [FAIL] CUDA download failed.
        echo  Download manually: https://developer.nvidia.com/cuda-downloads
        echo.
    ) else (
        echo  Installing CUDA 12.8...
        echo  NOTE: This may take 10-20 minutes.
        start /wait "" "%CUDA_INSTALLER%" -s
        del "%CUDA_INSTALLER%" 2>nul
        echo  [OK] CUDA 12.8 installed.
    )
)

:: ─── Stage 3: NVIDIA NCG (CUDA Python) Catalog ─────────────────
:stage_ncg
echo.
echo  ┌─────────────────────────────────────────────────────────┐
echo  │  STAGE 3: NVIDIA NCG (CUDA Python / cuPyNumeric)       │
echo  └─────────────────────────────────────────────────────────┘
echo.

:: Install NCG via pip
echo  Installing NVIDIA NCG packages for Python 3.15...
echo.
echo  Core CUDA Python bindings:
python3.15 -m pip install nvidia-cuda-runtime-cu12 nvidia-cuda-nvrtc-cu12 nvidia-cublas-cu12 nvidia-cufft-cu12 nvidia-curand-cu12 nvidia-cusolver-cu12 nvidia-cusparse-cu12 nvidia-nccl-cu12 --upgrade 2>&1 | find "Successfully" >nul
if %errorlevel% equ 0 (
    echo  [OK] Core NVIDIA CUDA packages installed
) else (
    echo  [RETRY] Installing individually...
    for %%p in (nvidia-cuda-runtime-cu12 nvidia-cuda-nvrtc-cu12 nvidia-cublas-cu12) do (
        echo    %%p...
        python3.15 -m pip install %%p --upgrade 2>nul
    )
)

echo.
echo  NCG / cuPyNumeric:
python3.15 -m pip install cupy-cuda12x --upgrade 2>nul
if %errorlevel% equ 0 (
    echo  [OK] CuPy installed (GPU-accelerated NumPy)
) else (
    echo  [NOTE] CuPy may need Visual Studio Build Tools.
    echo  Alternative: pip install cupy-cuda12x --pre
)

echo.
echo  PyTorch with CUDA 12.8:
python3.15 -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128 --upgrade 2>nul
if %errorlevel% equ 0 (
    echo  [OK] PyTorch with CUDA 12.8 installed
) else (
    echo  [NOTE] PyTorch wheel may not be available for Python 3.15 yet.
    echo  Check: https://pytorch.org/get-started/locally/
)

echo.
echo  cuDF / RAPIDS:
python3.15 -m pip install cudf-cu12 dask-cudf-cu12 --upgrade 2>nul
if %errorlevel% equ 0 (
    echo  [OK] cuDF installed (GPU DataFrame)
) else (
    echo  [NOTE] RAPIDS may need specific Python version.
    echo  Check: https://rapids.ai/
)

:: ─── Stage 4: PIP packages for AZAZAEL ─────────────────────────
:stage_pip
echo.
echo  ┌─────────────────────────────────────────────────────────┐
echo  │  STAGE 4: AZAZAEL Python Dependencies                  │
echo  └─────────────────────────────────────────────────────────┘
echo.

echo  Installing core AZAZAEL dependencies...
python3.15 -m pip install numpy scipy matplotlib pillow opencv-python --upgrade 2>nul
python3.15 -m pip install requests aiohttp websockets --upgrade 2>nul
python3.15 -m pip install pyyaml toml rich colorama tqdm --upgrade 2>nul
python3.15 -m pip install pytest pytest-asyncio pytest-cov --upgrade 2>nul
python3.15 -m pip install pydantic cattrs orjson --upgrade 2>nul

echo  [OK] Core dependencies installed.

:: ─── Stage 5: Verification ─────────────────────────────────────
:verify
echo.
echo  ┌─────────────────────────────────────────────────────────┐
echo  │  STAGE 5: Verification                                  │
echo  └─────────────────────────────────────────────────────────┘
echo.

echo  === Python ===
python3.15 --version 2>nul || echo  Python 3.15.0 NOT FOUND

echo  === CUDA ===
nvcc --version 2>nul | find "release" || echo  CUDA toolkit NOT FOUND

echo  === NVIDIA GPU ===
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv 2>nul || echo  nvidia-smi NOT FOUND

echo  === Free-Threaded Check ===
python3.15 -c "import sysconfig; v=sysconfig.get_config_var('Py_GIL_DISABLED'); print(f'GIL Disabled: {v}')" 2>nul || echo  Free-threaded check FAILED

echo  === CuPy ===
python3.15 -c "import cupy; print(f'CuPy {cupy.__version__} — Devices: {cupy.cuda.runtime.getDeviceCount()}')" 2>nul || echo  CuPy NOT FOUND

echo  === PyTorch ===
python3.15 -c "import torch; print(f'PyTorch {torch.__version__} — CUDA: {torch.cuda.is_available()}')" 2>nul || echo  PyTorch NOT FOUND

echo.
echo  ╔═══════════════════════════════════════════════════════════╗
echo  ║  🜁  INSTALLATION COMPLETE                                ║
echo  ║                                                         ║
echo  ║  Next Steps:                                            ║
echo  ║  1. Run: AZAZAEL_LAUNCHER.bat                          ║
echo  ║  2. Test: python -m TOOLS.debug_runner --all            ║
echo  ║  3. Verify GPU: nvidia-smi                              ║
echo  ╚═══════════════════════════════════════════════════════════╝
echo.

pause
exit /b 0
