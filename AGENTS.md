# Repository Guidelines

## Project Structure & Module Organization

StageSight has three processes. `apps/web/` contains the Next.js/React/TypeScript frontend; routes live in `src/app`, UI in `src/components`, API helpers in `src/lib`, and shared types in `src/types`. `services/agent/` is the FastAPI backend: routes are in `app/main.py`, tools in `app/agent/tools`, schemas in `app/models`, and tests in `app/tests`. `services/crawler/worker.py` refreshes the shared SQLite catalog. Deployment files are under `infra/`; architecture and demo notes belong in `docs/`.

Treat `services/agent/data/`, image caches, `.next/`, `node_modules/`, `__pycache__/`, and `*.tsbuildinfo` as generated state, not source.

## Build, Test, and Development Commands

- `cd apps/web && npm install && npm run dev` starts the UI on port 3000.
- `cd apps/web && npm run build` performs the production build and TypeScript validation.
- `cd apps/web && npm run lint` runs the configured Next.js lint check.
- `cd services/agent && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt` prepares the backend.
- `cd services/agent && uvicorn app.main:app --reload --port 8080` starts the API and Swagger docs.
- `cd services/agent && pytest app/tests/ -v` runs backend tests; append `::test_name` for one case.
- `python services/crawler/worker.py --once` performs one catalog refresh after the backend environment is installed.

## Coding Style & Naming Conventions

Follow existing formatting: two-space indentation and double quotes in TypeScript/TSX; four spaces, snake_case functions, and type hints in Python. Use `PascalCase` for React components and Pydantic models, `camelCase` for frontend variables, and `test_<behavior>` for pytest cases. Reuse Tailwind/CSS tokens and centralize API access in `apps/web/src/lib/api.ts`. Prefer small, testable backend functions.

## Testing Guidelines

Pytest is configured in `services/agent/pytest.ini` with async support. Add regression tests beside affected backend features. Tests must use the temporary database from `app/tests/conftest.py`; never target the live catalog. There is no frontend unit-test suite, so run `npm run build` and manually verify changed routes.

## Commit & Pull Request Guidelines

History follows subjects such as `feat: add catalog sync` and `fix: unwrap async params`. Keep commits focused and imperative. Pull requests should explain behavior, list validation commands, link the issue, and include screenshots for UI changes. Call out schema, environment, crawler, or deployment changes.

## Security & Data Integrity

Copy `.env.example` to `.env`; never commit API keys. Do not add fabricated or fallback venue listings: catalog entries must represent real listings with source URLs. Preserve honest unknown values, robots rules, confidence labels, delisting safeguards, and upload validation.
