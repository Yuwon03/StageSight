import os
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseModel):
    PROJECT_NAME: str = "StageSight Agent Service"
    PORT: int = int(os.getenv("PORT", "8080"))
    GOOGLE_CLOUD_PROJECT: str = os.getenv("GOOGLE_CLOUD_PROJECT", "pure-pact-477701-j8")
    GOOGLE_CLOUD_LOCATION: str = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    GEMINI_API_KEY: str | None = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    # Gemini is reached through Vertex AI on Google Cloud, authenticated by the
    # runtime service account rather than an API key. The API-key path is kept
    # as a fallback so local development and the evaluation harness still run
    # without cloud credentials, and so a Vertex outage is one env var away from
    # being routed around without a rebuild.
    USE_VERTEX_AI: bool = os.getenv("USE_VERTEX_AI", "true").strip().lower() in ("1", "true", "yes", "on")
    # NOT GOOGLE_CLOUD_LOCATION: that is the Cloud Run region (us-central1).
    # Verified 2026-09-10 — the Gemini 3.x models this project uses are published
    # only to Vertex's `global` endpoint; every one of them 404s in us-central1,
    # us-east5 and europe-west4, where only 2.5-era models are served.
    VERTEX_LOCATION: str = os.getenv("VERTEX_LOCATION", "global")
    PARALLEL_API_KEY: str | None = os.getenv("PARALLEL_API_KEY")
    GOOGLE_MAPS_API_KEY: str | None = os.getenv("GOOGLE_MAPS_API_KEY")
    ENV: str = os.getenv("ENV", "development")

settings = Settings()
