#!/usr/bin/env pwsh
# build_cuda_binary.ps1 — Build a CUDA llama-server binary for the Azazael accelerator chain.
#
# Prerequisites (install before running):
#   - Visual Studio 2022 with C++ workload ("Desktop development with C++")
#   - Ninja: choco install ninja
#   - CUDA Toolkit 12.4+: download redist archives from NVIDIA, extract to
#     C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.4
#   - Set CUDA_PATH to the toolkit root, add %CUDA_PATH%\bin to PATH
#
# Usage:
#   .\build_cuda_binary.ps1
#   # Output: C:\Users\clint\llama-server\bin\llama-b10964-cuda\llama-server.exe

$ErrorActionPreference = "Stop"
$BUILD_ROOT = "C:\Users\clint\llama-server\build-spark"
$OUT_DIR    = "C:\Users\clint\llama-server\bin\llama-b10964-cuda"
$BUILD_DIR  = "$BUILD_ROOT\build"

# Verify prerequisites
if (-not (Get-Command nvcc -ErrorAction SilentlyContinue)) {
    Write-Error "nvcc not found on PATH. Install CUDA Toolkit and add %CUDA_PATH%\bin to PATH."
}
if (-not (Get-Command ninja -ErrorAction SilentlyContinue)) {
    Write-Error "ninja not found on PATH. Run: choco install ninja"
}
if (-not $env:CUDA_PATH) {
    Write-Error "CUDA_PATH env var not set. Point it at the CUDA Toolkit root."
}

# Run vcvarsall.bat for x64 MSVC environment
$VsVars = "C:\Program Files\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvarsall.bat"
if (-not (Test-Path $VsVars)) {
    Write-Error "vcvarsall.bat not found at $VsVars. Install Visual Studio 2022."
}

# Configure
New-Item -ItemType Directory -Force -Path $BUILD_DIR | Out-Null
& cmd /c """
call "$VsVars" x64
cmake -S "$BUILD_ROOT" -B "$BUILD_DIR" -G "Ninja Multi-Config" `
  -DGGML_BACKEND_DL=ON `
  -DGGML_NATIVE=OFF `
  -DGGML_CPU=OFF `
  -DGGML_CUDA=ON `
  -DLLAMA_BUILD_BORINGSSL=ON
"""

# Build
& cmd /c """
cmake --build "$BUILD_DIR" --config Release -j $env:NUMBER_OF_PROCESSORS --target ggml-cuda
"""

# Install binary into the sidecar's expected location
$srcExe = Get-ChildItem -Path "$BUILD_DIR\bin\Release\llama-server.exe" -ErrorAction SilentlyContinue
if (-not $srcExe) {
    # Try Debug if Release missing
    $srcExe = Get-ChildItem -Path "$BUILD_DIR\bin\llama-server.exe" -ErrorAction SilentlyContinue
}
if (-not $srcExe) {
    Write-Error "llama-server.exe not found after build. Check $BUILD_DIR for errors."
}

New-Item -ItemType Directory -Force -Path $OUT_DIR | Out-Null
Copy-Item $srcExe.FullName -Destination "$OUT_DIR\llama-server.exe" -Force
Write-Host "Installed CUDA llama-server to $OUT_DIR\llama-server.exe"

# Verify it lists CUDA devices
& "$OUT_DIR\llama-server.exe" --list-devices 2>&1 | Select-String -Pattern "CUDA|GPU" -SimpleMatch
