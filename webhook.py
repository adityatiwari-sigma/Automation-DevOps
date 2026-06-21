"""
Auto-Remediation Webhook — production-grade Flask/Gunicorn service.

Run via systemd/gunicorn:
  gunicorn --workers 2 --threads 4 --bind 0.0.0.0:5051 webhook:app

Security:
  - Bearer token auth on all POST /webhook requests (token from config.json)
  - Script name validated against an explicit allowlist (no path traversal)
  - Shared state protected by threading.Lock
  - Request body capped at 1 MB
"""

import os
import json
import subprocess
import threading
import time
import smtplib
from datetime import datetime
from functools import wraps
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from flask import Flask, request, jsonify, abort

import config_loader

# ────────────────────────────────────────────────────────────
# Constants derived from config.json
# ────────────────────────────────────────────────────────────
_cfg           = config_loader.load()
WEBHOOK_TOKEN  = _cfg["webhook"]["secret_token"]
SCRIPTS_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "remediate")
LOG_FILE       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auto-remediation.log")

# Explicit allowlist — only these script names can be dispatched
ALLOWED_SCRIPTS = frozenset({"fpm-reload", "nginx-file-limit", "redis-flush-cache", "generic_triage"})

DEDUP_WINDOW_SECONDS = 30   # ignore repeat firings within this window

# ────────────────────────────────────────────────────────────
# Thread-safe in-memory state
# ────────────────────────────────────────────────────────────
_lock              = threading.Lock()
_fired_alerts: dict     = {}   # fingerprint → last_fired_timestamp
_remediation_history: dict = {} # fingerprint → execution metadata


def _update_fired(fingerprint: str):
    with _lock:
        _fired_alerts[fingerprint] = time.time()


def _is_dedup_hit(fingerprint: str) -> bool:
    with _lock:
        last = _fired_alerts.get(fingerprint, 0)
        return (time.time() - last) < DEDUP_WINDOW_SECONDS


def _store_history(fingerprint: str, data: dict):
    with _lock:
        _remediation_history[fingerprint] = data


def _pop_history(fingerprint: str) -> dict | None:
    with _lock:
        return _remediation_history.pop(fingerprint, None)


# ────────────────────────────────────────────────────────────
# Flask app
# ────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MB cap


def require_auth(f):
    """Verify Bearer token sent by Alertmanager."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != WEBHOOK_TOKEN:
            abort(401)
        return f(*args, **kwargs)
    return decorated


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────
def _log(trigger: str, platform: str, diagnosis: str, action: str,
         status: str, duration: float, evidence: str, is_rca: bool = False):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    evidence_clean = evidence.replace("\n", " | ").replace('"', "'")[:500]
    line = (
        f"{ts} "
        f'TRIGGER="{trigger}" '
        f'PLATFORM="{platform}" '
        f'DIAGNOSIS="{diagnosis}" '
        f'ACTION="{action}" '
        f'STATUS="{status}" '
        f'DURATION="{duration:.2f}s" '
        f'EVIDENCE="{evidence_clean}"\n'
    )
    try:
        with open(LOG_FILE, "a") as f:
            if is_rca:
                f.write("─" * 60 + "\n")
                f.write(f"[{ts}] RCA INVESTIGATION STARTED\n")
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        print(f"[webhook] log write failed: {e}")


def _send_email(subject: str, body: str, to: str | None = None):
    cfg = config_loader.load()
    sender   = cfg["email"]["from_address"]
    password = cfg["email"]["app_password"]
    recipient = to or cfg["email"]["recipients"]["p1"]

    if not sender or not password:
        print("[webhook] email credentials not configured, skipping")
        return
    try:
        msg = MIMEMultipart()
        msg["From"]    = f"AIOps Remediation <{sender}>"
        msg["To"]      = recipient
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(sender, password)
            server.send_message(msg)
    except Exception as e:
        print(f"[webhook] email send failed: {e}")


def _get_safe_script_path(script_name: str) -> str | None:
    """Return the absolute path only if the script name is on the allowlist."""
    if script_name not in ALLOWED_SCRIPTS:
        return None
    candidate = os.path.realpath(os.path.join(SCRIPTS_DIR, f"{script_name}.sh"))
    safe_root  = os.path.realpath(SCRIPTS_DIR) + os.sep
    if not candidate.startswith(safe_root):
        return None
    return candidate


def _is_auto_remediate_enabled() -> bool:
    return bool(_cfg["webhook"].get("auto_remediate", True))


def _run_verify(task: str) -> str:
    """SSH into the remote server and collect post-remediation evidence."""
    cfg = config_loader.load()
    remote_ip  = cfg["network"]["remote_ip"]
    ssh_user   = cfg["ssh"]["user"]

    cmds = {
        "fpm-reload":       "systemctl status php8.4-fpm --no-pager",
        "nginx-file-limit": "sysctl fs.file-max && systemctl status nginx --no-pager",
        "redis-flush-cache":"redis-cli info memory | grep used_memory_human",
    }
    verify_cmd = cmds.get(task, "uptime")

    helper = os.path.join(SCRIPTS_DIR, "ssh_helper.py")
    try:
        res = subprocess.run(
            ["python3", helper, verify_cmd],
            capture_output=True, text=True, timeout=30
        )
        return (res.stdout.strip() or res.stderr.strip())[:1000]
    except Exception as e:
        return f"verification failed: {e}"


# ────────────────────────────────────────────────────────────
# Alert processor (runs in a background thread per alert)
# ────────────────────────────────────────────────────────────
def _process_alert(alert: dict):
    labels      = alert.get("labels", {})
    task        = labels.get("remediation", "generic_triage")
    platform    = labels.get("platform", "unknown")
    fingerprint = alert.get("fingerprint", f"{task}_{platform}")

    # ── RESOLVED ──────────────────────────────────────────────
    if alert.get("status") == "resolved":
        history = _pop_history(fingerprint)
        verify_output = _run_verify(task) if history else "no prior execution recorded"

        alert_name = labels.get("alertname", "Unknown")
        body = (
            f"Incident '{alert_name}' resolved on platform '{platform}'.\n\n"
            f"=== ROOT CAUSE ANALYSIS ===\n"
        )
        if history:
            body += (
                f"Detection:    {history['trigger']}\n"
                f"Action:       {history['action']}\n"
                f"Started:      {history['start_time']}\n"
                f"Duration:     {history['duration']:.2f}s\n"
                f"Final Status: {history['status']}\n"
                f"Evidence:     {history['evidence']}\n\n"
            )
        body += f"=== POST-REMEDIATION VERIFICATION ===\n{verify_output}\n"

        threading.Thread(
            target=_send_email,
            args=(f"[RESOLVED] {alert_name} [{platform}]", body),
            daemon=True
        ).start()
        _log("resolution", platform, "System Recovered", "monitoring", "RESOLVED", 0,
             "self-heal summary sent")
        return

    # ── FIRING ────────────────────────────────────────────────
    if not _is_auto_remediate_enabled():
        _log(task, platform, "Maintenance Window", "skipped", "SKIPPED", 0,
             "auto_remediate=false in config.json")
        return

    if _is_dedup_hit(fingerprint):
        return

    _update_fired(fingerprint)

    script_path = _get_safe_script_path(task)
    if script_path is None:
        _log(task, platform, "Blocked", "security", "BLOCKED", 0,
             f"'{task}' not in ALLOWED_SCRIPTS allowlist")
        return
    if not os.path.isfile(script_path):
        _log(task, platform, "Script missing", "error", "FAILED", 0,
             f"script not found: {script_path}")
        return

    start = time.time()
    start_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        env = os.environ.copy()
        for k, v in labels.items():
            env[f"LABEL_{k.upper()}"] = str(v)

        result = subprocess.run(
            ["bash", script_path],
            env=env, capture_output=True, text=True, timeout=90
        )
        duration = time.time() - start

        if "Confirmed:" in result.stdout:
            diagnosis = result.stdout.split("Confirmed:")[1].split(".")[0].strip()
        else:
            diagnosis = "Analysis attempted"

        exec_status = "FAILED" if result.returncode != 0 else "SUCCESS"
        evidence    = (result.stdout + result.stderr).strip()

        _store_history(fingerprint, {
            "trigger":    task,
            "action":     task,
            "start_time": start_ts,
            "duration":   duration,
            "status":     exec_status,
            "evidence":   evidence[:500],
            "platform":   platform,
        })

        _log(task, platform, diagnosis, task, exec_status, duration,
             evidence, is_rca=True)

    except subprocess.TimeoutExpired:
        _log(task, platform, "Timeout", task, "TIMEOUT", time.time() - start,
             "script exceeded 90s timeout")
    except Exception as e:
        _log(task, platform, "Exception", task, "FAILED", time.time() - start, str(e))


# ────────────────────────────────────────────────────────────
# Routes
# ────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"status": "ok", "auto_remediate": _is_auto_remediate_enabled()})


@app.route("/")
def index():
    try:
        lines = open(LOG_FILE).readlines()[-20:] if os.path.exists(LOG_FILE) else []
        recent = "".join(lines)
    except OSError as e:
        recent = f"Error reading log: {e}"

    port = _cfg["ports"]["webhook"]
    return (
        f"""<html><body style="font-family:monospace;padding:20px;background:#111;color:#0f0">
        <h2>AIOps Auto-Remediation Engine</h2>
        <p>Status: <b>ACTIVE</b> | Port: {port} | Auto-remediate: {_is_auto_remediate_enabled()}</p>
        <hr/>
        <h3>Recent log (last 20 lines):</h3>
        <pre style="overflow-x:auto;font-size:12px">{recent}</pre>
        <button onclick="location.reload()" style="padding:8px 16px;cursor:pointer">Refresh</button>
        </body></html>""",
        200,
    )


@app.route("/webhook", methods=["POST"])
@require_auth
def webhook():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "invalid JSON"}), 400

    for alert in data.get("alerts", []):
        threading.Thread(target=_process_alert, args=(alert,), daemon=True).start()

    return jsonify({"status": "accepted", "count": len(data.get("alerts", []))}), 200


# ── Only used for local dev testing; production uses gunicorn ──
if __name__ == "__main__":
    port = _cfg["ports"]["webhook"]
    print(f"[dev] starting Flask on port {port} — use gunicorn for production")
    app.run(host="127.0.0.1", port=port, debug=False)
