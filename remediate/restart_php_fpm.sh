#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# PHP-FPM FULL RESTART — hard restart (not reload) of PHP-FPM
# Use when fpm-reload.sh (graceful reload) is insufficient:
# stuck workers, corrupted opcode cache, memory leak recovery
# ============================================================

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$(dirname "$DIR")/auto-remediation.log"
CONFIG_FILE="$(dirname "$DIR")/config.json"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# ── Safety guard: respect AUTO_REMEDIATE flag ──────────────
AUTO_REMEDIATE=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['webhook']['auto_remediate'])" 2>/dev/null || echo "True")
if [ "$AUTO_REMEDIATE" = "False" ]; then
    echo "[$TIMESTAMP] TRIGGER=\"restart_php_fpm\" STATUS=\"SKIPPED\" REASON=\"auto_remediate=false\"" >> "$LOG_FILE"
    exit 0
fi

REMOTE_IP=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['network']['remote_ip'])")

echo "[$TIMESTAMP] TRIGGER=\"restart_php_fpm\" ACTION=\"ssh-execute\" STATUS=\"STARTING\" TARGET=\"$REMOTE_IP\"" >> "$LOG_FILE"

# Full restart — kills all workers, clears opcode cache, starts fresh
if python3 "$DIR/ssh_helper.py" "systemctl restart php8.4-fpm && systemctl is-active php8.4-fpm"; then
    echo "[$TIMESTAMP] TRIGGER=\"restart_php_fpm\" ACTION=\"service-restart\" STATUS=\"SUCCESS\" TARGET=\"$REMOTE_IP\"" >> "$LOG_FILE"
    echo "PHP-FPM restarted (full) successfully on $REMOTE_IP."
else
    echo "[$TIMESTAMP] TRIGGER=\"restart_php_fpm\" ACTION=\"service-restart\" STATUS=\"FAILED\" TARGET=\"$REMOTE_IP\"" >> "$LOG_FILE"
    echo "ERROR: PHP-FPM full restart failed on $REMOTE_IP." >&2
    exit 1
fi
