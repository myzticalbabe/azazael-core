#!/bin/bash
# Azazael Ubuntu 26.04 Post-Install Script
# Run AFTER Ubuntu installation on E:\
# Usage: sudo bash post_install.sh

set -e

echo "========================================"
echo "Azazael Ubuntu 26.04 Post-Install"
echo "========================================"

# Color codes
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
   echo "Please run as root: sudo bash post_install.sh"
   exit 1
fi

log "Starting Azazael setup..."

# Fix apt_pkg issue (common on fresh Ubuntu 26.04)
log "Fixing apt_pkg compatibility..."
apt install --reinstall -y python3-apt 2>/dev/null || true

# ===== 1. SYSTEM UPDATE =====
log "Updating system..."
apt update && apt upgrade -y

# ===== 2. INSTALL PYTHON 3.15t (FREE-THREADING) =====
log "Installing Python 3.15t free-threading build..."
if [ -x /opt/python315t/bin/python3.15 ]; then
  log "Python 3.15t already present at /opt/python315t -- skipping source build."
else
# NOTE: Python 3.13 (system default) is NOT used. 3.14t was RETIRED 2026-10-19
# (see azazael/src/CORE/python_env.py); production is the 3.15t free-threading
# build, matching the Windows pillar. Install to /opt/python315t/.
PY315_VER="3.15.0rc3"

# Prerequisites for building 3.15t from source:
apt install -y wget build-essential libssl-dev zlib1g-dev \
    libbz2-dev libreadline-dev libsqlite3-dev libncurses5-dev \
    libncursesw5-dev xz-utils tk-dev libffi-dev liblzma-dev \
    libgdbm-dev libnss3-dev libdb5.3-dev uuid-dev || warn "libdb5.3-dev not available — Python build skips Berkeley DB (harmless)"

# Download and build Python 3.15t from source with --disable-gil
cd /tmp
wget "https://www.python.org/ftp/python/${PY315_VER}/Python-${PY315_VER}.tar.xz"
# Optionally verify checksum: wget -qO- "https://www.python.org/ftp/python/${PY315_VER}/Python-${PY315_VER}.tar.xz.asc" | gpg --verify
tar xf "Python-${PY315_VER}.tar.xz"
cd "Python-${PY315_VER}"
./configure --prefix=/opt/python315t --disable-gil --enable-optimizations --with-ensurepip=install
make -j$(nproc)
make install
fi

# Add to PATH for all users (3.14t symlinks are deliberately not recreated)
ln -sf /opt/python315t/bin/python3.15 /usr/local/bin/python3.15t
ln -sf /opt/python315t/bin/python3.15 /usr/local/bin/python3.15
# NOTE: /usr/local/bin/python3 is deliberately untouched -- the OS
# and existing venvs resolve python3 already; only versioned names here.

# Upgrade pip for 3.15t
/opt/python315t/bin/python3.15 -m pip install --upgrade pip setuptools wheel

# Verify free-threading
/opt/python315t/bin/python3.15 -c "
import sys
print(f'Python {sys.version}')
ft = hasattr(sys, '_is_gil_enabled') and not sys._is_gil_enabled()
print(f'Free-threaded: {ft}')
assert ft, 'Must be free-threading build!'
"

# ===== 3. INSTALL BUILD TOOLS =====
log "Installing build tools..."
apt install -y \
    cmake \
    git \
    curl \
    vim \
    htop \
    tmux \
    unzip \
    zip \
    libopenblas-dev \
    liblapack-dev

# ===== 4. INSTALL INTEL GPU/NPU DRIVERS (26.04 Resolute) =====
log "Installing Intel GPU drivers..."
# Check if Intel repository has packages for Ubuntu 26.04 (resolute)
INTEL_REPO_URL="https://repositories.intel.com/graphics/ubuntu"
UBUNTU_CODENAME=$(lsb_release -cs 2>/dev/null || echo "resolute")
log "Ubuntu codename: ${UBUNTU_CODENAME}"
if wget -q --spider "${INTEL_REPO_URL}/dists/${UBUNTU_CODENAME}/Release" 2>/dev/null; then
    log "Intel repo supports ${UBUNTU_CODENAME} — installing official drivers..."
    KEYRING_FILE="/usr/share/keyrings/intel-graphics.gpg"
    if wget -qO - "${INTEL_REPO_URL}/intel-graphics.key" | gpg --dearmor -o "${KEYRING_FILE}" 2>/dev/null; then
        echo "deb [arch=amd64 signed-by=${KEYRING_FILE}] ${INTEL_REPO_URL} ${UBUNTU_CODENAME} main" > /etc/apt/sources.list.d/intel-gpu.list
    else
        warn "gpg --dearmor failed — falling back to legacy apt-key method"
        wget -qO - "${INTEL_REPO_URL}/intel-graphics.key" | apt-key add -
        apt-add-repository "deb [arch=amd64] ${INTEL_REPO_URL} ${UBUNTU_CODENAME} main"
    fi
    apt update || warn "Intel repo update failed — skipping GPU drivers"
    apt install -y intel-opencl-icd intel-level-zero-gpu intel-media-va-driver-non-free || \
        warn "Some Intel GPU packages not available for ${UBUNTU_CODENAME}"
else
    warn "Intel repo does not support ${UBUNTU_CODENAME} yet — skipping GPU driver installation."
    warn "Install manually from https://dgpu-docs.intel.com when 26.04 support is added."
fi

# ===== 5. CREATE 3.15t VENV =====
log "Creating Python 3.15t venv..."
cd /home/$(logname)
/opt/python315t/bin/python3.15 -m venv azazael_315t
source azazael_315t/bin/activate

# ===== 6. UPGRADE PIP =====
log "Upgrading pip..."
pip install --upgrade pip setuptools wheel

# ===== 7. INSTALL CORE ML PACKAGES (3.15t compatible) =====
log "Installing PyTorch (CPU)..."
pip install torch --index-url https://download.pytorch.org/whl/cpu

log "Installing transformers and NLP..."
pip install transformers huggingface_hub nltk

log "Installing OpenVINO..."
pip install openvino

log "Installing NumPy..."
pip install numpy

# Pure-Python packages — should work on 3.15t without C extension issues:
log "Installing Jupyter and utilities..."
pip install jupyterlab ipython fastapi uvicorn matplotlib

# NOTE: a package with only a cp314t wheel cannot load in a 3.15 process.
# If no cp315t wheel exists, install it into the sanctioned 3.14 GIL venv
# (Windows: C:/Azazael/.venvs/py314m, documented in python_env.py) instead of
# pinning this pillar back to 3.14.

# ===== 8. OPTIONAL: LOCAL cp315t WHEELS =====
log "Looking for local cp315t wheels..."
if [ -d "/mnt/g/AZAZAEL/cp315t_wheels" ]; then
    pip install /mnt/g/AZAZAEL/cp315t_wheels/*.whl --no-deps 2>/dev/null || true
else
    log "No /mnt/g/AZAZAEL/cp315t_wheels — skipping (cp314t wheels are not loadable here)"
fi

# ===== 9. VERIFY INSTALLATION =====
log "Verifying installations..."
PYTHON=/opt/python315t/bin/python3.15
$PYTHON -c "
import sys
ft = hasattr(sys, '_is_gil_enabled') and not sys._is_gil_enabled()
print(f'Python 3.15t free-threading: {ft}')
import torch
import transformers
import nltk
import openvino
import numpy
print(f'PyTorch: {torch.__version__}')
print(f'Transformers: {transformers.__version__}')
print(f'OpenVINO: {openvino.__version__}')
print(f'NumPy: {numpy.__version__}')
# These are pure Python and should work:
import matplotlib; print(f'Matplotlib: {matplotlib.__version__}')
import fastapi; print(f'FastAPI: OK')
"

# ===== 10. ADD USER TO GROUPS =====
log "Configuring user groups..."
usermod -aG video $(logname)
usermod -aG render $(logname)

# ===== 11. CREATE STARTUP SCRIPT =====
log "Creating startup script..."
cat > /home/$(logname)/azazael_start.sh << 'EOF'
#!/bin/bash
source ~/azazael_315t/bin/activate
echo "Azazael 3.15t venv activated"
cd ~/projects/azazael
EOF
chmod +x /home/$(logname)/azazael_start.sh

# ===== 12. CREATE PROJECT DIRECTORY =====
log "Creating project directory..."
mkdir -p /home/$(logname)/projects/azazael
chown -R $(logname):$(logname) /home/$(logname)/projects

echo ""
echo "========================================"
echo "Azazael Installation Complete!"
echo "========================================"
echo ""
echo "Next steps:"
echo "1. Reboot: sudo reboot"
echo "2. Activate venv: source ~/azazael_315t/bin/activate"
echo "3. Test: /opt/python315t/bin/python3.15 -c 'import torch; print(torch.__version__)'"
echo ""
echo "Project directory: ~/projects/azazael/"
echo ""
