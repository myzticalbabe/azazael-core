#!/usr/bin/env bash
# HYDRA Bash API Client — Control HYDRA via curl
# Usage:  ./hydra_api_client.sh [command]
# Commands: status, discover, run, adapt, nodes, health

API="${API_URL:-http://localhost:8000}"

case "${1:-status}" in
    status|st)
        curl -s "$API/status" | python3 -m json.tool
        ;;
    discover|disc)
        curl -s -X POST "$API/discover" | python3 -m json.tool
        ;;
    run)
        curl -s -X POST "$API/run" | python3 -m json.tool
        ;;
    adapt)
        curl -s -X POST "$API/adapt" | python3 -m json.tool
        ;;
    nodes|node)
        curl -s "$API/nodes" | python3 -m json.tool
        ;;
    health|he)
        curl -s "$API/" | python3 -m json.tool
        ;;
    watch|w)
        watch -n 5 "curl -s $API/status | python3 -m json.tool"
        ;;
    *)
        echo "HYDRA API Client"
        echo "Usage: $0 {status|discover|run|adapt|nodes|health|watch}"
        echo ""
        echo "Examples:"
        echo "  $0 status          # Cluster status"
        echo "  $0 discover        # Discover ML groups"
        echo "  $0 run             # Run pipeline"
        echo "  $0 watch           # Live-updating status"
        exit 1
        ;;
esac
