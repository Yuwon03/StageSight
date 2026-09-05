#!/usr/bin/env bash
# ==============================================================================
# StageSight → Google Cloud Run
#
#   ./infra/deploy.sh              backend then frontend
#   ./infra/deploy.sh backend      just the API
#   ./infra/deploy.sh frontend     just the web app (reuses the live API URL)
#
# Ordering is not cosmetic: Next.js inlines NEXT_PUBLIC_* at BUILD time, so the
# backend must exist and its URL be known before the frontend image is built.
#
# The catalogue is baked into the backend image (see Dockerfile.backend). Cloud
# Run has no persistent disk, so the deployed service serves the snapshot taken
# at build time; refreshing it means rebuilding, and the crawler stays a local
# process. That is a deliberate trade for the hackathon deployment.
# ==============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-pure-pact-477701-j8}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
BACKEND_SERVICE="stagesight-agent"
FRONTEND_SERVICE="stagesight-web"
TARGET="${1:-all}"

# Keys come from the repo-root .env and are set as Cloud Run env vars. They are
# never copied into an image — .gcloudignore excludes .env from the build.
[ -f .env ] && set -a && . ./.env && set +a

need() { [ -n "${!1:-}" ] || { echo "!! $1 가 .env 에 없습니다 — $2"; MISSING=1; }; }
MISSING=0
need GEMINI_API_KEY   "AI 프레임 시뮬레이터와 대본 매칭이 503을 반환합니다"
need PARALLEL_API_KEY "촬영 허가 조사(파트너 트랙 요건)가 동작하지 않습니다"
need TOURAPI_KEY      "한국관광공사 소스가 비활성화됩니다"
[ "$MISSING" = "1" ] && echo "   (계속 진행합니다 — 해당 기능만 비활성화됩니다)"

echo "=== StageSight → ${PROJECT_ID} (${REGION}) ==="

gcloud services enable \
  run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
  aiplatform.googleapis.com --project="${PROJECT_ID}" --quiet

REPO="stagesight"
gcloud artifacts repositories describe "${REPO}" \
  --project="${PROJECT_ID}" --location="${REGION}" >/dev/null 2>&1 || \
gcloud artifacts repositories create "${REPO}" \
  --project="${PROJECT_ID}" --location="${REGION}" --repository-format=docker \
  --description="StageSight images" --quiet

IMG="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}"
TAG="$(date +%Y%m%d-%H%M%S)"

deploy_backend() {
  echo "▸ 백엔드 이미지 빌드 (카탈로그 포함)…"
  gcloud builds submit "${ROOT}" \
    --project="${PROJECT_ID}" --region="${REGION}" \
    --tag="${IMG}/agent:${TAG}" \
    --gcs-log-dir="gs://${PROJECT_ID}_cloudbuild/logs" 2>/dev/null \
  || gcloud builds submit "${ROOT}" --project="${PROJECT_ID}" \
       --config=<(cat <<YAML
steps:
- name: gcr.io/cloud-builders/docker
  args: ['build','-f','infra/Dockerfile.backend','-t','${IMG}/agent:${TAG}','.']
images: ['${IMG}/agent:${TAG}']
options:
  machineType: E2_HIGHCPU_8
timeout: 1800s
YAML
)

  echo "▸ 백엔드 배포…"
  gcloud run deploy "${BACKEND_SERVICE}" \
    --image="${IMG}/agent:${TAG}" \
    --project="${PROJECT_ID}" --region="${REGION}" \
    --platform=managed --allow-unauthenticated \
    --min-instances=0 --max-instances=5 \
    --memory=2Gi --cpu=2 --timeout=300 \
    --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION},GEMINI_API_KEY=${GEMINI_API_KEY:-},PARALLEL_API_KEY=${PARALLEL_API_KEY:-},TOURAPI_KEY=${TOURAPI_KEY:-}" \
    --quiet
}

deploy_frontend() {
  BACKEND_URL="$(gcloud run services describe "${BACKEND_SERVICE}" \
    --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)')"
  [ -n "${BACKEND_URL}" ] || { echo "!! 백엔드 URL을 찾을 수 없습니다. 먼저 백엔드를 배포하세요."; exit 1; }
  echo "▸ 프론트엔드 이미지 빌드 (API=${BACKEND_URL})…"

  gcloud builds submit "${ROOT}" --project="${PROJECT_ID}" \
    --config=<(cat <<YAML
steps:
- name: gcr.io/cloud-builders/docker
  args: ['build','-f','infra/Dockerfile.frontend',
         '--build-arg','NEXT_PUBLIC_API_BASE_URL=${BACKEND_URL}',
         '-t','${IMG}/web:${TAG}','.']
images: ['${IMG}/web:${TAG}']
options:
  machineType: E2_HIGHCPU_8
timeout: 1800s
YAML
)

  echo "▸ 프론트엔드 배포…"
  gcloud run deploy "${FRONTEND_SERVICE}" \
    --image="${IMG}/web:${TAG}" \
    --project="${PROJECT_ID}" --region="${REGION}" \
    --platform=managed --allow-unauthenticated \
    --min-instances=0 --max-instances=5 \
    --memory=1Gi --port=3000 --quiet
}

case "${TARGET}" in
  backend)  deploy_backend ;;
  frontend) deploy_frontend ;;
  *)        deploy_backend; deploy_frontend ;;
esac

echo
echo "=== 배포 완료 ==="
[ "${TARGET}" != "frontend" ] && echo "  API : $(gcloud run services describe "${BACKEND_SERVICE}" --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)')"
[ "${TARGET}" != "backend"  ] && echo "  WEB : $(gcloud run services describe "${FRONTEND_SERVICE}" --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)')"
