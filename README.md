# StageSight

StageSight is a web application for previewing a filming location through realistic camera views before an in-person scout.

Choosing a space is one of the biggest bottlenecks for directors and screenwriters. Turning an imagined scene into a real place is also one of the hardest parts of making art. StageSight helps bridge that gap: it shows how a location may feel through a camera and recommends spaces that fit the screenplay.

The catalogue brings together as many real spaces as possible from multiple platforms, currently within South Korea. Users can adjust camera field of view, angle and time of day to explore more than the source photograph and get a practical sense of filming there. They can also paste or upload a screenplay, receive recommendations for each scene, and refine them through chat using budget and mood preferences.

[English workspace](https://stagesight-web-479126230193.us-central1.run.app/en) · [한국어](https://stagesight-web-479126230193.us-central1.run.app/)

## Features

- Browse real listings gathered from multiple Korean sources and open their original pages.
- Adjust camera angle, field of view and time of day, then compare the source photo with a Gemini preview.
- Research permits, noise and parking with Parallel Search and source links.
- Match each screenplay scene to available locations and refine the results by budget or mood through chat.

No account is required. Saved activity stays in your browser. AI images are illustrative, not measured geometry or guaranteed lighting. Confirm availability and permissions with the venue.

## Stack

Next.js, FastAPI, SQLite, Google Gemini (`google-genai`) and Parallel Search (`parallel-web`). Both services run on Google Cloud Run. The backend uses a custom Python workflow, not an ADK runtime.

The English version uses the same catalogue, screenplay matching, camera simulator and personal activity UI as the Korean version. Venue names, regions, specifications and source summaries are translated for display, while the original Korean records and source links remain unchanged for traceability.

## Run

Requires Node.js 20+ and Python 3.11+. Copy `.env.example` to `.env` and set `GEMINI_API_KEY` and `PARALLEL_API_KEY`.

```bash
python3 -m venv services/agent/.venv
services/agent/.venv/bin/pip install -r services/agent/requirements.txt
services/agent/.venv/bin/uvicorn app.main:app --app-dir services/agent --port 8080
```

In another terminal:

```bash
cd apps/web
npm install
npm run dev
```

Open `http://localhost:3000/en`. A fresh checkout has no catalogue. From the root, run `services/agent/.venv/bin/python services/crawler/worker.py --once` with the enabled providers' credentials. No synthetic listings are supplied.

## Verify and deploy

Run `.venv/bin/pytest app/tests/ -q` in `services/agent` and `npm run build` in `apps/web`.

From the root, `./infra/deploy.sh` deploys both services using an authenticated Google Cloud CLI. The catalogue is a build-time snapshot; refreshing it requires redeployment.

Code: [Apache-2.0](LICENSE). Third-party listings and photographs retain their original rights.
