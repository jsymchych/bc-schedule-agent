#!/usr/bin/env bash
# Define / deploy bc-schedule-agent-demo on Cloud Run.
# Spend gate: refuses unless TYPED_YES=yes (CTO chat typed yes).
#
# Shape (Wave A):
#   project: nexus-sovereign-engine
#   region:  us-central1
#   service: bc-schedule-agent-demo   # own service; not ks-ai-backend
#   ingress: all + --no-allow-unauthenticated  (not a stranger walk)
#   runtime: synthetic fixtures only; no Neon / Wellington / KS_AI merge
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT="${GCP_PROJECT:-nexus-sovereign-engine}"
REGION="${GCP_REGION:-us-central1}"
SERVICE="${CLOUD_RUN_SERVICE:-bc-schedule-agent-demo}"
IMAGE="${CLOUD_RUN_IMAGE:-${REGION}-docker.pkg.dev/${PROJECT}/bc-schedule-agent/demo:latest}"

if [[ "${TYPED_YES:-}" != "yes" ]]; then
  cat <<EOF
REFUSE: Cloud Run create/deploy waits for typed yes in CTO chat.
  Set TYPED_YES=yes to allow billable gcloud (after human typed yes).
  Service shape is defined in deploy/cloudrun-demo.yaml.
  Local path unchanged: python3 -m bc_schedule_agent.demo_app → 127.0.0.1:8765
EOF
  exit 2
fi

echo "[preflight] project=${PROJECT} region=${REGION} service=${SERVICE}"
gcloud config get-value project >/dev/null
gcloud auth list --filter=status:ACTIVE --format='value(account)' | head -1

DEPLOY_COMMON=(
  --project="${PROJECT}"
  --region="${REGION}"
  --platform=managed
  --port=8080
  --memory=512Mi
  --cpu=1
  --max-instances=2
  --timeout=300
  --concurrency=40
  --ingress=all
  --no-allow-unauthenticated
  --set-env-vars="HOST=0.0.0.0"
  --quiet
)

if command -v docker >/dev/null 2>&1; then
  echo "[build] docker build → ${IMAGE}"
  docker build -t "${IMAGE}" "${ROOT}"
  docker push "${IMAGE}"
  echo "[deploy] gcloud run deploy ${SERVICE} from image (no public invoker)"
  gcloud run deploy "${SERVICE}" --image="${IMAGE}" "${DEPLOY_COMMON[@]}"
else
  echo "[build] no local docker — Cloud Build via gcloud run deploy --source"
  echo "[deploy] gcloud run deploy ${SERVICE} from source (no public invoker)"
  gcloud run deploy "${SERVICE}" --source="${ROOT}" "${DEPLOY_COMMON[@]}"
fi

echo "[iam] do NOT bind allUsers run.invoker — edge (Wave B) holds invoke"
URL="$(gcloud run services describe "${SERVICE}" --project="${PROJECT}" --region="${REGION}" --format='value(status.url)' 2>/dev/null || true)"
echo "[done] ${SERVICE} up; url=${URL:-unknown}; direct anonymous walk should 403"
