#!/usr/bin/env bash
# Deploy schedule-demo edge (Vercel) + grant Cloud Run invoker to edge SA.
# Spend gate: refuses unless TYPED_YES=yes (CTO chat typed yes).
#
# Project intent: NEW Vercel project for schedule-demo.kitchenstack-ai.com
#   — not the KS_AI / app. frontend project.
# DNS: Squarespace first (see dns-schedule-demo.md); then vercel --prod.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EDGE="${ROOT}/edge"
PROJECT_NAME="${VERCEL_PROJECT:-bc-schedule-agent-edge}"
CLOUD_RUN_SERVICE="${CLOUD_RUN_SERVICE:-bc-schedule-agent-demo}"
GCP_PROJECT="${GCP_PROJECT:-nexus-sovereign-engine}"
GCP_REGION="${GCP_REGION:-us-central1}"

if [[ "${TYPED_YES:-}" != "yes" ]]; then
  cat <<EOF
REFUSE: DNS cutover + edge prod deploy wait for typed yes in CTO chat.
  Set TYPED_YES=yes after human typed yes.
  Edge source: ${EDGE}
  Vercel project (intended): ${PROJECT_NAME}
  Proxy target: Cloud Run ${CLOUD_RUN_SERVICE} (${GCP_PROJECT}/${GCP_REGION})
  DNS runbook: ${ROOT}/deploy/dns-schedule-demo.md
  Confirm ALLOWED_EMAILS edits are on schedule-demo project — not only app.
EOF
  exit 2
fi

if [[ ! -d "${EDGE}" ]]; then
  echo "REFUSE: missing edge/ directory" >&2
  exit 1
fi

echo "[preflight] vercel project=${PROJECT_NAME} edge=${EDGE}"
command -v npx >/dev/null
command -v gcloud >/dev/null

cd "${EDGE}"
echo "[deploy] vercel deploy --prod (project ${PROJECT_NAME})"
npx vercel deploy --prod --yes --name="${PROJECT_NAME}"

if [[ -n "${EDGE_RUN_INVOKER_MEMBER:-}" ]]; then
  echo "[iam] bind run.invoker for ${EDGE_RUN_INVOKER_MEMBER} on ${CLOUD_RUN_SERVICE}"
  gcloud run services add-iam-policy-binding "${CLOUD_RUN_SERVICE}" \
    --project="${GCP_PROJECT}" \
    --region="${GCP_REGION}" \
    --member="${EDGE_RUN_INVOKER_MEMBER}" \
    --role="roles/run.invoker" \
    --quiet
else
  echo "[iam] skip — set EDGE_RUN_INVOKER_MEMBER=serviceAccount:…@….iam.gserviceaccount.com after SA exists"
fi

echo "[done] edge prod deploy attempted; confirm schedule-demo host + allowlist on THIS project"
echo "[remind] do not bind allUsers run.invoker on Cloud Run"
