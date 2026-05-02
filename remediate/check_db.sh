#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# DATABASE HEALTH CHECK — verifies MySQL is running and
# accepting connections on the remote server
# ============================================================

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$(dirname "$DIR")/auto-remediation.log"
CONFIG_FILE="$(dirname "$DIR")/config.json"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

REMOTE_IP=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['network']['remote_ip'])")

echo "[$TIMESTAMP] TRIGGER=\"check_db\" ACTION=\"health-check\" STATUS=\"STARTING\" TARGET=\"$REMOTE_IP\"" >> "$LOG_FILE"

# Check MySQL service status and basic connectivity in one SSH call
DB_STATUS=$(python3 "$DIR/ssh_helper.py" \
    "systemctl is-active mysql 2>/dev/null || systemctl is-active mariadb 2>/dev/null || echo inactive")

if [ "$DB_STATUS" = "active" ]; then
    # Verify it accepts connections (no credentials needed for localhost ping)
    PING_RESULT=$(python3 "$DIR/ssh_helper.py" \
        "mysqladmin --connect-timeout=5 ping 2>&1 || echo FAIL" 2>/dev/null || echo "FAIL")

    if echo "$PING_RESULT" | grep -q "mysqld is alive"; then
        echo "[$TIMESTAMP] TRIGGER=\"check_db\" ACTION=\"health-check\" STATUS=\"SUCCESS\" TARGET=\"$REMOTE_IP\" DETAIL=\"MySQL running and accepting connections\"" >> "$LOG_FILE"
        echo "Database healthy on $REMOTE_IP — MySQL running and accepting connections."
    else
        echo "[$TIMESTAMP] TRIGGER=\"check_db\" ACTION=\"health-check\" STATUS=\"DEGRADED\" TARGET=\"$REMOTE_IP\" DETAIL=\"service active but not accepting connections\"" >> "$LOG_FILE"
        echo "WARNING: MySQL service active but not accepting connections on $REMOTE_IP." >&2
        exit 1
    fi
else
    echo "[$TIMESTAMP] TRIGGER=\"check_db\" ACTION=\"health-check\" STATUS=\"FAILED\" TARGET=\"$REMOTE_IP\" DETAIL=\"MySQL/MariaDB service not active (state: $DB_STATUS)\"" >> "$LOG_FILE"
    echo "ERROR: MySQL/MariaDB is not running on $REMOTE_IP." >&2
    exit 1
fi
