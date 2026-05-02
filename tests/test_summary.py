#!/usr/bin/env python3
"""
test_summary.py — AIOps Platform Health Check & Test Summary Report

Performs a full system health assessment and runs the unit test suite,
then outputs a formatted report to the terminal (and optionally a file).

Usage:
    python3 tests/test_summary.py              # full report
    python3 tests/test_summary.py --no-tests   # health checks only (faster)
    python3 tests/test_summary.py --output report.txt  # save to file

What it checks:
  1. Configuration  — config.json validity, generated files presence
  2. Docker Services — container running state and health status
  3. Endpoint Health — HTTP health checks on all service URLs
  4. Remote Connectivity — reachability of the remote server and its exporters
  5. Webhook Service  — systemd status, process, auto-remediate flag
  6. Unit Tests       — runs pytest suite and summarises results
  7. Recent Activity  — last 10 remediation log entries
  8. Active Alerts    — queries Prometheus API for firing alerts
"""

import os
import sys
import json
import time
import socket
import argparse
import subprocess
import urllib.request
import urllib.error
from datetime import datetime
from typing import NamedTuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# ════════════════════════════════════════════════════════════
# RESULT MODEL
# ════════════════════════════════════════════════════════════

class CheckResult(NamedTuple):
    name: str
    passed: bool
    detail: str
    error: str = ""


# ════════════════════════════════════════════════════════════
# TERMINAL COLOURS
# ════════════════════════════════════════════════════════════

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

def ok(text):    return f"{GREEN}✓{RESET} {text}"
def fail(text):  return f"{RED}✗{RESET} {text}"
def warn(text):  return f"{YELLOW}!{RESET} {text}"
def info(text):  return f"{CYAN}·{RESET} {text}"
def header(text):return f"{BOLD}{CYAN}{text}{RESET}"
def dim(text):   return f"{DIM}{text}{RESET}"


# ════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════

def _load_config() -> dict | None:
    path = os.path.join(ROOT, "config.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _http_get(url: str, timeout: int = 5) -> tuple[bool, str]:
    """Returns (success, detail)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            status = resp.getcode()
            return (status == 200), f"HTTP {status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)


def _tcp_reachable(host: str, port: int, timeout: int = 3) -> tuple[bool, str]:
    """Check TCP connectivity to host:port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"{host}:{port} reachable"
    except Exception as e:
        return False, str(e)


def _run_cmd(cmd: list, timeout: int = 10) -> tuple[bool, str]:
    """Run a shell command; return (success, stdout/stderr)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        output = (r.stdout + r.stderr).strip()
        return r.returncode == 0, output
    except Exception as e:
        return False, str(e)


def _docker_containers() -> dict:
    """Returns {name: {status, health}} for all containers."""
    ok_run, output = _run_cmd(["docker", "ps", "-a", "--format",
                                "{{.Names}}\t{{.Status}}\t{{.State}}"])
    if not ok_run:
        return {}
    containers = {}
    for line in output.strip().splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            name   = parts[0]
            status = parts[1]
            state  = parts[2] if len(parts) > 2 else ""
            containers[name] = {"status": status, "state": state}
    return containers


# ════════════════════════════════════════════════════════════
# CHECK FUNCTIONS
# ════════════════════════════════════════════════════════════

def check_configuration(cfg: dict | None) -> list[CheckResult]:
    results = []

    if cfg is None:
        results.append(CheckResult("config.json", False, "File not found",
                                   "Copy config.example.json → config.json and fill in values"))
        return results

    results.append(CheckResult("config.json", True, "Present and readable"))

    # Required fields
    required = [
        ("network.local_ip",        "Local IP address"),
        ("network.remote_ip",       "Remote server IP"),
        ("email.from_address",      "SMTP from address"),
        ("webhook.secret_token",    "Webhook bearer token"),
        ("grafana.admin_password",  "Grafana admin password"),
    ]
    for key_path, label in required:
        val = cfg
        try:
            for part in key_path.split("."):
                val = val[part]
            has_placeholder = any(p in str(val) for p in ["CHANGE_ME", "FILL_IN", "GENERATE_WITH"])
            if has_placeholder:
                results.append(CheckResult(f"config: {label}", False,
                                           f"Still contains placeholder value",
                                           f"Edit config.json → {key_path}"))
            else:
                results.append(CheckResult(f"config: {label}", True, str(val)[:40]))
        except (KeyError, TypeError):
            results.append(CheckResult(f"config: {label}", False, "Key missing",
                                       f"Add {key_path} to config.json"))

    # Redis safety check
    try:
        cache_db   = cfg["redis"]["cache_db"]
        session_db = cfg["redis"]["session_db"]
        if cache_db == session_db:
            results.append(CheckResult("config: Redis DB isolation", False,
                                       f"cache_db == session_db == {cache_db}",
                                       "DANGER: FLUSHDB would erase session data"))
        else:
            results.append(CheckResult("config: Redis DB isolation", True,
                                       f"cache={cache_db}, session={session_db}"))
    except KeyError:
        results.append(CheckResult("config: Redis DB isolation", False, "redis keys missing"))

    # Generated files
    generated = [
        ".env",
        "prometheus/prometheus.yml",
        "alertmanager/alertmanager.yml",
        "promtail/promtail-config.yml",
    ]
    for rel in generated:
        path = os.path.join(ROOT, rel)
        exists = os.path.exists(path)
        results.append(CheckResult(
            f"generated: {rel}",
            exists,
            "Present" if exists else "Missing",
            "" if exists else "Run: python3 generate_configs.py"
        ))

    return results


def check_docker_services(cfg: dict | None) -> list[CheckResult]:
    results = []
    expected = [
        "prometheus", "loki", "alertmanager", "grafana",
        "pushgateway", "node-exporter", "cadvisor",
    ]
    containers = _docker_containers()
    if not containers:
        results.append(CheckResult("Docker daemon", False, "Could not query Docker",
                                   "Is Docker running? Try: docker ps"))
        return results

    results.append(CheckResult("Docker daemon", True, f"{len(containers)} containers found"))

    for name in expected:
        if name not in containers:
            results.append(CheckResult(f"container: {name}", False, "Not found",
                                       f"Run: docker-compose up -d {name}"))
            continue
        status = containers[name]["status"]
        running = "Up" in status
        healthy = "(healthy)" in status
        if running and healthy:
            detail = f"Running · healthy"
        elif running:
            detail = f"Running · {status[:50]}"
        else:
            detail = f"Stopped · {status[:50]}"
        results.append(CheckResult(f"container: {name}", running, detail,
                                   "" if running else f"Restart: docker-compose restart {name}"))

    return results


def check_endpoint_health(cfg: dict | None) -> list[CheckResult]:
    results = []
    if cfg is None:
        return [CheckResult("Endpoints", False, "Skipped — config.json missing")]

    local = cfg["network"]["local_ip"]
    ports = cfg["ports"]

    endpoints = [
        ("Prometheus",   f"http://{local}:{ports['prometheus']}/-/healthy"),
        ("Loki",         f"http://{local}:{ports['loki']}/ready"),
        ("Alertmanager", f"http://{local}:{ports['alertmanager']}/-/healthy"),
        ("Grafana",      f"http://{local}:{ports['grafana']}/api/health"),
        ("Pushgateway",  f"http://{local}:{ports['pushgateway']}/-/healthy"),
        ("Webhook",      f"http://{local}:{ports['webhook']}"),
    ]

    for name, url in endpoints:
        success, detail = _http_get(url)
        results.append(CheckResult(
            f"endpoint: {name}",
            success,
            f"{url} — {detail}",
            "" if success else f"Check: docker logs {name.lower()} | tail -20"
        ))

    return results


def check_remote_connectivity(cfg: dict | None) -> list[CheckResult]:
    results = []
    if cfg is None:
        return [CheckResult("Remote connectivity", False, "Skipped — config.json missing")]

    remote = cfg["network"]["remote_ip"]
    ports  = cfg["ports"]

    # Ping (best-effort — may be blocked by firewall)
    ping_ok, ping_out = _run_cmd(["ping", "-c", "1", "-W", "2", remote])
    results.append(CheckResult(
        f"remote: ping {remote}",
        ping_ok,
        "Responding" if ping_ok else "No response (may be firewalled)",
    ))

    # TCP port checks
    exporter_ports = [
        ("Node Exporter",   ports["node_exporter"]),
        ("Nginx Exporter",  ports["nginx_exporter"]),
        ("Promtail",        ports["promtail"]),
    ]
    for name, port in exporter_ports:
        ok_conn, detail = _tcp_reachable(remote, port)
        results.append(CheckResult(
            f"remote: {name} :{port}",
            ok_conn,
            detail,
            "" if ok_conn else f"Check if {name} is running on {remote}"
        ))

    return results


def check_webhook_service() -> list[CheckResult]:
    results = []

    # systemd status
    ok_run, output = _run_cmd(["systemctl", "is-active", "auto-remediation-webhook"])
    active = output.strip() == "active"
    results.append(CheckResult(
        "webhook: systemd service",
        active,
        output.strip(),
        "" if active else "Start: sudo systemctl start auto-remediation-webhook"
    ))

    # Process check
    ok_run, ps_out = _run_cmd(["pgrep", "-a", "gunicorn"])
    has_process = ok_run and "webhook" in ps_out
    results.append(CheckResult(
        "webhook: gunicorn process",
        has_process,
        ps_out[:80] if has_process else "Not found",
        "" if has_process else "Check service: journalctl -u auto-remediation-webhook -n 50"
    ))

    # Health endpoint
    ok_http, http_detail = _http_get("http://127.0.0.1:5051")
    auto_rem = "unknown"
    if ok_http:
        try:
            with urllib.request.urlopen("http://127.0.0.1:5051", timeout=3) as r:
                body = json.loads(r.read())
                auto_rem = str(body.get("auto_remediate", "unknown"))
        except Exception:
            pass
    results.append(CheckResult(
        "webhook: HTTP",
        ok_http,
        f"{http_detail} · auto_remediate={auto_rem}",
        "" if ok_http else "Check: curl http://localhost:5051"
    ))

    return results


def get_active_alerts(cfg: dict | None) -> list[dict]:
    """Query Prometheus for currently firing alerts."""
    if cfg is None:
        return []
    local = cfg["network"]["local_ip"]
    port  = cfg["ports"]["prometheus"]
    url   = f"http://{local}:{port}/api/v1/alerts"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
            return [a for a in data.get("data", {}).get("alerts", [])
                    if a.get("state") == "firing"]
    except Exception:
        return []


def get_recent_remediations(n: int = 10) -> list[str]:
    """Read the last N lines from the auto-remediation log."""
    log_path = os.path.join(ROOT, "auto-remediation.log")
    if not os.path.exists(log_path):
        return ["Log file not found (no remediations have run yet)"]
    try:
        with open(log_path) as f:
            lines = f.readlines()
        return [l.rstrip() for l in lines[-n:]]
    except Exception as e:
        return [f"Error reading log: {e}"]


def run_unit_tests() -> tuple[bool, str, int, int]:
    """
    Run pytest and return (all_passed, summary_output, passed_count, total_count).
    """
    test_dir = os.path.join(ROOT, "tests")
    ok_run, output = _run_cmd(
        [sys.executable, "-m", "pytest", test_dir,
         "-v", "--tb=short", "--no-header", "-q"],
        timeout=120
    )
    # Parse counts from the last line "X passed, Y failed in Zs"
    passed = failed = 0
    for line in reversed(output.splitlines()):
        import re
        m = re.search(r"(\d+) passed", line)
        if m:
            passed = int(m.group(1))
        m2 = re.search(r"(\d+) failed", line)
        if m2:
            failed = int(m2.group(1))
        if m or m2:
            break
    total = passed + failed
    return ok_run and failed == 0, output, passed, total


# ════════════════════════════════════════════════════════════
# REPORT RENDERER
# ════════════════════════════════════════════════════════════

def _section(title: str, results: list[CheckResult], buf: list):
    buf.append("")
    buf.append(header(f"{'─'*4} {title} {'─'*(50 - len(title))}"))
    for r in results:
        status_icon = ok(r.name) if r.passed else fail(r.name)
        line = f"  {status_icon:<55}  {dim(r.detail)}"
        buf.append(line)
        if not r.passed and r.error:
            buf.append(f"    {YELLOW}→ {r.error}{RESET}")


def _count_results(results: list[CheckResult]) -> tuple[int, int]:
    passed = sum(1 for r in results if r.passed)
    return passed, len(results)


def generate_report(run_tests: bool = True) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cfg = _load_config()

    buf = []
    buf.append("")
    buf.append(f"{BOLD}{'═'*66}{RESET}")
    buf.append(f"{BOLD}  AIOps Platform — Health & Test Summary Report{RESET}")
    buf.append(f"  Generated: {now}")
    if cfg:
        buf.append(f"  Platform:  {cfg.get('platform', {}).get('name', 'unknown')}")
        buf.append(f"  Local IP:  {cfg.get('network', {}).get('local_ip', 'unknown')}")
        buf.append(f"  Remote IP: {cfg.get('network', {}).get('remote_ip', 'unknown')}")
    buf.append(f"{BOLD}{'═'*66}{RESET}")

    all_results = []

    # 1. Configuration
    cfg_results = check_configuration(cfg)
    _section("CONFIGURATION", cfg_results, buf)
    all_results.extend(cfg_results)

    # 2. Docker services
    docker_results = check_docker_services(cfg)
    _section("DOCKER SERVICES", docker_results, buf)
    all_results.extend(docker_results)

    # 3. Endpoint health
    ep_results = check_endpoint_health(cfg)
    _section("ENDPOINT HEALTH", ep_results, buf)
    all_results.extend(ep_results)

    # 4. Remote connectivity
    remote_results = check_remote_connectivity(cfg)
    _section("REMOTE CONNECTIVITY", remote_results, buf)
    all_results.extend(remote_results)

    # 5. Webhook service
    webhook_results = check_webhook_service()
    _section("WEBHOOK SERVICE", webhook_results, buf)
    all_results.extend(webhook_results)

    # 6. Active alerts
    buf.append("")
    buf.append(header(f"{'─'*4} ACTIVE ALERTS {'─'*38}"))
    alerts = get_active_alerts(cfg)
    if not alerts:
        buf.append(f"  {ok('No active firing alerts')}")
    else:
        for a in alerts:
            name = a.get("labels", {}).get("alertname", "unknown")
            sev  = a.get("labels", {}).get("severity", "?")
            buf.append(f"  {fail(name):<55}  {dim(f'severity={sev}')}")

    # 7. Recent remediation log
    buf.append("")
    buf.append(header(f"{'─'*4} RECENT REMEDIATIONS (last 10 lines) {'─'*13}"))
    for line in get_recent_remediations(10):
        buf.append(f"  {dim(line)}")

    # 8. Unit tests
    test_all_passed = True
    test_passed = test_total = 0
    if run_tests:
        buf.append("")
        buf.append(header(f"{'─'*4} UNIT TESTS {'─'*42}"))
        buf.append(f"  {info('Running pytest ...')}")
        test_all_passed, test_output, test_passed, test_total = run_unit_tests()
        # Print individual test lines
        for line in test_output.splitlines():
            if "PASSED" in line:
                buf.append(f"    {GREEN}PASS{RESET}  {dim(line.split('PASSED')[0].strip())}")
            elif "FAILED" in line or "ERROR" in line:
                buf.append(f"    {RED}FAIL{RESET}  {line.strip()}")
            elif "short test summary" in line.lower():
                break
        if test_all_passed:
            buf.append(f"\n  {ok(f'{test_passed}/{test_total} tests passed')}")
        else:
            failed_count = test_total - test_passed
            buf.append(f"\n  {fail(f'{failed_count} test(s) FAILED ({test_passed}/{test_total} passed)')}")
    else:
        buf.append("")
        buf.append(header(f"{'─'*4} UNIT TESTS {'─'*42}"))
        buf.append(f"  {warn('Skipped (run without --no-tests to include)')}")

    # ── Overall summary ──────────────────────────────────────
    total_passed, total_checks = _count_results(all_results)
    total_failed = total_checks - total_passed
    buf.append("")
    buf.append(f"{BOLD}{'═'*66}{RESET}")

    all_ok = total_failed == 0 and (not run_tests or test_all_passed)
    if all_ok:
        buf.append(f"{BOLD}{GREEN}  OVERALL STATUS: ALL SYSTEMS OPERATIONAL{RESET}")
    elif total_failed <= 2:
        buf.append(f"{BOLD}{YELLOW}  OVERALL STATUS: DEGRADED ({total_failed} check(s) failed){RESET}")
    else:
        buf.append(f"{BOLD}{RED}  OVERALL STATUS: CRITICAL ({total_failed} check(s) failed){RESET}")

    buf.append(f"  Infrastructure:  {total_passed}/{total_checks} checks passed")
    if run_tests:
        buf.append(f"  Unit Tests:      {test_passed}/{test_total} tests passed")
    buf.append(f"  Report time:     {now}")
    buf.append(f"{BOLD}{'═'*66}{RESET}")
    buf.append("")

    if not all_ok:
        buf.append(f"{YELLOW}  Failed checks:{RESET}")
        for r in all_results:
            if not r.passed:
                buf.append(f"    {fail(r.name)}: {r.detail}")
                if r.error:
                    buf.append(f"      {YELLOW}→ {r.error}{RESET}")
        buf.append("")

    return "\n".join(buf)


# ════════════════════════════════════════════════════════════
# ENTRY POINT
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AIOps Platform health check and test summary"
    )
    parser.add_argument("--no-tests", action="store_true",
                        help="Skip unit tests (health checks only)")
    parser.add_argument("--output", metavar="FILE",
                        help="Also write report to a file (stripped of ANSI codes)")
    args = parser.parse_args()

    report = generate_report(run_tests=not args.no_tests)
    print(report)

    if args.output:
        import re
        ansi_escape = re.compile(r"\033\[[0-9;]*m")
        plain = ansi_escape.sub("", report)
        with open(args.output, "w") as f:
            f.write(plain)
        print(f"Report saved to: {args.output}")

    # Exit non-zero if any infrastructure check failed
    cfg = _load_config()
    all_infra = (
        check_configuration(cfg) +
        check_docker_services(cfg) +
        check_endpoint_health(cfg)
    )
    sys.exit(0 if all(r.passed for r in all_infra) else 1)
