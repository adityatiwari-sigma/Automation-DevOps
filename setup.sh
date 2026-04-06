#!/bin/bash

# ==============================================================================
# Auto-Remediation System Setup Script
# ==============================================================================

set -e

DRY_RUN=false
if [[ "$1" == "--dry-run" ]]; then
    DRY_RUN=true
    echo "################################################################"
    echo "# RUNNING IN DRY-RUN MODE: No changes will be applied          #"
    echo "################################################################"
fi

# Helper to execute or just print commands
run_cmd() {
    if [ "$DRY_RUN" = true ]; then
        echo "[DRY-RUN] Executing: $@"
    else
        # If it's a bash -c command, we need to handle it specially
        if [[ "$1" == "bash" && "$2" == "-c" ]]; then
            shift 2
            eval "$@"
        else
            "$@"
        fi
    fi
}

echo "--- Initializing Setup ---"

# 1. Create directories
run_cmd mkdir -p /opt/monitoring/remediate

# 2. Install remediation scripts
echo "Installing remediation scripts to /opt/monitoring/remediate/..."
run_cmd cp remediate/fpm-reload.sh /opt/monitoring/remediate/
run_cmd cp remediate/redis-flush-cache.sh /opt/monitoring/remediate/
run_cmd cp remediate/nginx-file-limit.sh /opt/monitoring/remediate/
run_cmd chmod 750 /opt/monitoring/remediate/fpm-reload.sh
run_cmd chmod 750 /opt/monitoring/remediate/redis-flush-cache.sh
run_cmd chmod 750 /opt/monitoring/remediate/nginx-file-limit.sh

# 3. Install webhook.py
echo "Installing webhook.py to /opt/monitoring/..."
run_cmd cp webhook.py /opt/monitoring/
run_cmd chmod 644 /opt/monitoring/webhook.py

# 4. Install and enable the systemd service
echo "Setting up systemd service..."
run_cmd cp webhook/auto-remediation-webhook.service /etc/systemd/system/
if [ ! -f /etc/default/auto-remediation-webhook ]; then
    run_cmd bash -c "echo 'AUTO_REMEDIATE=true' > /etc/default/auto-remediation-webhook"
    run_cmd bash -c "echo 'WEBHOOK_PORT=5001' >> /etc/default/auto-remediation-webhook"
    run_cmd bash -c "echo 'REMEDIATION_DIR=/opt/monitoring/remediate' >> /etc/default/auto-remediation-webhook"
    run_cmd bash -c "echo 'SLACK_WEBHOOK_URL=' >> /etc/default/auto-remediation-webhook"
fi
run_cmd systemctl daemon-reload
run_cmd systemctl enable auto-remediation-webhook
run_cmd systemctl restart auto-remediation-webhook

# 5. Create log file with correct permissions
echo "Initializing log file..."
run_cmd touch /var/log/auto-remediation.log
run_cmd chown root:adm /var/log/auto-remediation.log
run_cmd chmod 0640 /var/log/auto-remediation.log

# 6. Install logrotate config
echo "Installing logrotate configuration..."
run_cmd cp webhook/auto-remediation /etc/logrotate.d/

# 7. Provision SSH key for root (webhook run by root needs access to remote targets)
echo "Provisioning SSH key for remote access..."
USER_ID_RSA="/home/adityatiwari/.ssh/id_ed25519"
if [ -f "$USER_ID_RSA" ]; then
    run_cmd mkdir -p /root/.ssh
    run_cmd cp "$USER_ID_RSA" /root/.ssh/id_ed25519
    run_cmd chmod 600 /root/.ssh/id_ed25519
    echo "SSH key provisioned for root"
else
    echo "Warning: SSH key not found at $USER_ID_RSA - remote remediation may fail"
fi

# 8. Back up then replaces alertmanager.yml and both rules files
echo "Updating configuration files..."
FILES=(
    "alertmanager/alertmanager.yml"
    "prometheus/rules/app-alerts.yaml"
    "loki/rules/fake/loki-alerts.yaml"
)

for file in "${FILES[@]}"; do
    if [ -f "$file" ]; then
        run_cmd cp "$file" "$file.bak"
        echo "Backed up $file to $file.bak"
    else
        echo "Warning: $file not found, skipping backup."
    fi
done

# 8. Reload Prometheus, Alertmanager, and Loki
echo "Reloading monitoring services (Docker)..."
for svc in alertmanager prometheus loki; do
    if [ "$DRY_RUN" = true ]; then
        echo "[DRY-RUN] docker kill -s HUP $svc"
    else
        # Check if container is running before sending HUP
        if docker ps --format '{{.Names}}' | grep -q "^$svc$"; then
            if docker kill -s HUP "$svc" >/dev/null 2>&1; then
                echo "Successfully reloaded $svc"
            else
                echo "Failed to reload $svc (container may be starting up)"
            fi
        else
            echo "Skipping reload for $svc: Container is not running"
        fi
    fi
done

# 9. Final health check
echo "--- Setup Complete ---"
if [ "$DRY_RUN" = false ]; then
    echo "Final health check:"
    curl -s http://127.0.0.1:5001/healthz || echo "Webhook not responding yet"
else
    echo "[DRY-RUN] Health check: curl http://127.0.0.1:5001/healthz"
fi
