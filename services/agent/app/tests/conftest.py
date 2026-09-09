"""Point every test at a throwaway database.

The catalog fixtures truncate the locations table. Before this file existed they
did that against services/agent/data/catalog.db and destroyed the real ingested
catalog, so the isolation is set up before app.store is ever imported.
"""
import os
import tempfile
from pathlib import Path

# Vertex AI is the production path, but reaching it needs cloud credentials and
# a live probe call. Tests must not depend on either, so the suite pins the
# API-key path; genai_client's Vertex branch is covered by monkeypatched unit
# tests in test_genai_client.py instead.
os.environ["USE_VERTEX_AI"] = "false"

_TMP_DB = Path(tempfile.gettempdir()) / "stagesight-test-catalog.db"
os.environ["STAGESIGHT_DB_PATH"] = str(_TMP_DB)
for suffix in ("", "-wal", "-shm"):
    Path(str(_TMP_DB) + suffix).unlink(missing_ok=True)

from app import store  # noqa: E402  (import after the env var is set)

store.init_db()
