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

RUN pip install --no-cache-dir .

# Cloud Run injects PORT; bind all interfaces in-container only.
ENV HOST=0.0.0.0
ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8

EXPOSE 8080

# Writable runtime dirs for fixtures/history/parameters (seeded at process start).
RUN mkdir -p /app/artifacts/demo /app/artifacts/history /app/artifacts/parameters

CMD ["python3", "-m", "bc_schedule_agent.demo_app"]
