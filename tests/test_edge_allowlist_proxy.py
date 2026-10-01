"""Wave B — edge allowlist + proxy definition smoke (no DNS/Vercel bill)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from bc_schedule_agent.edge_allowlist import is_email_allowed, parse_allowed_emails

ROOT = Path(__file__).resolve().parents[1]
EDGE = ROOT / "edge"


def test_edge_project_scaffold_present() -> None:
    assert (EDGE / "package.json").is_file()
    assert (EDGE / "auth.ts").is_file()
    assert (EDGE / "middleware.ts").is_file()
    assert (EDGE / "lib" / "allowlist.ts").is_file()
    assert (EDGE / "lib" / "proxy.ts").is_file()
    assert (EDGE / "app" / "[[...path]]" / "route.ts").is_file()
    assert (EDGE / "app" / "login" / "page.tsx").is_file()
    assert (EDGE / ".env.example").is_file()
    pkg = (EDGE / "package.json").read_text(encoding="utf-8")
    assert "next-auth" in pkg
    assert "google-auth-library" in pkg


def test_allowlist_fail_closed() -> None:
    assert parse_allowed_emails(None) == []
    assert parse_allowed_emails("") == []
    assert parse_allowed_emails(" Ada@X.com , bob@y.com ") == [
        "ada@x.com",
        "bob@y.com",
    ]
    assert is_email_allowed("ada@x.com", []) is False
    assert is_email_allowed(None, ["ada@x.com"]) is False
    assert is_email_allowed("intruder@evil.test", ["ada@x.com"]) is False
    assert is_email_allowed("Ada@X.com", ["ada@x.com"]) is True


def test_ts_allowlist_documents_fail_closed() -> None:
    text = (EDGE / "lib" / "allowlist.ts").read_text(encoding="utf-8")
    assert "Fail-closed" in text or "fail-closed" in text
    auth = (EDGE / "auth.ts").read_text(encoding="utf-8")
    assert "isEmailAllowed" in auth
    assert "parseAllowedEmails" in auth


def test_proxy_targets_cloud_run_not_ksai_scheduler() -> None:
    example = (EDGE / ".env.example").read_text(encoding="utf-8")
    assert "bc-schedule-agent-demo" in example
    assert "CLOUD_RUN_ORIGIN" in example
    assert "ALLOWED_EMAILS" in example
    assert "schedule-demo" in example
    proxy = (EDGE / "lib" / "proxy.ts").read_text(encoding="utf-8")
    assert "isEdgeOwnedPath" in proxy
    route = (EDGE / "app" / "[[...path]]" / "route.ts").read_text(encoding="utf-8")
    assert "getCloudRunIdToken" in route
    assert "CLOUD_RUN_ORIGIN" in route
    # Must not hard-wire live Scheduler host
    assert "app.kitchenstack-ai.com" not in route
    assert "/scheduler/generate" not in route


def test_edge_deploy_script_refuses_without_typed_yes() -> None:
    script = ROOT / "deploy" / "edge-vercel.sh"
    assert script.is_file()
    env = {**os.environ}
    env.pop("TYPED_YES", None)
    result = subprocess.run(
        ["bash", str(script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 2
    assert "REFUSE" in result.stdout
    assert "typed yes" in result.stdout.lower()
    assert "schedule-demo" in result.stdout


def test_dns_runbook_squarespace_first() -> None:
    doc = (ROOT / "deploy" / "dns-schedule-demo.md").read_text(encoding="utf-8")
    assert "Squarespace" in doc
    assert "schedule-demo.kitchenstack-ai.com" in doc
    assert "GCP Cloud DNS" in doc


def test_cloudrun_stays_no_public_invoker() -> None:
    script = (ROOT / "deploy" / "cloudrun-demo.sh").read_text(encoding="utf-8")
    assert "--no-allow-unauthenticated" in script
    edge_deploy = (ROOT / "deploy" / "edge-vercel.sh").read_text(encoding="utf-8")
    assert "allUsers" in edge_deploy  # named as do-not
    assert "do not bind allUsers" in edge_deploy.lower() or "remind" in edge_deploy.lower()


@pytest.mark.skipif(
    os.environ.get("EDGE_LIVE_SMOKE") != "1",
    reason="optional; set EDGE_LIVE_SMOKE=1 to curl Cloud Run anonymous 403",
)
def test_optional_direct_cloud_run_refused() -> None:
    origin = os.environ.get(
        "CLOUD_RUN_ORIGIN",
        "https://bc-schedule-agent-demo-mckmtx6pta-uc.a.run.app",
    )
    result = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", origin + "/"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() in {"403", "401"}
