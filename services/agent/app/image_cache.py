"""
Disk cache for listing photos.

Listing CDNs block hotlinking, so every photo already goes through our proxy.
Caching that response to disk turns the second and later views into a local file
read: the grid stops waiting on an external CDN, and the origin gets hit once per
photo instead of once per viewer.

Kept deliberately simple — content-addressed files plus a size-capped LRU sweep.
The catalog holds ~10 photos per listing, so a few thousand listings is a few GB
at full resolution; the cap keeps that bounded.
"""
import asyncio
import hashlib
import logging
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "image-cache"
MAX_BYTES = 2 * 1024 * 1024 * 1024   # 2 GB
SWEEP_EVERY = 300                    # seconds between size checks
MAX_IMAGE_BYTES = 12 * 1024 * 1024   # refuse absurd payloads

BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

_last_sweep = 0.0
_inflight: Dict[str, asyncio.Event] = {}
_stats = {"hits": 0, "misses": 0, "errors": 0}


def _paths(url: str) -> Tuple[Path, Path]:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    base = CACHE_DIR / digest[:2] / digest
    return base.with_suffix(".bin"), base.with_suffix(".type")


def _read(url: str) -> Optional[Tuple[bytes, str]]:
    body_path, type_path = _paths(url)
    if not body_path.exists():
        return None
    try:
        content_type = type_path.read_text(encoding="utf-8").strip() if type_path.exists() else "image/jpeg"
        data = body_path.read_bytes()
        body_path.touch()  # bump mtime so the LRU sweep sees it as recently used
        return data, content_type
    except OSError:
        return None


def _write(url: str, data: bytes, content_type: str) -> None:
    body_path, type_path = _paths(url)
    try:
        body_path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp name then rename, so a concurrent reader never sees a
        # half-written file.
        tmp = body_path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(body_path)
        type_path.write_text(content_type, encoding="utf-8")
    except OSError as e:
        logger.warning(f"image cache write failed: {e}")


def _sweep_if_due() -> None:
    """Evict least-recently-used files once the cache exceeds its cap."""
    global _last_sweep
    now = time.time()
    if now - _last_sweep < SWEEP_EVERY:
        return
    _last_sweep = now
    try:
        files = [(p.stat().st_mtime, p.stat().st_size, p) for p in CACHE_DIR.rglob("*.bin")]
    except OSError:
        return
    total = sum(size for _, size, _ in files)
    if total <= MAX_BYTES:
        return
    files.sort(key=lambda t: t[0])  # oldest first
    freed = 0
    for _, size, path in files:
        if total - freed <= MAX_BYTES * 0.9:
            break
        try:
            path.unlink(missing_ok=True)
            path.with_suffix(".type").unlink(missing_ok=True)
            freed += size
        except OSError:
            continue
    logger.info(f"image cache swept: freed {freed // (1024*1024)}MB")


async def fetch_cached(url: str) -> Optional[Tuple[bytes, str, bool]]:
    """Returns (body, content_type, served_from_cache) or None when unavailable."""
    cached = _read(url)
    if cached:
        _stats["hits"] += 1
        return cached[0], cached[1], True

    # Collapse a stampede: the grid asks for the same photo from many cards at once.
    event = _inflight.get(url)
    if event is not None:
        try:
            await asyncio.wait_for(event.wait(), timeout=15)
        except asyncio.TimeoutError:
            pass
        again = _read(url)
        if again:
            _stats["hits"] += 1
            return again[0], again[1], True

    _inflight[url] = asyncio.Event()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(12.0, connect=4.0), follow_redirects=True
        ) as client:
            resp = await client.get(url, headers={"User-Agent": BROWSER_UA})
            if resp.status_code != 200:
                _stats["errors"] += 1
                return None
            content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0]
            if not content_type.startswith("image/"):
                _stats["errors"] += 1
                return None
            body = resp.content
            if len(body) > MAX_IMAGE_BYTES:
                _stats["errors"] += 1
                return None
            _write(url, body, content_type)
            _sweep_if_due()
            _stats["misses"] += 1
            return body, content_type, False
    except Exception as e:
        _stats["errors"] += 1
        logger.warning(f"image proxy error for {url}: {e!r}")
        return None
    finally:
        ev = _inflight.pop(url, None)
        if ev:
            ev.set()


def cache_stats() -> Dict[str, object]:
    try:
        files = list(CACHE_DIR.rglob("*.bin"))
        size = sum(p.stat().st_size for p in files)
    except OSError:
        files, size = [], 0
    served = _stats["hits"] + _stats["misses"]
    return {
        **_stats,
        "hit_rate": round(_stats["hits"] / served, 3) if served else 0.0,
        "cached_images": len(files),
        "size_mb": round(size / (1024 * 1024), 1),
        "cap_mb": MAX_BYTES // (1024 * 1024),
        "dir": str(CACHE_DIR),
    }
