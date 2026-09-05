"""Point every test at a throwaway database.

The catalog fixtures truncate the locations table. Before this file existed they
did that against services/agent/data/catalog.db and destroyed the real ingested
catalog, so the isolation is set up before app.store is ever imported.
"""
import os
import tempfile
from pathlib import Path

_TMP_DB = Path(tempfile.gettempdir()) / "stagesight-test-catalog.db"
os.environ["STAGESIGHT_DB_PATH"] = str(_TMP_DB)
for suffix in ("", "-wal", "-shm"):
    Path(str(_TMP_DB) + suffix).unlink(missing_ok=True)

from app import store  # noqa: E402  (import after the env var is set)

store.init_db()
