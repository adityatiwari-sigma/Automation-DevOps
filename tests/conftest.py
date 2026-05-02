"""
conftest.py — shared pytest fixtures for the AIOps test suite.

Injects a test configuration into config_loader BEFORE webhook.py is
imported so module-level code (_cfg = config_loader.load()) picks up
the test values instead of requiring a real config.json on disk.
"""

import sys
import os
import json
import pytest
import tempfile
import importlib

# ── Ensure the project root is on sys.path ──────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# ── Canonical test configuration ────────────────────────────
TEST_CONFIG = {
    "network": {
        "local_ip":  "127.0.0.1",
        "remote_ip": "192.0.2.1",       # TEST-NET — never actually routed
    },
    "ports": {
        "grafana":        3000,
        "prometheus":     9090,
        "loki":           3100,
        "alertmanager":   9093,
        "pushgateway":    9091,
        "webhook":        5051,
        "node_exporter":  9100,
        "nginx_exporter": 9113,
        "promtail":       9080,
        "cadvisor":       8080,
    },
    "grafana": {
        "admin_user":     "admin",
        "admin_password": "test-grafana-password",
    },
    "email": {
        "smtp_host":    "smtp.gmail.com:587",
        "from_address": "test@example.com",
        "app_password": "test-app-password",
        "recipients": {
            "p1":          "p1@example.com",
            "p2":          "p2@example.com",
            "p3":          "p3@example.com",
            "correlation": "corr@example.com",
        },
    },
    "ssh": {
        "user":          "testuser",
        "password":      "testpass",
        "sudo_password": "testsudo",
    },
    "redis": {
        "port":       6379,
        "cache_db":   0,
        "session_db": 1,
        "password":   "",
    },
    "slack": {"webhook_url": ""},
    "webhook": {
        "secret_token":   "test-secret-token-abc123",
        "auto_remediate": True,
    },
    "platform": {
        "name":          "test.example.com",
        "instance_name": "test-instance",
        "environment":   "test",
    },
}

# ── Inject test config before webhook.py loads ──────────────
import config_loader
config_loader._CONFIG = TEST_CONFIG     # bypasses file I/O entirely


# ── Fixtures ─────────────────────────────────────────────────

@pytest.fixture(scope="session")
def app():
    """Flask test application — created once per session."""
    import webhook
    webhook.app.config["TESTING"] = True
    # Override log file to /dev/null during tests
    webhook.LOG_FILE = os.devnull
    return webhook.app


@pytest.fixture
def client(app):
    """Fresh Flask test client for each test."""
    with app.test_client() as c:
        yield c


@pytest.fixture
def valid_auth():
    """Authorization header with the correct bearer token."""
    return {"Authorization": f"Bearer {TEST_CONFIG['webhook']['secret_token']}"}


@pytest.fixture
def firing_alert():
    """Minimal firing Alertmanager payload for a known remediation task."""
    return {
        "version": "4",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "fingerprint": "test-fingerprint-001",
                "labels": {
                    "alertname": "TestAlert",
                    "severity":  "p1",
                    "platform":  "nginx",
                    "remediation": "fpm-reload",
                },
                "annotations": {"summary": "Test alert firing"},
            }
        ],
    }


@pytest.fixture
def resolved_alert():
    """Minimal resolved Alertmanager payload."""
    return {
        "version": "4",
        "status": "resolved",
        "alerts": [
            {
                "status": "resolved",
                "fingerprint": "test-fingerprint-001",
                "labels": {
                    "alertname": "TestAlert",
                    "severity":  "p1",
                    "platform":  "nginx",
                    "remediation": "fpm-reload",
                },
                "annotations": {"summary": "Test alert resolved"},
            }
        ],
    }


@pytest.fixture
def tmp_config(tmp_path):
    """Write TEST_CONFIG to a temp file; return its path."""
    p = tmp_path / "config.json"
    p.write_text(json.dumps(TEST_CONFIG))
    return str(p)


@pytest.fixture(autouse=True)
def clear_dedup_state():
    """Reset in-memory dedup caches between tests to avoid cross-test pollution."""
    import webhook
    with webhook._lock:
        webhook._fired_alerts.clear()
        webhook._remediation_history.clear()
    yield
    with webhook._lock:
        webhook._fired_alerts.clear()
        webhook._remediation_history.clear()
