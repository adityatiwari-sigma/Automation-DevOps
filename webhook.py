import os
import json
import subprocess
import threading
import datetime
import signal
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("WEBHOOK_PORT", 5001))
REMEDIATION_DIR = os.environ.get("REMEDIATION_DIR", "/opt/monitoring/remediate")
LOG_FILE = "/var/log/auto-remediation.log"
PID_FILE = "/run/auto-remediation-webhook.pid"

def write_pid():
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
    except Exception as e:
        print(f"Error writing PID file: {e}")

def signal_handler(signum, frame):
    if signum == signal.SIGUSR1:
        log_message("Received SIGUSR1 (log rotation signal)")
    elif signum == signal.SIGTERM:
        log_message("Received SIGTERM, shutting down...")
        sys.exit(0)

def log_message(message):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_msg = f"{timestamp} {message}"
    print(formatted_msg)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(formatted_msg + "\n")
    except Exception as e:
        print(f"Error logging to {LOG_FILE}: {e}")

def run_remediation(script_name):
    script_path = os.path.join(REMEDIATION_DIR, f"{script_name}.sh")
    if not os.path.exists(script_path):
        log_message(f"REMEDIATION_DISPATCH ERROR=\"Script not found: {script_path}\"")
        return

    log_message(f"REMEDIATION_DISPATCH SCRIPT=\"{script_name}\" STATUS=\"START\"")
    try:
        # Run script and ignore output as it logs itself
        subprocess.run(["/bin/bash", script_path], check=False, env=os.environ.copy())
        log_message(f"REMEDIATION_DISPATCH SCRIPT=\"{script_name}\" STATUS=\"DISPATCHED\"")
    except Exception as e:
        log_message(f"REMEDIATION_DISPATCH SCRIPT=\"{script_name}\" STATUS=\"FAILED\" ERROR=\"{e}\"")

class WebhookHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/healthz':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            auto_remediate = os.environ.get("AUTO_REMEDIATE", "true")
            response = {"status": "ok", "auto_remediate": auto_remediate}
            self.wfile.write(json.dumps(response).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == '/webhook':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                data = json.loads(post_data.decode('utf-8'))
            except Exception as e:
                log_message(f"WEBHOOK_ERROR=\"JSON parse failed: {e}\"")
                self.send_response(400)
                self.end_headers()
                return

            auto_remediate = os.environ.get("AUTO_REMEDIATE", "true")
            
            for alert in data.get('alerts', []):
                if alert.get('status') == 'firing':
                    remediation = alert.get('labels', {}).get('remediation')
                    if remediation:
                        if auto_remediate == 'false':
                            log_message(f"WEBHOOK_SKIP SCRIPT=\"{remediation}\" REASON=\"AUTO_REMEDIATE=false\"")
                        else:
                            # Run each script in a background thread
                            thread = threading.Thread(target=run_remediation, args=(remediation,))
                            thread.start()

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Override to suppress default HTTP logging to stderr, 
        # but we could also pipe it to our log file if needed.
        return

def run_server():
    # Register signal handlers
    signal.signal(signal.SIGUSR1, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    write_pid()

    server_address = ('', PORT)
    try:
        httpd = HTTPServer(server_address, WebhookHandler)
        log_message(f"WEBHOOK_START PORT={PORT} REMEDIATION_DIR={REMEDIATION_DIR}")
        httpd.serve_forever()
    except Exception as e:
        log_message(f"WEBHOOK_FATAL ERROR=\"{e}\"")

if __name__ == '__main__':
    run_server()
