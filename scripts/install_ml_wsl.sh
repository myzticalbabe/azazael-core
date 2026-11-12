#!/bin/bash
# ML packages for Python 3.15t on Ubuntu 26.04 (3.14t retired 2026-10-19)
# Run: wsl -d Ubuntu-26.04 -u root bash /mnt/c/Azazael/azazael/scripts/install_ml_wsl.sh

# Live 3.15t on the WSL pillar: /opt/python315t (binary is python3.15;
# /usr/local/bin/python3.15t is a symlink to it for shebang compatibility).
PY=/opt/python315t/bin/python3.15

echo '=== Installing ML packages for 3.15t ==='

$PY -m pip install torch --index-url https://download.pytorch.org/whl/cpu 2>&1 | tail -5
echo 'torch done'

$PY -m pip install transformers nltk openvino numpy 2>&1 | tail -5
echo 'core ML done'

$PY -m pip install jupyterlab matplotlib fastapi 2>&1 | tail -5
echo 'utilities done'

echo '=== Verification ==='
$PY -c "import sys; ft = hasattr(sys, '_is_gil_enabled') and not sys._is_gil_enabled(); print(f'Free-threaded: {ft}'); import torch, nltk, openvino, numpy; print(f'PyTorch: {torch.__version__}'); print(f'NumPy: {numpy.__version__}'); print(f'OpenVINO: {openvino.__version__}'); import matplotlib; print('Matplotlib OK'); import fastapi; print('FastAPI OK'); import transformers; print('Transformers OK'); print('ALL PACKAGES VERIFIED!')"
