#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# AIOps Auto-Remediation — Setup & Deploy Script
#
# Usage:
#   ./setup.sh              — full install
#   ./setup.sh --dry-run    — preview without making changes
#
# Prerequisites:
#   1. config.json must exist (copy from config.example.json and fill in values)
#   2. python3 generate_configs.py must have been run (creates .env and YAML configs)
#   3. pip install -r requirements.txt in the project directory
# ==============================================================================

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true && echo "=== DRY-RUN MODE: no changes will be applied ==="

run() {
    if [ "$DRY_RUN" = true ]; then echo "[dry-run] $*"; else "$@"; fi
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="/opt/aops"
SERVICE_FILE="$SCRIPT_DIR/auto-remediation-webhook.service"

# ── 0. Pre-flight checks ──────────────────────────────────────────────────────
echo "--- Pre-flight checks ---"

if [ ! -f "$SCRIPT_DIR/config.json" ]; then
    echo "ERROR: config.json not found."
    echo "       Copy config.example.json → config.json and fill in your values, then re-run."
    exit 1
fi

if [ ! -f "$SCRIPT_DIR/.env" ] || [ ! -f "$SCRIPT_DIR/prometheus/prometheus.yml" ]; then
    echo "ERROR: Generated configs missing. Run:  python3 generate_configs.py"
    exit 1
fi

python3 "$SCRIPT_DIR/generate_configs.py" --check
echo "Config validation: OK"

# ── 1. Create deploy directory ────────────────────────────────────────────────
echo "--- Installing files to $DEPLOY_DIR ---"
run mkdir -p "$DEPLOY_DIR/remediate"

# ── 2. Copy application files ─────────────────────────────────────────────────
run cp "$SCRIPT_DIR/webhook.py"       "$DEPLOY_DIR/"
run cp "$SCRIPT_DIR/config_loader.py" "$DEPLOY_DIR/"
run cp "$SCRIPT_DIR/config.json"      "$DEPLOY_DIR/"
run chmod 600 "$DEPLOY_DIR/config.json"   # credentials — owner-only

for script in fpm-reload nginx-file-limit redis-flush-cache generic_triage; do
    run cp "$SCRIPT_DIR/remediate/${script}.sh"    "$DEPLOY_DIR/remediate/"
    run chmod 750 "$DEPLOY_DIR/remediate/${script}.sh"
done
run cp "$SCRIPT_DIR/remediate/ssh_helper.py" "$DEPLOY_DIR/remediate/"
run chmod 640 "$DEPLOY_DIR/remediate/ssh_helper.py"

# ── 3. Install Python dependencies ────────────────────────────────────────────
echo "--- Installing Python dependencies ---"
run pip3 install -r "$SCRIPT_DIR/requirements.txt" --quiet

# ── 4. Install systemd service ────────────────────────────────────────────────
echo "--- Installing systemd service ---"
# Patch the WorkingDirectory to the actual deploy path
if [ "$DRY_RUN" = false ]; then
    sed "s|WorkingDirectory=.*|WorkingDirectory=$DEPLOY_DIR|g" \
        "$SERVICE_FILE" > /etc/systemd/system/auto-remediation-webhook.service
else
    echo "[dry-run] Would write /etc/systemd/system/auto-remediation-webhook.service"
fi

run systemctl daemon-reload
run systemctl enable auto-remediation-webhook
run systemctl restart auto-remediation-webhook

# ── 5. Create log files with correct permissions ──────────────────────────────
echo "--- Setting up log files ---"
for logfile in /var/log/auto-remediation.log /var/log/auto-remediation-access.log /var/log/auto-remediation-error.log; do
    run touch "$logfile"
    run chmod 0640 "$logfile"
done

# ── 6. Install logrotate ──────────────────────────────────────────────────────
echo "--- Installing logrotate config ---"
run cp "$SCRIPT_DIR/webhook/auto-remediation" /etc/logrotate.d/

# ── 7. Reload Docker monitoring services ─────────────────────────────────────
echo "--- Reloading monitoring services ---"
for svc in alertmanager prometheus loki; do
    if [ "$DRY_RUN" = true ]; then
        echo "[dry-run] docker kill -s HUP $svc"
    else
        if docker ps --format '{{.Names}}' | grep -q "^${svc}$"; then
            docker kill -s HUP "$svc" >/dev/null 2>&1 && echo "Reloaded $svc" || echo "Warning: could not reload $svc"
        else
            echo "Skipping $svc — container not running"
        fi
    fi
done

# ── 8. Health check ────────────────────────────────────────────────────────────
WEBHOOK_PORT=$(python3 -c "import json; print(json.load(open('$SCRIPT_DIR/config.json'))['ports']['webhook'])")
echo "--- Health check ---"
if [ "$DRY_RUN" = false ]; then
    sleep 3
    curl -sf "http://127.0.0.1:${WEBHOOK_PORT}/health" && echo " (webhook healthy)" || echo "Warning: webhook not responding yet — check: systemctl status auto-remediation-webhook"
else
    echo "[dry-run] Would check: curl http://127.0.0.1:${WEBHOOK_PORT}/health"
fi

echo ""
echo "=== Setup complete ==="
