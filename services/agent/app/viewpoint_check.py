"""
Did the camera actually move, or is the render a warp of the source?

Matched keypoints that are all explained by a single homography mean the output
is the input panned, cropped or untouched. Genuine parallax — a camera that
travelled — breaks that assumption and drives the inlier ratio down.

This exists because neither a person nor a VLM judge reliably tells those apart:
asked "did the camera orbit?", a judge looks at a plausible photograph of a room
and says yes. Measured across our own runs, 6 of 10 nominal 90-degree orbits were
a pure pan or entirely static while the judge scored them 1.7/5 — the judge knew
something was wrong but could not say what. This can.

Lives in `app/` rather than `evaluation/` because the API serves the result to
users; the evaluation harness imports it from here.
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
from PIL import Image
import io

logger = logging.getLogger(__name__)

# Above this, one global warp explains the output: a pan, a crop, or no move.
# Calibrated on our own baseline cells, where a deliberately static render sits
# at 0.93-1.00 and a genuine orbit lands near 0.57-0.72.
GLOBAL_WARP_RATIO = 0.70


def _gray(data: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(data)).convert("L").resize((512, 512)))


def homography_inlier_ratio(src: bytes, out: bytes) -> Optional[float]:
    """Fraction of matched keypoints explained by one global warp.

    Returns None when there are too few matches to decide — heavy relighting can
    destroy descriptors, and an extreme angle change can leave nothing to match.
    None must not be read as either a pass or a fail.
    """
    try:
        import cv2
    except ImportError:
        return None
    try:
        a, b = _gray(src), _gray(out)
        sift = cv2.SIFT_create(nfeatures=1500)
        ka, da = sift.detectAndCompute(a, None)
        kb, db = sift.detectAndCompute(b, None)
        if da is None or db is None or len(ka) < 20 or len(kb) < 20:
            return None

        raw = cv2.BFMatcher().knnMatch(da, db, k=2)
        good = [m for m, n in (p for p in raw if len(p) == 2) if m.distance < 0.75 * n.distance]
        if len(good) < 25:
            return None

        pa = np.float32([ka[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        pb = np.float32([kb[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        _, mask = cv2.findHomography(pa, pb, cv2.RANSAC, 4.0)
        return None if mask is None else round(float(mask.ravel().mean()), 3)
    except Exception as e:
        logger.debug(f"viewpoint check failed: {e}")
        return None


def camera_actually_moved(src: bytes, out: bytes) -> Optional[bool]:
    ratio = homography_inlier_ratio(src, out)
    return None if ratio is None else ratio <= GLOBAL_WARP_RATIO
