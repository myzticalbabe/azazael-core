#!/bin/bash
# ============================================================================
# AZAZAEL ECOSYSTEM - KLINK Unified Command Interface
# Location: ~/KLINK/azazael.sh
# Supports: Linux/Debian/macOS/WSL
# ============================================================================

# Configuration
export AZAZAEL_ROOT="${AZAZAEL_ROOT:-$HOME/Desktop/Azazael}"
export KLINK_ROOT="$(dirname "$(readlink -f "$0")")"

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# ============================================================================
# CORE COMMANDS
# ============================================================================

show_help() {
    cat << EOF
${BLUE}AZAZAEL ECOSYSTEM - KLINK Command Interface${NC}

${YELLOW}Usage:${NC}  azazael <command> [options]

${YELLOW}Core Commands:${NC}
  status              Show ecosystem status
  version             Show version info
  list                List all available commands

${YELLOW}WORMZ Commands:${NC}
  systemz             System information worm
  processz            Process management worm
  diskz               Disk analysis worm
  networkz            Network analysis worm
  clockz              Time synchronization worm
  worms               Run all worms

${YELLOW}METAWORMS Commands:${NC}
  errorz              Error tracking metaworm
  trimz                Optimization metaworm
  healthz              Health monitoring metaworm
  metaz                Metadata metaworm
  optimiz              Optimization metaworm
  hydrometa            HYDRA metaworm

${YELLOW}CLUSTER Commands:${NC}
  cluster-system       System cluster
  cluster-network      Network cluster
  cluster-health       Health cluster

${YELLOW}HYDRA Commands:${NC}
  hydra-run            Run full HYDRA cycle
  hydra-discovery      Discovery stage
  hydra-analysis       Analysis stage
  hydra-optimize       Optimization stage
  hydra-validate       Validation stage
  hydra-deploy        Deployment stage
  hydra-feedback      Feedback stage

${YELLOW}ECOSYSTEM Commands:${NC}
  terence              TERENCE error resolver
  horus                HORUS output formatter
  tarbaby              Binary builder
  poindexter           Mathematical indexer
  persephone           Cross-platform utilities
  bibl                 Terminology crawler

${YELLOW}OPTIMIZATION Commands:${NC}
  optimize-all         Run full ecosystem optimization
  analyze-batch        Analyze batch files
  run-all              Run all ecosystem components

${YELLOW}SHORTY Commands:${NC}
  shortcuts            List SHORTY shortcuts
  run-shortcut <name>  Run SHORTY shortcut

${YELLOW}Options:${NC}
  --help, -h           Show this help
  --verbose, -v        Verbose output
  --json               Output in JSON format

EOF
    exit 0
}

# ============================================================================
# WORMZ EXECUTION
# ============================================================================

run_worm() {
    local worm="$1"
    local worm_path="$AZAZAEL_ROOT/WORMZ/${worm^}.py"
    
    if [[ ! -f "$worm_path" ]]; then
        log_error "Worm not found: $worm"
        return 1
    fi
    
    log_info "Running $worm..."
    python3 "$worm_path" --status
}

run_all_worms() {
    log_info "Running all WORMZ worms..."
    for worm in systemz processz diskz networkz clockz; do
        run_worm "$worm"
    done
}

# ============================================================================
# METAWORMS EXECUTION
# ============================================================================

run_metaworm() {
    local metaworm="$1"
    local meta_path="$AZAZAEL_ROOT/METAWORMS/${metaworm^}.py"
    
    if [[ ! -f "$meta_path" ]]; then
        log_error "Metaworm not found: $metaworm"
        return 1
    fi
    
    log_info "Running $metaworm..."
    python3 "$meta_path" --all
}

# ============================================================================
# HYDRA EXECUTION
# ============================================================================

run_hydra() {
    local stage="${1:-all}"
    
    if [[ "$stage" == "all" ]]; then
        log_info "Running full HYDRA cycle..."
        python3 -c "
import sys
sys.path.insert(0, '$AZAZAEL_ROOT')
from HYDRA import HYDRA
h = HYDRA()
h.run_all()
"
    else
        log_info "Running HYDRA stage: $stage"
        python3 -c "
import sys
sys.path.insert(0, '$AZAZAEL_ROOT')
from HYDRA import HYDRA
h = HYDRA()
h.run_stage('$stage')
"
    fi
}

# ============================================================================
# ECOSYSTEM COMMANDS
# ============================================================================

run_terence() {
    log_info "Running TERENCE error resolver..."
    python3 "$AZAZAEL_ROOT/TERENCE/TERENCE.py" --scan
}

run_horus() {
    log_info "Running HORUS output formatter..."
    python3 "$AZAZAEL_ROOT/HORUS/HORUS.py"
}

run_tarbaby() {
    log_info "Running TARBABY binary builder..."
    python3 "$AZAZAEL_ROOT/TARBABY/TARBABY.py" --test
}

run_poindexter() {
    log_info "Running POINDEXTER mathematical indexer..."
    python3 "$AZAZAEL_ROOT/POINDEXTER/POINDEXTER.py"
}

run_persephone() {
    log_info "Running PERSEPHONE cross-platform utilities..."
    python3 "$AZAZAEL_ROOT/PERSEPHONE/persephone.py"
}

run_bibl() {
    log_info "Running BIBL terminology crawler..."
    python3 "$AZAZAEL_ROOT/BIBL/bibl.py" 2>/dev/null || log_warn "BIBL not found"
}

# ============================================================================
# OPTIMIZATION COMMANDS
# ============================================================================

run_optimizer() {
    log_info "Running ecosystem optimizer..."
    python3 "$AZAZAEL_ROOT/ECOSYSTEM_OPTIMIZER.py" -o
}

run_batch_analyzer() {
    log_info "Running batch file analyzer..."
    python3 "$AZAZAEL_ROOT/BAT_OPTIMIZATION_ANALYSIS.py"
}

run_full_ecosystem() {
    log_info "Running full ecosystem..."
    python3 "$AZAZAEL_ROOT/AZAZAEL_INTEGRATION.py"
}

# ============================================================================
# SHORTY COMMANDS
# ============================================================================

list_shortcuts() {
    echo "${YELLOW}Available SHORTY shortcuts:${NC}"
    ls -1 "$AZAZAEL_ROOT/SHORTY/"*.bat 2>/dev/null | xargs -I {} basename {} .bat | sed 's/^/  /'
}

run_shortcut() {
    local name="$1"
    local shortcut="$AZAZAEL_ROOT/SHORTY/${name}.bat"
    
    if [[ ! -f "$shortcut" ]]; then
        log_error "Shortcut not found: $name"
        list_shortcuts
        return 1
    fi
    
    log_info "Running SHORTY shortcut: $name"
    bash "$shortcut"
}

# ============================================================================
# MAIN DISPATCHER
# ============================================================================

main() {
    local cmd="${1:-}"
    shift || true
    
    case "$cmd" in
        ""|help|--help|-h)
            show_help
            ;;
        status)
            echo "${GREEN}Azazael Ecosystem Status${NC}"
            echo "Root: $AZAZAEL_ROOT"
            echo "Klink: $KLINK_ROOT"
            ;;
        version)
            echo "Azazael Ecosystem v4.0.0"
            ;;
        list)
            echo "Available commands:"
            compgen -A function | grep -v '^_' | sed 's/^/  /'
            ;;
        systemz|processz|diskz|networkz|clockz)
            run_worm "$cmd"
            ;;
        worms)
            run_all_worms
            ;;
        errorz|trimz|healthz|metaz|optimiz|hydrometa)
            run_metaworm "$cmd"
            ;;
        cluster-system|cluster-network|cluster-health)
            local cluster="${cmd#cluster-}"
            python3 "$AZAZAEL_ROOT/WORMZ/CLUSTERWORMS.py" --run "$cluster"
            ;;
        hydra-run|hydra-discovery|hydra-analysis|hydra-optimize|hydra-validate|hydra-deploy|hydra-feedback)
            local stage="${cmd#hydra-}"
            run_hydra "$stage"
            ;;
        terence|horus|tarbaby|poindexter|persephone)
            run_"$cmd"
            ;;
        bibl)
            run_bibl
            ;;
        optimize-all)
            run_optimizer
            ;;
        analyze-batch)
            run_batch_analyzer
            ;;
        run-all)
            run_full_ecosystem
            ;;
        shortcuts)
            list_shortcuts
            ;;
        run-shortcut)
            run_shortcut "$1"
            ;;
        --verbose|-v)
            export VERBOSE=1
            "${@:2}"
            ;;
        --json)
            python3 -c "
import sys
sys.path.insert(0, '$AZAZAEL_ROOT')
from AZAZAEL_INTEGRATION import AzazaelEcosystem
e = AzazaelEcosystem()
e.discover_components()
import json
print(json.dumps(e.get_status(), indent=2))
"
            ;;
        *)
            log_error "Unknown command: $cmd"
            show_help
            ;;
    esac
}

main "$@"
