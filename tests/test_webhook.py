"""
test_webhook.py — unit tests for the auto-remediation webhook service.

Covers:
  - Authentication middleware (Bearer token)
  - Endpoint availability (health, index)
  - Input validation (bad JSON, empty body, oversized)
  - Security: path traversal protection, script allowlist
  - Deduplication: 30-second cooldown window
  - Alert processing: firing, resolved, disabled auto_remediate
  - Thread safety: concurrent alert submissions
"""

import json
import time
import pytest
from unittest.mock import patch, MagicMock


# ═══════════════════════════════════════════════════════════
# AUTHENTICATION
# ═══════════════════════════════════════════════════════════

class TestAuthentication:

    def test_missing_auth_header_returns_401(self, client):
        """POST /webhook without any Authorization header must be rejected."""
        resp = client.post("/webhook", json={"alerts": []})
        assert resp.status_code == 401

    def test_wrong_token_returns_401(self, client):
        """POST /webhook with an incorrect Bearer token must be rejected."""
        resp = client.post(
            "/webhook",
            json={"alerts": []},
            headers={"Authorization": "Bearer wrong-token-xyz"}
        )
        assert resp.status_code == 401

    def test_malformed_auth_scheme_returns_401(self, client):
        """Basic auth scheme (not Bearer) must be rejected."""
        resp = client.post(
            "/webhook",
            json={"alerts": []},
            headers={"Authorization": "Basic dGVzdDp0ZXN0"}
        )
        assert resp.status_code == 401

    def test_correct_token_returns_200(self, client, valid_auth):
        """POST /webhook with the correct Bearer token must be accepted."""
        resp = client.post("/webhook", json={"alerts": []}, headers=valid_auth)
        assert resp.status_code == 200

    def test_get_requests_do_not_require_auth(self, client):
        """Health and index endpoints must be accessible without authentication."""
        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 200


# ═══════════════════════════════════════════════════════════
# ENDPOINTS — HEALTH & INDEX
# ═══════════════════════════════════════════════════════════

class TestEndpoints:

    def test_health_returns_ok_status(self, client):
        """GET /health must return JSON with status=ok."""
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "ok"

    def test_health_reports_auto_remediate_flag(self, client):
        """GET /health must include the current auto_remediate setting."""
        resp = client.get("/health")
        body = resp.get_json()
        assert "auto_remediate" in body
        assert isinstance(body["auto_remediate"], bool)

    def test_index_returns_html(self, client):
        """GET / must return HTML content."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"AIOps" in resp.data or b"Auto-Remediation" in resp.data

    def test_health_auto_remediate_true_by_default(self, client):
        """auto_remediate should be True per TEST_CONFIG."""
        resp = client.get("/health")
        assert resp.get_json()["auto_remediate"] is True


# ═══════════════════════════════════════════════════════════
# INPUT VALIDATION
# ═══════════════════════════════════════════════════════════

class TestInputValidation:

    def test_empty_body_returns_400(self, client, valid_auth):
        """POST /webhook with no body must return 400."""
        resp = client.post(
            "/webhook",
            data="",
            content_type="application/json",
            headers=valid_auth
        )
        assert resp.status_code == 400

    def test_invalid_json_returns_400(self, client, valid_auth):
        """POST /webhook with malformed JSON must return 400."""
        resp = client.post(
            "/webhook",
            data="not-json",
            content_type="application/json",
            headers=valid_auth
        )
        assert resp.status_code == 400

    def test_valid_empty_alerts_list_returns_200(self, client, valid_auth):
        """POST /webhook with an empty alerts list is valid (resolved state check)."""
        resp = client.post("/webhook", json={"alerts": []}, headers=valid_auth)
        assert resp.status_code == 200

    def test_response_includes_alert_count(self, client, valid_auth):
        """Response must echo back how many alerts were accepted."""
        payload = {"alerts": [
            {"status": "firing", "fingerprint": "fp1",
             "labels": {"alertname": "A"}, "annotations": {}}
        ]}
        resp = client.post("/webhook", json=payload, headers=valid_auth)
        body = resp.get_json()
        assert body.get("count") == 1


# ═══════════════════════════════════════════════════════════
# SECURITY — ALLOWLIST & PATH TRAVERSAL
# ═══════════════════════════════════════════════════════════

class TestSecurityAllowlist:

    def test_allowed_script_names_pass(self):
        """All four allowed script names must resolve to a safe path."""
        import webhook
        allowed = ["fpm-reload", "nginx-file-limit", "redis-flush-cache", "generic_triage"]
        for name in allowed:
            path = webhook._get_safe_script_path(name)
            assert path is not None, f"Expected {name} to be allowed"

    def test_unknown_script_name_blocked(self):
        """An unknown script name must be blocked (returns None)."""
        import webhook
        result = webhook._get_safe_script_path("unknown-script")
        assert result is None

    def test_path_traversal_attempt_blocked(self):
        """Path traversal via ../ must be blocked."""
        import webhook
        for evil in ["../etc/passwd", "../../root/.bashrc", "fpm-reload/../../../etc/cron.d/evil"]:
            result = webhook._get_safe_script_path(evil)
            assert result is None, f"Expected '{evil}' to be blocked"

    def test_empty_script_name_blocked(self):
        """Empty string script name must be blocked."""
        import webhook
        assert webhook._get_safe_script_path("") is None

    def test_script_with_extension_blocked(self):
        """Script names should not include .sh (allowlist has bare names)."""
        import webhook
        assert webhook._get_safe_script_path("fpm-reload.sh") is None

    def test_allowed_scripts_frozenset_has_4_entries(self):
        """The allowlist must contain exactly the 4 known remediation scripts."""
        import webhook
        assert len(webhook.ALLOWED_SCRIPTS) == 4
        assert "generic_triage" in webhook.ALLOWED_SCRIPTS


# ═══════════════════════════════════════════════════════════
# DEDUPLICATION
# ═══════════════════════════════════════════════════════════

class TestDeduplication:

    def test_first_alert_not_a_dedup_hit(self):
        """A fresh fingerprint must not trigger the dedup cooldown."""
        import webhook
        result = webhook._is_dedup_hit("brand-new-fingerprint-xyz")
        assert result is False

    def test_fired_alert_is_dedup_hit_within_window(self):
        """A fingerprint fired a moment ago must register as a dedup hit."""
        import webhook
        fp = "dedup-test-fingerprint"
        webhook._update_fired(fp)
        assert webhook._is_dedup_hit(fp) is True

    def test_expired_alert_is_not_dedup_hit(self):
        """A fingerprint older than DEDUP_WINDOW_SECONDS must not be a hit."""
        import webhook
        fp = "expired-fingerprint"
        with webhook._lock:
            webhook._fired_alerts[fp] = time.time() - (webhook.DEDUP_WINDOW_SECONDS + 5)
        assert webhook._is_dedup_hit(fp) is False

    def test_dedup_window_is_30_seconds(self):
        """DEDUP_WINDOW_SECONDS must be exactly 30."""
        import webhook
        assert webhook.DEDUP_WINDOW_SECONDS == 30


# ═══════════════════════════════════════════════════════════
# ALERT PROCESSING — FIRING
# ═══════════════════════════════════════════════════════════

class TestAlertFiring:

    def test_firing_alert_with_valid_script_executes(self, client, valid_auth, firing_alert):
        """A firing alert with a known remediation script should execute it."""
        with patch("webhook.subprocess.run") as mock_run, \
             patch("webhook.os.path.isfile", return_value=True):
            mock_run.return_value = MagicMock(returncode=0, stdout="PHP-FPM reloaded.", stderr="")
            resp = client.post("/webhook", json=firing_alert, headers=valid_auth)
            assert resp.status_code == 200
            # Give the background thread time to run
            time.sleep(0.2)
            mock_run.assert_called_once()

    def test_firing_alert_stores_history(self, client, valid_auth, firing_alert):
        """A successful remediation execution must store history for RCA email."""
        with patch("webhook.subprocess.run") as mock_run, \
             patch("webhook.os.path.isfile", return_value=True):
            mock_run.return_value = MagicMock(returncode=0, stdout="Done.", stderr="")
            client.post("/webhook", json=firing_alert, headers=valid_auth)
            time.sleep(0.2)
            import webhook
            with webhook._lock:
                # fingerprint from the fixture is "test-fingerprint-001"
                history = webhook._remediation_history.get("test-fingerprint-001")
            assert history is not None
            assert history["trigger"] == "fpm-reload"

    def test_firing_alert_with_unknown_script_is_blocked(self, client, valid_auth):
        """A firing alert whose remediation label is not in ALLOWED_SCRIPTS must be blocked."""
        evil_payload = {
            "alerts": [{
                "status": "firing",
                "fingerprint": "evil-fp",
                "labels": {"alertname": "Evil", "remediation": "evil-script", "platform": "nginx"},
                "annotations": {},
            }]
        }
        with patch("webhook.subprocess.run") as mock_run:
            client.post("/webhook", json=evil_payload, headers=valid_auth)
            time.sleep(0.2)
            mock_run.assert_not_called()

    def test_firing_alert_skipped_when_auto_remediate_disabled(self, client, valid_auth, firing_alert):
        """When auto_remediate=False, no script should execute."""
        import webhook
        original = webhook._is_auto_remediate_enabled
        webhook._cfg["webhook"]["auto_remediate"] = False
        try:
            with patch("webhook.subprocess.run") as mock_run, \
                 patch("webhook.os.path.isfile", return_value=True):
                client.post("/webhook", json=firing_alert, headers=valid_auth)
                time.sleep(0.2)
                mock_run.assert_not_called()
        finally:
            webhook._cfg["webhook"]["auto_remediate"] = True

    def test_second_identical_alert_within_dedup_window_skipped(self, client, valid_auth, firing_alert):
        """A duplicate alert within 30 seconds must not trigger a second script execution."""
        with patch("webhook.subprocess.run") as mock_run, \
             patch("webhook.os.path.isfile", return_value=True):
            mock_run.return_value = MagicMock(returncode=0, stdout="Done.", stderr="")
            # First alert
            client.post("/webhook", json=firing_alert, headers=valid_auth)
            time.sleep(0.1)
            # Second identical alert — must be deduped
            client.post("/webhook", json=firing_alert, headers=valid_auth)
            time.sleep(0.1)
            assert mock_run.call_count == 1, "Script should only execute once within dedup window"


# ═══════════════════════════════════════════════════════════
# ALERT PROCESSING — RESOLVED
# ═══════════════════════════════════════════════════════════

class TestAlertResolved:

    def test_resolved_alert_sends_email(self, client, valid_auth, resolved_alert):
        """A resolved alert must trigger an RCA summary email."""
        import webhook
        # Pre-seed history so resolved handler has something to work with
        with webhook._lock:
            webhook._remediation_history["test-fingerprint-001"] = {
                "trigger": "fpm-reload", "action": "fpm-reload",
                "start_time": "2026-05-02 10:00:00", "duration": 2.5,
                "status": "SUCCESS", "evidence": "PHP-FPM reloaded.", "platform": "nginx",
            }
        with patch("webhook._send_email") as mock_email, \
             patch("webhook._run_verify", return_value="php8.4-fpm running"):
            client.post("/webhook", json=resolved_alert, headers=valid_auth)
            time.sleep(0.3)
            mock_email.assert_called_once()
            subject, body = mock_email.call_args[0]
            assert "RESOLVED" in subject or "TestAlert" in subject

    def test_resolved_alert_clears_history(self, client, valid_auth, resolved_alert):
        """Processing a resolved alert must remove its fingerprint from history."""
        import webhook
        with webhook._lock:
            webhook._remediation_history["test-fingerprint-001"] = {
                "trigger": "fpm-reload", "action": "fpm-reload",
                "start_time": "2026-05-02 10:00:00", "duration": 1.0,
                "status": "SUCCESS", "evidence": "Done.", "platform": "nginx",
            }
        with patch("webhook._send_email"), \
             patch("webhook._run_verify", return_value="OK"):
            client.post("/webhook", json=resolved_alert, headers=valid_auth)
            time.sleep(0.3)
            with webhook._lock:
                remaining = webhook._remediation_history.get("test-fingerprint-001")
            assert remaining is None

    def test_resolved_alert_without_prior_history_sends_email(self, client, valid_auth, resolved_alert):
        """A resolved alert with no prior execution history must still send a summary email."""
        with patch("webhook._send_email") as mock_email, \
             patch("webhook._run_verify", return_value="uptime: OK"):
            client.post("/webhook", json=resolved_alert, headers=valid_auth)
            time.sleep(0.3)
            mock_email.assert_called_once()


# ═══════════════════════════════════════════════════════════
# THREAD SAFETY
# ═══════════════════════════════════════════════════════════

class TestThreadSafety:

    def test_concurrent_different_alerts_all_processed(self, app, valid_auth):
        """10 alerts with different fingerprints submitted concurrently must all be accepted."""
        import threading

        results = []
        lock = threading.Lock()

        def post_alert(fp):
            payload = {"alerts": [{
                "status": "firing",
                "fingerprint": fp,
                "labels": {"alertname": "ConcurrentTest", "remediation": "fpm-reload", "platform": "nginx"},
                "annotations": {},
            }]}
            with patch("webhook.subprocess.run") as m, \
                 patch("webhook.os.path.isfile", return_value=True):
                m.return_value = MagicMock(returncode=0, stdout="OK", stderr="")
                # Each thread gets its own client to avoid Flask ContextVar conflicts
                with app.test_client() as c:
                    r = c.post("/webhook", json=payload, headers=valid_auth)
                    with lock:
                        results.append(r.status_code)

        threads = [threading.Thread(target=post_alert, args=(f"concurrent-fp-{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 10, f"Only {len(results)}/10 threads completed"
        assert all(s == 200 for s in results), f"Not all requests were accepted: {results}"

    def test_dedup_dict_access_is_thread_safe(self):
        """Concurrent calls to _update_fired and _is_dedup_hit must not raise exceptions."""
        import webhook
        import threading
        errors = []

        def worker(fp):
            try:
                webhook._update_fired(fp)
                webhook._is_dedup_hit(fp)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker, args=(f"thread-fp-{i}",)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread safety errors: {errors}"
