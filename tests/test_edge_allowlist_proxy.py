"""Wave B — edge signed invite + proxy definition smoke (no DNS/Vercel bill)."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from bc_schedule_agent.invite_token import (
    invite_url,
    mint_invite,
    verify_token,
)

ROOT = Path(__file__).resolve().parents[1]
EDGE = ROOT / "edge"
SECRET = "test-invite-signing-secret-not-for-prod"


def test_edge_project_scaffold_present() -> None:
    assert (EDGE / "package.json").is_file()
    assert (EDGE / "middleware.ts").is_file()
    assert (EDGE / "lib" / "invite.ts").is_file()
    assert (EDGE / "lib" / "redeem.ts").is_file()
    assert (EDGE / "lib" / "session.ts").is_file()
    assert (EDGE / "lib" / "proxy.ts").is_file()
    assert (EDGE / "app" / "invite" / "route.ts").is_file()
    assert (EDGE / "app" / "[[...path]]" / "route.ts").is_file()
    assert (EDGE / "app" / "login" / "page.tsx").is_file()
    assert (EDGE / ".env.example").is_file()
    pkg = (EDGE / "package.json").read_text(encoding="utf-8")
    assert "next-auth" not in pkg
    assert "google-auth-library" in pkg
    assert "next" in pkg
    # Google OAuth admit path removed
    assert not (EDGE / "auth.ts").exists()
    assert not (EDGE / "lib" / "allowlist.ts").exists()


def test_invite_token_mint_verify_and_expiry() -> None:
    token = mint_invite(SECRET, label="prospect", ttl_seconds=3600, jti="jti-fixed-1")
    payload = verify_token(SECRET, token)
    assert payload["jti"] == "jti-fixed-1"
    assert payload["label"] == "prospect"
    url = invite_url("https://schedule-demo.kitchenstack-ai.com", token)
    assert url.startswith("https://schedule-demo.kitchenstack-ai.com/invite?t=")
    expired = mint_invite(
        SECRET, label="old", ttl_seconds=1, now=int(time.time()) - 10, jti="jti-old"
    )
    with pytest.raises(ValueError, match="expired"):
        verify_token(SECRET, expired)
    with pytest.raises(ValueError, match="bad signature"):
        verify_token(SECRET, token[:-1] + ("a" if token[-1] != "a" else "b"))


def test_mint_script_prints_url() -> None:
    script = ROOT / "scripts" / "mint_invite.py"
    assert script.is_file()
    result = subprocess.run(
        [
            "python3",
            str(script),
            "--label",
            "smoke",
            "--ttl-days",
            "1",
            "--base-url",
            "https://schedule-demo.kitchenstack-ai.com",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "INVITE_SIGNING_SECRET": SECRET},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    line = result.stdout.strip()
    assert line.startswith("https://schedule-demo.kitchenstack-ai.com/invite?t=")
    token = line.split("t=", 1)[1]
    verify_token(SECRET, token)


def test_env_documents_invite_not_google() -> None:
    example = (EDGE / ".env.example").read_text(encoding="utf-8")
    assert "INVITE_SIGNING_SECRET" in example
    assert "KV_REST_API_URL" in example
    assert "CLOUD_RUN_ORIGIN" in example
    assert "bc-schedule-agent-demo" in example
    assert "ALLOWED_EMAILS" not in example
    assert "GOOGLE_CLIENT" not in example
    invite_ts = (EDGE / "lib" / "invite.ts").read_text(encoding="utf-8")
    assert "jti" in invite_ts
    redeem = (EDGE / "lib" / "redeem.ts").read_text(encoding="utf-8")
    assert "claimInviteJti" in redeem
    login = (EDGE / "app" / "login" / "page.tsx").read_text(encoding="utf-8")
    assert "Google sign-in is not used" in login or "invite" in login.lower()


def test_proxy_targets_cloud_run_not_ksai_scheduler() -> None:
    proxy = (EDGE / "lib" / "proxy.ts").read_text(encoding="utf-8")
    assert "isEdgeOwnedPath" in proxy
    assert "/invite" in proxy
    route = (EDGE / "app" / "[[...path]]" / "route.ts").read_text(encoding="utf-8")
    assert "getCloudRunIdToken" in route
    assert "CLOUD_RUN_ORIGIN" in route
    assert "readSessionFromCookie" in route
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
    assert "invite" in doc.lower()


def test_cloudrun_stays_no_public_invoker() -> None:
    script = (ROOT / "deploy" / "cloudrun-demo.sh").read_text(encoding="utf-8")
    assert "--no-allow-unauthenticated" in script
    edge_deploy = (ROOT / "deploy" / "edge-vercel.sh").read_text(encoding="utf-8")
    assert "allUsers" in edge_deploy
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
