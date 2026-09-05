#!/usr/bin/env bash
# StageSight — start every service for local development.
#
#   ./run.sh          API + crawler + web
#   ./run.sh --no-crawl   skip the crawler (the catalog already has listings)
#
# Three processes, deliberately separate:
#   8080  API      services/agent      FastAPI
#   ----  crawler  services/crawler    refreshes listings into the shared SQLite
#   3000  web      apps/web            Next.js
#
# The crawler and the API share services/agent/data/catalog.db over SQLite WAL,
# so a crawl never blocks a request. Ctrl-C stops all three.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"
LOGS="$ROOT/.logs"; mkdir -p "$LOGS"

CRAWL=1
[ "${1:-}" = "--no-crawl" ] && CRAWL=0

VENV="$ROOT/services/agent/.venv"
[ -x "$VENV/bin/python" ] || { echo "backend venv 없음. 아래를 먼저 실행하세요:
  cd services/agent && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }
[ -d "$ROOT/apps/web/node_modules" ] || { echo "web 의존성 없음. 먼저: npm install --prefix apps/web"; exit 1; }
[ -f "$ROOT/.env" ] || echo "경고: .env 가 없습니다. GEMINI_API_KEY 없이는 AI 시뮬레이터가 503을 반환합니다."

PIDS=()
cleanup() {
  echo; echo "종료 중..."
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  echo "정지 완료."
}
trap cleanup INT TERM EXIT

echo "▸ API      http://localhost:8080/docs"
( cd "$ROOT/services/agent" && exec "$VENV/bin/uvicorn" app.main:app --host 0.0.0.0 --port 8080 --reload ) \
  > "$LOGS/api.log" 2>&1 &
PIDS+=($!)

# Wait for the API before starting anything that talks to it.
for _ in $(seq 1 60); do
  [ "$(curl -s -m 2 -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/health || true)" = "200" ] && break
  sleep 1
done
if [ "$(curl -s -m 2 -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/health || true)" != "200" ]; then
  echo "API 기동 실패 — $LOGS/api.log 를 확인하세요."; exit 1
fi
echo "  live listings: $("$VENV/bin/python" -c "import json,urllib.request;print(json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/locations/stats'))['live'])" 2>/dev/null || echo '?')"
echo "  prompt:        $(curl -s -m 3 http://127.0.0.1:8080/api/simulate/prompt-version 2>/dev/null || echo '?')"
echo "  sources:       $(curl -s -m 3 http://127.0.0.1:8080/api/providers 2>/dev/null | "$VENV/bin/python" -c "
import json,sys
try:
    ps=json.load(sys.stdin)['providers']
    on=[p['provider'] for p in ps if p['enabled']]
    off=[p['provider'] for p in ps if not p['enabled']]
    print(f\"{len(on)} active ({', '.join(on)}) · {len(off)} awaiting permission\")
except Exception: print('?')" 2>/dev/null || echo '?')"

if [ "$CRAWL" = "1" ]; then
  echo "▸ crawler  15분 주기 · 모든 활성 공급처 (로그: .logs/crawler.log)"
  # Providers awaiting permission are refused by the registry, not by this flag.
  ( cd "$ROOT/services/agent" && exec "$VENV/bin/python" "$ROOT/services/crawler/worker.py" --interval 900 ) \
    > "$LOGS/crawler.log" 2>&1 &
  PIDS+=($!)
fi

echo "▸ web      http://localhost:3000"
( cd "$ROOT" && exec npm run dev --prefix apps/web ) > "$LOGS/web.log" 2>&1 &
PIDS+=($!)

for _ in $(seq 1 90); do
  [ "$(curl -s -m 2 -o /dev/null -w '%{http_code}' http://127.0.0.1:3000 || true)" = "200" ] && break
  sleep 1
done
echo
echo "준비 완료 — http://localhost:3000   (Ctrl-C 로 전체 종료)"
wait
