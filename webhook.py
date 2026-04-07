import os
import json
import subprocess
import time
from datetime import datetime
from flask import Flask, request, jsonify

# Configuration
LOG_FILE = "/home/adityatiwari/Documents/AOPS/auto-remediation.log"
SLACK_WEBHOOK = "https://hooks.slack.com/services/T01D35Z6P7P/B06TZM06DQX/YOUR_KEY"
SCRIPTS_DIR = "/home/adityatiwari/Documents/AOPS/remediate"

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
        if alert.get('status') == 'resolved':
            log_remediation("resolution", alert.get('labels', {}).get('platform', 'unknown'), "System Recovered", "monitoring", "RESOLVED", 0, "Self-heal confirmed by Alertmanager")
            continue

        start_time = time.time()
        labels = alert.get('labels', {})
        remediation_task = labels.get('remediation', 'generic_triage')
        platform = labels.get('platform', 'unknown')

        script_path = os.path.join(SCRIPTS_DIR, f"{remediation_task}.sh")
        
        if os.path.exists(script_path):
            env = os.environ.copy()
            for key, value in labels.items():
                env[f"LABEL_{key.upper()}"] = str(value)

            try:
                result = subprocess.run(["bash", script_path], env=env, capture_output=True, text=True, timeout=60)
                duration = round(time.time() - start_time, 2)
                
                # Dynamic Diagnosis based on script result
                if "Confirmed:" in result.stdout:
                    diagnosis = result.stdout.split("Confirmed:")[1].split(".")[0].strip()
                    exec_status = "SUCCESS"
                elif "SKIPPED" in result.stdout:
                    diagnosis = "Triage Finished (No Pattern Matched)"
                    exec_status = "SKIPPED"
                else:
                    diagnosis = "Analysis Attempted"
                    exec_status = "FAILED" if result.returncode != 0 else "SUCCESS"
                
                log_remediation(remediation_task, platform, diagnosis, remediation_task, exec_status, duration, result.stdout.strip())
                
            except Exception as e:
                log_remediation(remediation_task, platform, "ERROR CRASH", "triage", "FAILED", 0, str(e))

    return jsonify({"status": "processed"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5051)
