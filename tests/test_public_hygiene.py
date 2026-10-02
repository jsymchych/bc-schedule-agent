"""Wave C — public publish hygiene (no secrets / estate-only stranger docs)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Paths strangers (and public git) must be able to follow without an estate mount.
DOC_PATHS = (
    ROOT / "README.md",
    ROOT / "DEMO.md",
    ROOT / "edge" / "README.md",
    ROOT / "edge" / ".env.example",
)

ESTATE_ABS = re.compile(r"/Volumes/SovereignSSD/")
LIVE_CLOUDRUN_HASH = re.compile(r"bc-schedule-agent-demo-mckmtx6pta", re.I)
SECRET_ASSIGN = re.compile(
    r"(?im)^(INVITE_SIGNING_SECRET|SESSION_SECRET|AUTH_SECRET|GOOGLE_CLIENT_SECRET|"
    r"KV_REST_API_TOKEN|BLOB_READ_WRITE_TOKEN|GCP_SA_JSON)[ \t]*=[ \t]*(\S+)[ \t]*$"
)


def test_gitignore_blocks_secret_and_shelf_paths() -> None:
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "edge/.env" in text
    assert "edge/.env*.local" in text or ".env*.local" in text
    assert "Library/" in text
    assert "artifacts/history/" in text
    assert "artifacts/parameters/" in text
    assert "gcp-sa" in text or "service-account" in text


def test_docs_stranger_install_without_estate_only_cd() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    demo = (ROOT / "DEMO.md").read_text(encoding="utf-8")
    assert 'pip install -e ".[dev]"' in readme
    assert "python3 -m venv" in readme
    assert "schedule-demo.kitchenstack-ai.com" in readme
    assert "schedule-demo.kitchenstack-ai.com" in demo
    assert "one presenter" in readme.lower() or "one presenter" in demo.lower()
    # Estate absolute must not be the only / primary cd instruction
    assert "cd /Volumes/SovereignSSD/builds/bc-schedule-agent" not in demo
    assert "cd /Volumes/SovereignSSD/builds/bc-schedule-agent" not in readme


def test_publishable_docs_have_no_estate_abs_or_filled_secrets() -> None:
    for path in DOC_PATHS:
        text = path.read_text(encoding="utf-8")
        assert not ESTATE_ABS.search(text), f"estate absolute in {path.name}"
        assert not LIVE_CLOUDRUN_HASH.search(text), f"live Cloud Run hash in {path.name}"
        for match in SECRET_ASSIGN.finditer(text):
            value = match.group(2)
            assert value in {"", "…", "...", "<set-me>", "changeme"}, (
                f"filled secret-looking assign in {path.name}: {match.group(0)!r}"
            )


def test_smoke_report_paths_are_repo_relative() -> None:
    report = (ROOT / "artifacts" / "demo" / "smoke" / "smoke_report.json").read_text(
        encoding="utf-8"
    )
    assert "/Volumes/" not in report
    assert "ticket_6_real-ingest" not in report
