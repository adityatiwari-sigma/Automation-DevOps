import os
import json
import subprocess
import time
from datetime import datetime
from flask import Flask, request, jsonify
import threading
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Configuration
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auto-remediation.log")
SLACK_WEBHOOK = "https://hooks.slack.com/services/T01D35Z6P7P/B06TZM06DQX/YOUR_KEY"
SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "remediate")

# Deduplication and Resolution History
remediation_history = {}
fired_alerts = {}

def get_env_var(var_name):
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                if line.startswith(f"{var_name}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get(var_name)

def send_email(subject, body):
    sender_email = get_env_var("GMAIL_ADDRESS")
    sender_password = get_env_var("GMAIL_APP_PASSWORD")
    if not sender_email or not sender_password:
        print("Email credentials missing in .env")
        return

    try:
        msg = MIMEMultipart()
        msg['From'] = f"AOPS Remediation <{sender_email}>"
        msg['To'] = sender_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, sender_password)
            server.send_message(msg)
    except Exception as e:
        print(f"Failed to send email: {e}")

def is_auto_remediate_enabled():
    paths = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        "/etc/default/auto-remediation-webhook"
    ]
    for env_path in paths:
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                for line in f:
                    if line.startswith("AUTO_REMEDIATE="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'").lower()
                        if val == "false":
                            return False
    if os.environ.get("AUTO_REMEDIATE", "true").lower() == "false":
        return False
    return True

app = Flask(__name__)

def log_remediation(trigger, platform, diagnosis, action, status, duration, evidence):
    """Writes a production-ready structured log entry."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    # Use JSON for evidence to keep it one-line but readable
    evidence_clean = evidence.replace('\n', ' | ').replace('"', "'")
    log_line = (
        f"{timestamp} "
        f"TRIGGER=\"{trigger}\" "
        f"PLATFORM=\"{platform}\" "
        f"DIAGNOSIS=\"{diagnosis}\" "
        f"ACTION=\"{action}\" "
        f"STATUS=\"{status}\" "
        f"DURATION=\"{duration}s\" "
        f"EVIDENCE=\"{evidence_clean}\"\n"
    )
    with open(LOG_FILE, "a") as f:
        f.write(log_line)
        f.flush()
        os.fsync(f.fileno())

@app.route('/', methods=['GET'])
def index():
    """Live Action Dashboard for AOPS Remediation."""
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r') as f:
                lines = f.readlines()
            recent_logs = "".join(lines[-10:])
        else:
            recent_logs = "No incidents logged yet."
    except Exception as e:
        recent_logs = f"Error reading logs: {str(e)}"
    
    return f"""
    <html><body style="font-family: sans-serif; padding: 20px; background: #fafafa;">
    <h1>🚀 AOPS Auto-Remediation Engine</h1>
    <p>Engine Status: <span style="color: green; font-weight: bold;">ACTIVE</span> | UI Port: 5051</p>
    <hr/>
    <h3>📜 Full Remediation Evidence Feed (Last 10):</h3>
    <pre style="background: #222; color: #0f0; padding: 15px; border-radius: 5px; overflow-x: auto; font-size: 13px; line-height: 1.5;">{recent_logs}</pre>
    <button onclick="window.location.reload();" style="padding: 10px 20px; cursor: pointer; background: #333; color: white; border: none; border-radius: 5px;">🔄 Refresh Feed</button>
    <p style="color: #666; font-size: 12px;">Monitoring path: {LOG_FILE}</p>
    </body></html>
    """, 200

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data:
        return jsonify({"error": "No data"}), 400

    alerts = data.get('alerts', [])
    for alert in alerts:
        threading.Thread(target=process_alert, args=(alert,)).start()

    return jsonify({"status": "processed"}), 200

def process_alert(alert):
    labels = alert.get('labels', {})
    remediation_task = labels.get('remediation', 'generic_triage')
    platform = labels.get('platform', 'unknown')
    fingerprint = alert.get('fingerprint', f"{remediation_task}_{platform}")

    if alert.get('status') == 'resolved':
        # Retrieve history
        history = remediation_history.pop(fingerprint, None)
        
        # Run Verification Command
        verification_output = "No specific verification command defined."
        if history:
            task = history.get('task')
            remote_ip = get_env_var("REMOTE_IP") or "10.10.2.21"
            ssh_user = get_env_var("SSH_USER") or "test"
            ssh_pw = get_env_var("SSH_PASSWORD") or ""
            sudo_pw = get_env_var("SUDO_PASSWORD") or ""
            
            verify_cmd = "uptime"
            if task == "fpm-reload":
                verify_cmd = "systemctl status php8.4-fpm --no-pager"
            elif task == "nginx-file-limit":
                verify_cmd = "sysctl fs.file-max && systemctl status nginx --no-pager"
            elif task == "redis-flush-cache":
                verify_cmd = "redis-cli info memory | grep used_memory_human"

            try:
                # Use ssh_helper.py for verification
                scripts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "remediate")
                helper_path = os.path.join(scripts_dir, "ssh_helper.py")
                res = subprocess.run(["python3", helper_path, remote_ip, ssh_user, ssh_pw, sudo_pw, verify_cmd], capture_output=True, text=True, timeout=30)
                verification_output = res.stdout.strip() or res.stderr.strip()
            except Exception as e:
                verification_output = f"Verification failed: {e}"

        # Build Summary Email
        subject = f"✅ Incident Resolved: {remediation_task} on {platform}"
        body = f"The incident that triggered '{remediation_task}' has been resolved.\n\n"
        if history:
            body += f"--- REMEDIATION DETAILS ---\n"
            body += f"Trigger: {history.get('trigger')}\n"
            body += f"Action Taken: {history.get('action')}\n"
            body += f"Start Time: {history.get('start_time')}\n"
            body += f"Duration: {history.get('duration')}s\n"
            body += f"Remediation Status: {history.get('status')}\n\n"
            body += f"--- INITIAL EVIDENCE ---\n"
            body += f"{history.get('evidence')}\n\n"
        
        body += f"--- POST-REMEDIATION VERIFICATION ---\n"
        body += f"{verification_output}\n\n"
        body += f"System Status: RECOVERED\n"
        
        send_email(subject, body)
        log_remediation("resolution", platform, "System Recovered", "monitoring", "RESOLVED", 0, "Self-heal summary sent")
        return

    # FIRING CASE
    start_time = time.time()
    start_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Deduplication Check (30s cooldown for script execution itself)
    current_time = time.time()
    if fingerprint in fired_alerts:
        if current_time - fired_alerts[fingerprint] < 30:
            return
    fired_alerts[fingerprint] = current_time

    script_path = os.path.join(SCRIPTS_DIR, f"{remediation_task}.sh")
    
    if not is_auto_remediate_enabled():
        log_remediation(remediation_task, platform, "Maintenance Window", "skipped", "SKIPPED", 0, "AUTO_REMEDIATE is false")
        return

    if os.path.exists(script_path):
        env = os.environ.copy()
        for key, value in labels.items():
            env[f"LABEL_{key.upper()}"] = str(value)

        try:
            result = subprocess.run(["bash", script_path], env=env, capture_output=True, text=True, timeout=60)
            duration = round(time.time() - start_time, 2)
            
            if "Confirmed:" in result.stdout:
                diagnosis = result.stdout.split("Confirmed:")[1].split(".")[0].strip()
                exec_status = "SUCCESS"
            else:
                diagnosis = "Analysis Attempted"
                exec_status = "FAILED" if result.returncode != 0 else "SUCCESS"
            
            # Store for Resolved Email
            remediation_history[fingerprint] = {
                "trigger": remediation_task,
                "action": remediation_task,
                "task": remediation_task,
                "start_time": start_ts,
                "duration": duration,
                "status": exec_status,
                "evidence": result.stdout.strip(),
                "platform": platform
            }
            
            log_remediation(remediation_task, platform, diagnosis, remediation_task, exec_status, duration, result.stdout.strip())
            # Do NOT send email here; wait for resolution
            
        except Exception as e:
            log_remediation(remediation_task, platform, "ERROR CRASH", "triage", "FAILED", 0, str(e))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5051)
