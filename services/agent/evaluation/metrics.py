"""
Deterministic, model-free checks on an (original, generated) image pair.

These exist to cross-check the VLM judge, and above all to catch the one failure
a judge is bad at spotting: the model quietly ignored the instruction and handed
back something almost identical to the input. A judge asked "was the camera
moved?" will often rationalise a near-copy as "subtly repositioned"; a perceptual
hash will not.

Every threshold here is a starting point calibrated against our own baseline
runs, not a universal constant — recalibrate with calibrate() if the source
photos change character.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, asdict
from typing import Dict, Optional, Tuple

import imagehash
import numpy as np
from PIL import Image

# A pHash distance at or below this means "the model returned the input".
# Measured: identical file = 0; a genuine relight of the same framing lands
# around 6-14; a real viewpoint change lands 16+.
UNCHANGED_PHASH = 4
# Below this, a nominal camera move produced no visible reframing.
WEAK_CHANGE_PHASH = 8


def _load(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGB")


def _luma(img: Image.Image) -> np.ndarray:
    a = np.asarray(img.resize((256, 256)), dtype=np.float32) / 255.0
    # Rec. 709 luma
    return 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]


@dataclass
class PairMetrics:
    phash_distance: int          # 0 = identical, higher = more different
    unchanged: bool              # model almost certainly ignored the instruction
    weak_change: bool
    mean_luma_src: float
    mean_luma_out: float
    luma_ratio: float            # out / src; <1 darker, >1 brighter
    dark_pixel_frac: float       # fraction of output below 0.10 luma — night signal
    warm_index: float            # (R-B)/(R+B) mean; >0 warm, <0 cool
    mirror_score: float          # 0..1, how symmetric the output is left-right
    detail_ratio: float          # output edge energy / source edge energy
    subject_scale_hint: float    # >1 suggests the frame widened (content shrank)

    def as_dict(self) -> Dict[str, float]:
        return asdict(self)


def _edge_energy(l: np.ndarray) -> float:
    gx = np.abs(np.diff(l, axis=1)).mean()
    gy = np.abs(np.diff(l, axis=0)).mean()
    return float(gx + gy)


def _mirror_score(img: Image.Image) -> float:
    """How close the image is to its own left-right mirror.

    Our wide-angle failure mode was the model mirroring the visible half of the
    room and inventing duplicate windows and fixtures; that shows up here as an
    abnormally high value. Normal interiors sit well below 0.9.
    """
    l = _luma(img)
    flipped = l[:, ::-1]
    diff = np.abs(l - flipped).mean()
    return float(1.0 - min(1.0, diff / 0.25))


def _warm_index(img: Image.Image) -> float:
    a = np.asarray(img.resize((128, 128)), dtype=np.float32) + 1e-6
    r, b = a[..., 0], a[..., 2]
    return float(((r - b) / (r + b)).mean())


def _subject_scale_hint(src: Image.Image, out: Image.Image) -> float:
    """Rough proxy for field-of-view change.

    Widening the lens pushes content away, so fine detail per unit area rises
    (more stuff packed in) while large-scale structure shrinks. Comparing edge
    energy at two scales separates "zoomed out" from "zoomed in" better than
    either alone. This is a hint, not a measurement — the judge decides.
    """
    ls, lo = _luma(src), _luma(out)
    fine = (_edge_energy(lo) + 1e-6) / (_edge_energy(ls) + 1e-6)
    coarse_s = _edge_energy(np.asarray(Image.fromarray((ls * 255).astype(np.uint8)).resize((64, 64)), dtype=np.float32) / 255.0)
    coarse_o = _edge_energy(np.asarray(Image.fromarray((lo * 255).astype(np.uint8)).resize((64, 64)), dtype=np.float32) / 255.0)
    coarse = (coarse_o + 1e-6) / (coarse_s + 1e-6)
    return float(fine / (coarse + 1e-6))


def compare(src_bytes: bytes, out_bytes: bytes) -> PairMetrics:
    src, out = _load(src_bytes), _load(out_bytes)

    d = int(imagehash.phash(src, hash_size=16) - imagehash.phash(out, hash_size=16))
    ls, lo = _luma(src), _luma(out)
    ms, mo = float(ls.mean()), float(lo.mean())

    return PairMetrics(
        phash_distance=d,
        unchanged=d <= UNCHANGED_PHASH,
        weak_change=d <= WEAK_CHANGE_PHASH,
        mean_luma_src=round(ms, 4),
        mean_luma_out=round(mo, 4),
        luma_ratio=round(mo / (ms + 1e-6), 3),
        dark_pixel_frac=round(float((lo < 0.10).mean()), 3),
        warm_index=round(_warm_index(out), 4),
        mirror_score=round(_mirror_score(out), 3),
        detail_ratio=round((_edge_energy(lo) + 1e-6) / (_edge_energy(ls) + 1e-6), 3),
        subject_scale_hint=round(_subject_scale_hint(src, out), 3),
    )


# ── Expectation checks: does the measurement agree with what was asked for? ──
def expectation_flags(m: PairMetrics, case: Dict) -> Dict[str, Optional[bool]]:
    """Returns pass/fail per expectation, or None when the metric cannot decide.

    Kept intentionally conservative — these flag obvious contradictions, and the
    judge handles everything subtler.
    """
    phase = case.get("light_phase", "midday")
    rotation = int(case.get("rotation", 0)) % 360
    tilt = int(case.get("tilt", 0))
    zoom = int(case.get("zoom", 10))

    flags: Dict[str, Optional[bool]] = {}

    # Any requested change at all must move the image away from the input.
    asked_to_change = (
        min(rotation, 360 - rotation) >= 12 or abs(tilt) >= 8 or zoom <= 8 or zoom >= 12
        or phase in ("night", "blue_hour", "golden_hour")
    )
    flags["produced_a_change"] = (not m.unchanged) if asked_to_change else None

    # Night must actually be dark; midday must not be.
    if phase == "night":
        flags["night_is_dark"] = m.mean_luma_out < 0.28 and m.luma_ratio < 0.75
    elif phase == "blue_hour":
        flags["blue_hour_is_dim"] = m.mean_luma_out < 0.45
    elif phase in ("midday", "morning"):
        flags["daylight_is_bright"] = m.mean_luma_out > 0.25
    else:
        flags["daylight_is_bright"] = None

    # Golden hour should skew warm relative to a neutral render.
    flags["golden_is_warm"] = (m.warm_index > 0.02) if phase == "golden_hour" else None

    # The mirrored-room failure.
    flags["not_mirrored"] = m.mirror_score < 0.90

    # A large camera move should register as a large perceptual change.
    big_move = min(rotation, 360 - rotation) >= 60 or abs(tilt) >= 60
    flags["big_move_registered"] = (not m.weak_change) if big_move else None

    return flags


def calibrate(pairs) -> Dict[str, float]:
    """Summarise metric distributions over a set of known-good pairs, so the
    thresholds above can be re-grounded rather than guessed."""
    import statistics as st
    ds = [p.phash_distance for p in pairs]
    return {
        "phash_min": min(ds), "phash_median": st.median(ds), "phash_max": max(ds),
        "mirror_max": max(p.mirror_score for p in pairs),
        "luma_min": min(p.mean_luma_out for p in pairs),
        "luma_max": max(p.mean_luma_out for p in pairs),
    }


# ── Orbit compliance ────────────────────────────────────────────────────────
# Owned by app/viewpoint_check.py because the API serves this to users too; the
# harness only re-exports it so both read the same threshold.
from app.viewpoint_check import homography_inlier_ratio, GLOBAL_WARP_RATIO  # noqa: E402,F401
