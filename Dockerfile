# BC schedule agent demo — Cloud Run packaging (synthetic fixtures only).
# Service: bc-schedule-agent-demo · project nexus-sovereign-engine · us-central1
# Own service beside ks-ai-backend. No Neon. No Wellington. No KS_AI merge.
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY fixtures ./fixtures
COPY rulesets ./rulesets
COPY DEMO.md ./

# Keep the repo layout on disk. parents[2] from src/bc_schedule_agent/*.py
# resolves to /app (fixtures/, rulesets/, artifacts/). A plain pip install
# would relocate the package under site-packages and break those paths.
ENV PYTHONPATH=/app/src

# Cloud Run injects PORT; bind all interfaces in-container only.
ENV HOST=0.0.0.0
ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8

EXPOSE 8080

# Writable runtime dirs for fixtures/history/parameters (seeded at process start).
RUN mkdir -p /app/artifacts/demo /app/artifacts/history /app/artifacts/parameters

CMD ["python3", "-m", "bc_schedule_agent.demo_app"]