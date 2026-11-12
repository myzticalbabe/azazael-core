#!/bin/bash
# verify_gaps.sh — location shim (C-P2).
# Canonical gate lives at azazael/.agents/skills/verify_gaps.sh;
# this shim exists so anyone looking in scripts/ finds the gate.
# Usage: bash azazael/scripts/verify_gaps.sh
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$HERE/../.agents/skills/verify_gaps.sh" "$@"
