"""Wave A — container + Cloud Run definition smoke (no live bill)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_present_and_serves_demo_module() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "bc_schedule_agent.demo_app" in text
    assert "HOST=0.0.0.0" in text
    assert "PORT=8080" in text
    assert "PYTHONPATH=/app/src" in text
    assert "pip install" not in text
    assert "beside ks-ai-backend" in text
    assert "No Neon" in text
    assert "No Wellington" in text


def test_cloudrun_service_shape_restricted() -> None:
    yaml = (ROOT / "deploy" / "cloudrun-demo.yaml").read_text(encoding="utf-8")
    assert "bc-schedule-agent-demo" in yaml
    assert "nexus-sovereign-engine" in yaml
    assert "us-central1" in yaml
    script = (ROOT / "deploy" / "cloudrun-demo.sh").read_text(encoding="utf-8")
    assert "--no-allow-unauthenticated" in script
    assert "TYPED_YES" in script
    assert "ks-ai-backend" in script  # named as neighbor, not merge


def test_deploy_script_refuses_without_typed_yes() -> None:
    script = ROOT / "deploy" / "cloudrun-demo.sh"
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


def test_demo_app_defaults_stay_local_loopback() -> None:
    from bc_schedule_agent import demo_app

    env_backup = {k: os.environ.get(k) for k in ("HOST", "PORT")}
    try:
        os.environ.pop("HOST", None)
        os.environ.pop("PORT", None)
        assert demo_app._default_host() == "127.0.0.1"
        assert demo_app._default_port() == 8765
        os.environ["HOST"] = "0.0.0.0"
        os.environ["PORT"] = "8080"
        assert demo_app._default_host() == "0.0.0.0"
        assert demo_app._default_port() == 8080
    finally:
        for key, value in env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.mark.skipif(
    os.environ.get("CONTAINER_BUILD_SMOKE") != "1",
    reason="optional; set CONTAINER_BUILD_SMOKE=1 when docker is allowed",
)
def test_optional_docker_build() -> None:
    result = subprocess.run(
        ["docker", "build", "-t", "bc-schedule-agent-demo:smoke", str(ROOT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
