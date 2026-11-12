#!/bin/bash
# =============================================================================
# Azazael/PERSEPHONE WSL Admin Activation Script
#
# Purpose: Configure maximum command-line capabilities in WSL
#
# Usage: Run with: bash admin_activator_wsl.sh
# =============================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_ok() {
    echo -e "${GREEN}[OK]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

echo "================================================================================"
echo "WSL Admin Activation Script - Azazael/PERSEPHONE"
echo "================================================================================"
echo

# Detect WSL distribution
DISTRO=$(cat /etc/os-release | grep "^ID=" | cut -d'=' -f2)
VERSION=$(cat /etc/os-release | grep "^VERSION_ID=" | cut -d'=' -f2)
log_info "WSL Distribution: $DISTRO $VERSION"

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    log_ok "Running as root"
    ROOT_MODE=1
else
    log_warn "Not running as root"
    log_info "Run with: sudo bash $0"
    ROOT_MODE=0
fi

echo
echo "================================================================================"
echo "Environment Configuration"
echo "================================================================================"
echo

# Set environment variables
export AZAZAEL_ROOT="/mnt/c/Users/clint/Desktop/Azazael"
export PERSEPHONE_ROOT="/mnt/c/Users/clint/Desktop/PERSEPHONE"
export PYTHONPATH="${AZAZAEL_ROOT}/ml_platforms:${PERSEPHONE_ROOT}/lib:${PYTHONPATH}"
export PATH="${AZAZAEL_ROOT}/scripts:${PERSEPHONE_ROOT}/bin:${PATH}"

log_ok "Environment variables set"
echo
echo "AZAZAEL_ROOT=$AZAZAEL_ROOT"
echo "PERSEPHONE_ROOT=$PERSEPHONE_ROOT"
echo "PYTHONPATH=$PYTHONPATH"

echo
echo "================================================================================"
echo "Linux Commands Reference"
echo "================================================================================"
echo

echo -e "${CYAN}PROCESS MANAGEMENT:${NC}"
echo "  ps aux                  - All processes with details"
echo "  ps aux | grep python    - Python processes"
echo "  top                     - Process monitor"
echo "  htop                    - Enhanced process monitor"
echo "  kill -9 <PID>          - Force kill process"
echo "  pkill -f pattern        - Kill by pattern"
echo "  pgrep -f pattern        - Find PID by pattern"
echo "  lsof -i :8080          - Find process using port"
echo "  netstat -tulpn          - Listening ports"
echo "  ss -tulpn               - Socket statistics"
echo "  strace -p <PID>         - Trace system calls"
echo "  ltrace -p <PID>         - Trace library calls"

echo
echo -e "${CYAN}SYSTEM INFORMATION:${NC}"
echo "  uname -a                - Full system info"
echo "  hostnamectl            - Host information"
echo "  neofetch               - System summary with ASCII art"
echo "  hwinfo                  - Hardware info"
echo "  lscpu                  - CPU info"
echo "  lsmem                  - Memory info"
echo "  lsblk                  - Block devices"
echo "  df -h                   - Disk usage"
echo "  du -sh <dir>           - Directory size"
echo "  free -h                 - Memory and swap"
echo "  cat /proc/cpuinfo      - CPU details"
echo "  cat /proc/meminfo      - Memory details"
echo "  uptime                  - System uptime"

echo
echo -e "${CYAN}USER MANAGEMENT:${NC}"
echo "  whoami                  - Current user"
echo "  id                      - User ID and groups"
echo "  users                   - Logged in users"
echo "  last                    - Login history"
echo "  w                       - Who is logged in"
echo "  adduser username        - Add user"
echo "  deluser username        - Delete user"
echo "  usermod -aG sudo user  - Add to sudo group"

echo
echo -e "${CYAN}NETWORKING:${NC}"
echo "  ip addr                 - IP addresses"
echo "  ip link                 - Network interfaces"
echo "  ip route                - Routing table"
echo "  ping -c 4 google.com   - Ping test"
echo "  traceroute google.com   - Trace route"
echo "  mtr google.com          - Traceroute with stats"
echo "  dig google.com          - DNS lookup"
echo "  nslookup google.com     - DNS lookup"
echo "  curl -I google.com      - HTTP headers"
echo "  wget https://url        - Download file"
echo "  ssh user@host          - SSH connection"
echo "  scp file user@host:/path - Secure copy"
echo "  rsync -avz src/ dst/   - Sync files"

echo
echo -e "${CYAN}FILE MANAGEMENT:${NC}"
echo "  ls -lah                - List all files detailed"
echo "  find / -name file      - Find file by name"
echo "  find / -type f -exec grep -l 'text' {} \; - Find files containing text"
echo "  locate file            - Quick file search (updatedb first)"
echo "  du -sh */ | sort -hr   - Directory sizes sorted"
echo "  tar -czvf file.tar.gz dir/ - Compress"
echo "  tar -xzvf file.tar.gz    - Decompress"
echo "  zip -r file.zip dir/     - Zip compression"
echo "  unzip file.zip           - Unzip"
echo "  rsync -avz --delete src/ dst/ - Mirror directories"

echo
echo -e "${CYAN}PERMISSIONS:${NC}"
echo "  chmod +x file           - Make executable"
echo "  chown user:group file - Change owner"
echo "  chgrp group file       - Change group"
echo "  umask                  - Default permissions"
echo "  setfacl -m u:user:rw file - ACL permissions"
echo "  getfacl file           - View ACL"

echo
echo -e "${CYAN}SERVICES (systemd):${NC}"
echo "  systemctl status       - Service status"
echo "  systemctl start <svc>  - Start service"
echo "  systemctl stop <svc>   - Stop service"
echo "  systemctl restart <svc> - Restart service"
echo "  systemctl enable <svc> - Enable on boot"
echo "  systemctl disable <svc> - Disable on boot"
echo "  journalctl -u <svc>    - Service logs"
echo "  systemctl list-units    - List all units"
echo "  systemctl daemon-reload - Reload configs"

echo
echo -e "${CYAN}PACKAGE MANAGEMENT:${NC}"
echo "  apt update && apt upgrade - Update system"
echo "  apt install package    - Install package"
echo "  apt remove package    - Remove package"
echo "  apt search package    - Search package"
echo "  apt list --installed  - List installed"
echo "  dpkg -l | grep python - Python packages"
echo "  pip install package   - Python package"
echo "  pip list               - List pip packages"
echo "  pip freeze > requirements.txt - Save deps"
echo "  pip install -r requirements.txt - Install deps"

echo
echo -e "${CYAN}CONTAINERS & VIRTUALIZATION:${NC}"
echo "  docker ps              - List containers"
echo "  docker images          - List images"
echo "  docker run -it image   - Run container"
echo "  docker exec -it <id> bash - Shell in container"
echo "  docker-compose up -d   - Start compose"
echo "  docker-compose down    - Stop compose"
echo "  virt-manager           - Virtual machine manager"
echo "  virsh list             - List VMs"

echo
echo -e "${CYAN}DEVELOPMENT:${NC}"
echo "  git status             - Repo status"
echo "  git log --oneline      - Commit history"
echo "  git branch -a          - List branches"
echo "  git diff HEAD          - Changes"
echo "  git stash              - Stash changes"
echo "  make                   - Build with Makefile"
echo "  cmake ..               - Configure build"
echo "  ninja                  - Fast build system"
echo "  ninja install          - Install built files"

echo
echo -e "${CYAN}MONITORING:${NC}"
echo "  atop                   - Advanced system monitor"
echo "  nmon                   - Nigel Monitor"
echo "  iostat 1               - I/O statistics"
echo "  vmstat 1               - Virtual memory stats"
echo "  sar -u 1              - CPU usage"
echo "  sar -r 1              - Memory usage"
echo "  sar -d 1              - Disk usage"
echo "  nethogs               - Per-process network"

echo
echo -e "${CYAN}SCRIPTING:${NC}"
echo "  bash script.sh         - Run bash script"
echo "  python3 script.py      - Run Python script"
echo "  node script.js         - Run Node script"
echo "  ./script.sh            - Run executable script"
echo "  source script.sh       - Export variables"
echo "  env                    - Print all env vars"
echo "  export VAR=value       - Set env var"

echo
echo "================================================================================"
echo "Custom Aliases for Azazael/PERSEPHONE"
echo "================================================================================"
echo

# Create aliases file
ALIASES_FILE="${HOME}/.bash_aliases_azazael"

echo "# Azazael/PERSEPHONE Aliases" > "$ALIASES_FILE"
echo "# Generated by WSL Admin Activation Script" >> "$ALIASES_FILE"
echo "" >> "$ALIASES_FILE"

echo "# Azazael shortcuts" >> "$ALIASES_FILE"
echo "alias azazael='python \$AZAZAEL_ROOT/scripts/azazael_cli.py'" >> "$ALIASES_FILE"
echo "alias azstatus='python \$AZAZAEL_ROOT/scripts/azazael_cli.py status'" >> "$ALIASES_FILE"
echo "alias aztest='python \$AZAZAEL_ROOT/scripts/run_tests.py'" >> "$ALIASES_FILE"
echo "alias azvalidate='python \$AZAZAEL_ROOT/scripts/validate_docs.py .'" >> "$ALIASES_FILE"
echo "" >> "$ALIASES_FILE"

echo "# PERSEPHONE shortcuts" >> "$ALIASES_FILE"
echo "alias persephone='python \$PERSEPHONE_ROOT/bin/persephone.py'" >> "$ALIASES_FILE"
echo "alias sync-az='bash \$PERSEPHONE_ROOT/sync_azazael_wsl.sh'" >> "$ALIASES_FILE"
echo "alias sync-to-pers='bash \$PERSEPHONE_ROOT/sync_azazael_wsl.sh to-pers'" >> "$ALIASES_FILE"
echo "alias sync-from-pers='bash \$PERSEPHONE_ROOT/sync_azazael_wsl.sh from-pers'" >> "$ALIASES_FILE"
echo "" >> "$ALIASES_FILE"

echo "# System shortcuts" >> "$ALIASES_FILE"
echo "alias ..='cd ..'" >> "$ALIASES_FILE"
echo "alias ...='cd ../..'" >> "$ALIASES_FILE"
echo "alias l='ls -lah'" >> "$ALIASES_FILE"
echo "alias ll='ls -lh'" >> "$ALIASES_FILE"
echo "alias la='ls -A'" >> "$ALIASES_FILE"
echo "alias grep='grep --color=auto'" >> "$ALIASES_FILE"
echo "alias fgrep='fgrep --color=auto'" >> "$ALIASES_FILE"
echo "alias egrep='egrep --color=auto'" >> "$ALIASES_FILE"
echo "alias df='df -h'" >> "$ALIASES_FILE"
echo "alias free='free -h'" >> "$ALIASES_FILE"
echo "alias top='htop || top'" >> "$ALIASES_FILE"
echo "" >> "$ALIASES_FILE"

echo "# Python shortcuts" >> "$ALIASES_FILE"
echo "alias py='python3'" >> "$ALIASES_FILE"
echo "alias pip='pip3'" >> "$ALIASES_FILE"
echo "alias python='python3'" >> "$ALIASES_FILE"
echo "alias venv='python3 -m venv venv && source venv/bin/activate'" >> "$ALIASES_FILE"
echo "alias req='pip freeze > requirements.txt'" >> "$ALIASES_FILE"
echo "" >> "$ALIASES_FILE"

echo "# Git shortcuts" >> "$ALIASES_FILE"
echo "alias g='git'" >> "$ALIASES_FILE"
echo "alias gs='git status'" >> "$ALIASES_FILE"
echo "alias ga='git add'" >> "$ALIASES_FILE"
echo "alias gc='git commit'" >> "$ALIASES_FILE"
echo "alias gp='git push'" >> "$ALIASES_FILE"
echo "alias gpl='git pull'" >> "$ALIASES_FILE"
echo "alias gl='git log --oneline'" >> "$ALIASES_FILE"
echo "alias gd='git diff'" >> "$ALIASES_FILE"
echo "" >> "$ALIASES_FILE"

# Check if .bash_aliases is sourced in .bashrc
if ! grep -q ".bash_aliases_azazael" "${HOME}/.bashrc" 2>/dev/null; then
    echo "" >> "${HOME}/.bashrc"
    echo "# Source Azazael/PERSEPHONE aliases" >> "${HOME}/.bashrc"
    echo "if [ -f ~/.bash_aliases_azazael ]; then" >> "${HOME}/.bashrc"
    echo "    . ~/.bash_aliases_azazael" >> "${HOME}/.bashrc"
    echo "fi" >> "${HOME}/.bashrc"
    log_ok "Added aliases sourcing to .bashrc"
else
    log_info "Aliases already sourced in .bashrc"
fi

log_ok "Aliases file created: $ALIASES_FILE"

echo
echo "================================================================================"
echo "Creating Functions"
echo "================================================================================"
echo

# Create functions file
FUNCTIONS_FILE="${HOME}/.bash_functions_azazael"

echo "# Azazael/PERSEPHONE Functions" > "$FUNCTIONS_FILE"
echo "# Generated by WSL Admin Activation Script" >> "$FUNCTIONS_FILE"
echo "" >> "$FUNCTIONS_FILE"

cat >> "$FUNCTIONS_FILE" << 'FUNCTIONS'
# Find Python process
find-python() {
    ps aux | grep python | grep -v grep
}

# Kill all Python processes
kill-python() {
    pkill -f python || true
    log_ok "All Python processes killed"
}

# Restart network
restart-network() {
    sudo systemctl restart NetworkManager || sudo service networking restart
    log_ok "Network restarted"
}

# Show disk usage of mounted Windows directories
win-disk-usage() {
    for dir in /mnt/c /mnt/d /mnt/e /mnt/f; do
        if [ -d "$dir" ]; then
            echo "$dir: $(du -sh "$dir" 2>/dev/null | cut -f1)"
        fi
    done
}

# Sync Azazael to PERSEPHONE
sync-az-to-pers() {
    rsync -avz --progress \
        --exclude='*.pyc' \
        --exclude='__pycache__' \
        --exclude='*.log' \
        "$AZAZAEL_ROOT/" "$PERSEPHONE_ROOT/"
    log_ok "Sync complete"
}

# Sync PERSEPHONE to Azazael
sync-pers-to-az() {
    rsync -avz --progress \
        --exclude='*.pyc' \
        --exclude='__pycache__' \
        --exclude='*.log' \
        "$PERSEPHONE_ROOT/" "$AZAZAEL_ROOT/"
    log_ok "Sync complete"
}

# Quick test run
az-test() {
    cd "$AZAZAEL_ROOT"
    python scripts/run_tests.py
}

# Quick validate
az-validate() {
    cd "$AZAZAEL_ROOT"
    python scripts/validate_docs.py .
}

# Show environment
show-env() {
    echo "AZAZAEL_ROOT=$AZAZAEL_ROOT"
    echo "PERSEPHONE_ROOT=$PERSEPHONE_ROOT"
    echo "PYTHONPATH=$PYTHONPATH"
    echo "PATH (first 5):"
    echo "$PATH" | tr ':' '\n' | head -5
}

# Count files
count-files() {
    echo "Python files: $(find . -name '*.py' | wc -l)"
    echo "Markdown files: $(find . -name '*.md' | wc -l)"
    echo "Total files: $(find . -type f | wc -l)"
}

# WSL disk usage
wsl-disk() {
    echo "WSL disk usage:"
    du -sh ~/.local/share/containers 2>/dev/null || true
    du -sh /var/cache/apt 2>/dev/null || true
    du -sh /var/lib/docker 2>/dev/null || true
}

# Update all pip packages
pip-update-all() {
    pip list --outdated --format=freeze | grep -v '^\-e' | cut -d'=' -f1 | xargs -n1 pip install -U
}

# Memory info
mem-info() {
    echo "Memory:"
    free -h
    echo
    echo "Top processes:"
    ps aux --sort=-%mem | head -6
}

# CPU info
cpu-info() {
    echo "CPU:"
    lscpu | grep -E "Model name|CPU\(s\)|Thread|Core|Socket"
    echo
    echo "Top CPU processes:"
    ps aux --sort=-%cpu | head -6
}
FUNCTIONS

log_ok "Functions file created: $FUNCTIONS_FILE"

# Source functions in .bashrc
if ! grep -q ".bash_functions_azazael" "${HOME}/.bashrc" 2>/dev/null; then
    echo "" >> "${HOME}/.bashrc"
    echo "# Source Azazael/PERSEPHONE functions" >> "${HOME}/.bashrc"
    echo "if [ -f ~/.bash_functions_azazael ]; then" >> "${HOME}/.bashrc"
    echo "    . ~/.bash_functions_azazael" >> "${HOME}/.bashrc"
    echo "fi" >> "${HOME}/.bashrc"
    log_ok "Added functions sourcing to .bashrc"
fi

echo
echo "================================================================================"
echo "Installing Useful Packages"
echo "================================================================================"
echo

install_package() {
    if command -v $1 &> /dev/null; then
        log_info "$1 already installed"
    else
        log_info "Installing $1..."
        sudo apt install -y $1 2>/dev/null || log_warn "Could not install $1"
    fi
}

if [ $ROOT_MODE -eq 1 ]; then
    install_package htop
    install_package neofetch
    install_package rsync
    install_package curl
    install_package wget
    install_package git
    install_package vim
    install_package tree
    install_package jq
    install_package bat
    install_package ripgrep
    install_package fd-find
    install_package fzf
    install_package tar
    install_package zip
    install_package unzip
    install_package p7zip-full
    install_package net-tools
    install_package dnsutils
    install_package traceroute
    install_package mtr
    install_package nmap
    install_package tcpdump
    install_package strace
    install_package ltrace
    install_package lsof
    install_package sysstat
    install_package atop
    install_package iotop
    install_package iftop
    install_package nethogs
else
    log_warn "Not running as root - skipping package installation"
    log_info "Run with sudo to install packages"
fi

echo
echo "================================================================================"
echo "Environment Setup Script"
echo "================================================================================"
echo

# Create setup script in /etc/profile.d
SETUP_SCRIPT="/etc/profile.d/azazael-persephone.sh"

if [ $ROOT_MODE -eq 1 ]; then
    cat > "$SETUP_SCRIPT" << 'PROFILE_SCRIPT'
# Azazael/PERSEPHONE Environment Setup
# Generated by WSL Admin Activation Script

export AZAZAEL_ROOT="/mnt/c/Users/clint/Desktop/Azazael"
export PERSEPHONE_ROOT="/mnt/c/Users/clint/Desktop/PERSEPHONE"
export PYTHONPATH="${AZAZAEL_ROOT}/ml_platforms:${PERSEPHONE_ROOT}/lib:${PYTHONPATH}"
export PATH="${AZAZAEL_ROOT}/scripts:${PERSEPHONE_ROOT}/bin:${PATH}"

# Source user aliases
if [ -f ~/.bash_aliases_azazael ]; then
    . ~/.bash_aliases_azazael
fi

# Source user functions
if [ -f ~/.bash_functions_azazael ]; then
    . ~/.bash_functions_azazael
fi
PROFILE_SCRIPT

    chmod +x "$SETUP_SCRIPT"
    log_ok "System-wide setup script created: $SETUP_SCRIPT"
else
    log_warn "Not running as root - user-level setup only"
fi

echo
echo "================================================================================"
echo "Summary"
echo "================================================================================"
echo

log_ok "WSL Admin Activation Complete!"
echo
echo "Quick Commands:"
echo "  azazael         - Run Azazael CLI"
echo "  az-test         - Run tests"
echo "  az-validate     - Validate docs"
echo "  sync-az-to-pers - Sync Azazael -> PERSEPHONE"
echo "  sync-to-pers    - Same as above"
echo "  sync-from-pers  - Sync PERSEPHONE -> Azazael"
echo "  kill-python     - Kill all Python processes"
echo "  mem-info        - Memory information"
echo "  cpu-info        - CPU information"
echo "  win-disk-usage  - Windows disk usage"
echo "  show-env        - Show environment variables"
echo "  count-files     - Count files in project"
echo
echo "Files Created:"
echo "  $ALIASES_FILE"
echo "  $FUNCTIONS_FILE"
if [ $ROOT_MODE -eq 1 ]; then
    echo "  $SETUP_SCRIPT"
fi
echo
echo "To activate in current shell:"
echo "  source $ALIASES_FILE"
echo "  source $FUNCTIONS_FILE"
echo
echo "To activate in new shells, add to ~/.bashrc:"
echo "  source $ALIASES_FILE"
echo "  source $FUNCTIONS_FILE"
echo
echo "================================================================================"
echo "All commands are now available with full scripting capabilities!"
echo "================================================================================"
