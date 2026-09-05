"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";

export interface OrbitState {
  rotation: number; // 0-359, compass-style yaw around the subject
  tilt: number; // -90 (worm, looking up) … +90 (bird, straight down)
  zoom: number; // 1-20, maps to focal length
}

interface OrbitAnglePickerProps {
  value: OrbitState;
  onChange: (next: OrbitState) => void;
  thumbnail?: string;
  /** Drives the field-of-view wedge so zoom is visible on the rig, not just a number. */
  focalMm: number;
  batchMode: boolean;
  onBatchModeChange: (on: boolean) => void;
  batchCount: number;
  disabled?: boolean;
}

const R = 108; // wireframe sphere radius in SVG units
const CX = 130;
const CY = 130;

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const wrap360 = (v: number) => ((v % 360) + 360) % 360;

/**
 * Projects a point on the unit sphere to 2D, given the current viewing rotation.
 * The globe is drawn as if we are looking at it from slightly above the equator,
 * so latitude lines curve — that's what makes it read as a sphere rather than a disc.
 */
function project(lonDeg: number, latDeg: number, yawDeg: number) {
  const lon = ((lonDeg - yawDeg) * Math.PI) / 180;
  const lat = (latDeg * Math.PI) / 180;
  const x = Math.cos(lat) * Math.sin(lon);
  const y = Math.sin(lat);
  const z = Math.cos(lat) * Math.cos(lon);
  return { x: CX + x * R, y: CY - y * R, z };
}

function arcPath(
  points: Array<{ lon: number; lat: number }>,
  yaw: number
): { front: string; back: string } {
  let front = "";
  let back = "";
  let frontOpen = false;
  let backOpen = false;
  for (const p of points) {
    const { x, y, z } = project(p.lon, p.lat, yaw);
    const isFront = z >= 0;
    if (isFront) {
      front += `${frontOpen ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
      frontOpen = true;
      backOpen = false;
    } else {
      back += `${backOpen ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
      backOpen = true;
      frontOpen = false;
    }
  }
  return { front, back };
}

function meridian(lonDeg: number, yaw: number) {
  const pts = [];
  for (let lat = -90; lat <= 90; lat += 5) pts.push({ lon: lonDeg, lat });
  return arcPath(pts, yaw);
}

function parallel(latDeg: number, yaw: number) {
  const pts = [];
  for (let lon = 0; lon <= 360; lon += 5) pts.push({ lon, lat: latDeg });
  return arcPath(pts, yaw);
}

export const OrbitAnglePicker: React.FC<OrbitAnglePickerProps> = ({
  value,
  onChange,
  thumbnail,
  focalMm,
  batchMode,
  onBatchModeChange,
  batchCount,
  disabled = false,
}) => {
  const [dragging, setDragging] = useState(false);
  const last = useRef<{ x: number; y: number } | null>(null);
  const valueRef = useRef(value);
  valueRef.current = value;

  const onPointerDown = (e: React.PointerEvent) => {
    if (disabled) return;
    try {
      (e.target as Element).setPointerCapture?.(e.pointerId);
    } catch {
      // Capture is an optimisation; the window-level listeners below still track the drag.
    }
    last.current = { x: e.clientX, y: e.clientY };
    setDragging(true);
  };

  const onPointerMove = useCallback(
    (e: PointerEvent) => {
      if (!last.current) return;
      const dx = e.clientX - last.current.x;
      const dy = e.clientY - last.current.y;
      last.current = { x: e.clientX, y: e.clientY };
      const v = valueRef.current;
      onChange({
        ...v,
        // Rounded: the API takes integer degrees, and a fractional value 422s.
        rotation: Math.round(wrap360(v.rotation + dx * 0.8)),
        // Drag up ⇒ the camera rises ⇒ tilt increases. dy is negative upward,
        // so it subtracts — adding would invert the grab.
        tilt: Math.round(clamp(v.tilt - dy * 0.6, -90, 90)),
      });
    },
    [onChange]
  );

  const endDrag = useCallback(() => {
    last.current = null;
    setDragging(false);
  }, []);

  useEffect(() => {
    if (!dragging) return;
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", endDrag);
    window.addEventListener("pointercancel", endDrag);
    return () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", endDrag);
      window.removeEventListener("pointercancel", endDrag);
    };
  }, [dragging, onPointerMove, endDrag]);

  // The globe is a fixed reference frame and the CAMERA orbits it, so dragging
  // reads as "I am walking around the room", not "the room is spinning".
  // We look at the globe from a three-quarter angle so the home position
  // (rotation 0) sits readably to the side instead of dead-centre on the subject.
  const YAW = -40;
  const cam = project(value.rotation, value.tilt, YAW);
  const markerX = CX + (cam.x - CX) * 0.9;
  const markerY = CY + (cam.y - CY) * 0.9;
  const camBehind = cam.z < 0;

  const meridians = Array.from({ length: 12 }, (_, i) => meridian(i * 30, YAW));
  const parallels = [-60, -30, 0, 30, 60].map((l) => parallel(l, YAW));

  // Field-of-view wedge, aimed from the camera at the subject. Half-angle is the
  // real full-frame horizontal FOV: atan(18/f) — 16mm opens to ~97°, 85mm to ~24°.
  const aimAngle = Math.atan2(CY - markerY, CX - markerX);
  const aimLen = Math.hypot(CX - markerX, CY - markerY) || 1;
  const halfFov = Math.atan(18 / Math.max(8, focalMm));
  const reach = aimLen * 1.6;
  const wedge = [
    `M${markerX.toFixed(1)},${markerY.toFixed(1)}`,
    `L${(markerX + Math.cos(aimAngle - halfFov) * reach).toFixed(1)},${(markerY + Math.sin(aimAngle - halfFov) * reach).toFixed(1)}`,
    `L${(markerX + Math.cos(aimAngle + halfFov) * reach).toFixed(1)},${(markerY + Math.sin(aimAngle + halfFov) * reach).toFixed(1)}`,
    "Z",
  ].join("");

  const nudge = (dRot: number) =>
    onChange({ ...value, rotation: wrap360(value.rotation + dRot) });

  return (
    <div className="rounded-2xl bg-white/[0.04] border border-white/10 p-3">
      <p className="text-center text-[11px] font-semibold text-white/55 leading-snug mb-1">
        드래그해서 카메라 앵글을 바꿔보세요
      </p>

      <div className="relative flex items-center justify-center">
        {/* Left / right quick rotate */}
        <button
          onClick={() => nudge(-45)}
          disabled={disabled}
          aria-label="왼쪽으로 회전"
          className="absolute left-0 z-10 p-1.5 text-white/40 hover:text-white transition-colors disabled:opacity-25"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6" /></svg>
        </button>
        <button
          onClick={() => nudge(45)}
          disabled={disabled}
          aria-label="오른쪽으로 회전"
          className="absolute right-0 z-10 p-1.5 text-white/40 hover:text-white transition-colors disabled:opacity-25"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6" /></svg>
        </button>

        <svg
          viewBox="0 0 260 260"
          className={`w-full max-w-[230px] touch-none ${
            disabled ? "opacity-40" : dragging ? "cursor-grabbing" : "cursor-grab"
          }`}
          onPointerDown={onPointerDown}
        >
          {/* Back-facing wireframe (dimmer, drawn first) */}
          <g stroke="rgba(255,255,255,0.10)" fill="none" strokeWidth="0.7">
            {meridians.map((m, i) => <path key={`mb${i}`} d={m.back} />)}
            {parallels.map((p, i) => <path key={`pb${i}`} d={p.back} />)}
          </g>

          {/* Camera behind the subject — drawn under the thumbnail so depth reads */}
          {camBehind && (
            <g opacity="0.4">
              <path d={wedge} fill="rgba(255,255,255,0.07)" stroke="rgba(255,255,255,0.2)" strokeWidth="0.7" />
              <line x1={markerX} y1={markerY} x2={CX} y2={CY} stroke="rgba(255,255,255,0.4)" strokeWidth="1" strokeDasharray="3,3" />
              <circle cx={markerX} cy={markerY} r="13" fill="#0f172a" stroke="rgba(255,255,255,0.6)" strokeWidth="1.2" />
              <rect x={markerX - 7} y={markerY - 5} width="14" height="10" rx="2" fill="none" stroke="white" strokeWidth="1.1" />
            </g>
          )}

          {/* Subject thumbnail at the centre of the orbit */}
          <g>
            {thumbnail ? (
              <>
                <defs>
                  <clipPath id="orbitThumbClip">
                    <rect x={CX - 38} y={CY - 38} width="76" height="76" rx="10" />
                  </clipPath>
                </defs>
                <image
                  href={thumbnail}
                  x={CX - 38}
                  y={CY - 38}
                  width="76"
                  height="76"
                  preserveAspectRatio="xMidYMid slice"
                  clipPath="url(#orbitThumbClip)"
                />
                <rect
                  x={CX - 38} y={CY - 38} width="76" height="76" rx="10"
                  fill="none" stroke="rgba(255,255,255,0.35)" strokeWidth="1.5"
                />
              </>
            ) : (
              <rect
                x={CX - 38} y={CY - 38} width="76" height="76" rx="10"
                fill="rgba(255,255,255,0.08)" stroke="rgba(255,255,255,0.25)" strokeWidth="1.5"
              />
            )}
          </g>

          {/* Front-facing wireframe */}
          <g stroke="rgba(255,255,255,0.26)" fill="none" strokeWidth="0.8">
            {meridians.map((m, i) => <path key={`mf${i}`} d={m.front} />)}
            {parallels.map((p, i) => <path key={`pf${i}`} d={p.front} />)}
          </g>

          {/* Camera in front of the subject */}
          {!camBehind && (
            <>
              <path d={wedge} fill="rgba(255,255,255,0.10)" stroke="rgba(255,255,255,0.28)" strokeWidth="0.8" />
              <line
                x1={markerX} y1={markerY} x2={CX} y2={CY}
                stroke="rgba(255,255,255,0.5)" strokeWidth="1.2" strokeDasharray="3,3"
              />
              <text
                x={CX} y={CY + 56} textAnchor="middle"
                fill="rgba(255,255,255,0.5)" fontSize="9"
                fontFamily="var(--font-mono), monospace" letterSpacing="0.5"
              >
                {Math.round((halfFov * 360) / Math.PI)}° · {focalMm}mm
              </text>
              <g transform={`translate(${markerX}, ${markerY})`}>
                <circle r="15" fill="#0f172a" stroke="rgba(255,255,255,0.85)" strokeWidth="1.5" />
                <rect x="-8" y="-6" width="16" height="12" rx="2.5" fill="none" stroke="white" strokeWidth="1.3" />
                <circle r="3.2" fill="#ef4444" />
              </g>
            </>
          )}

          {/* Pole hints */}
          <text x={CX} y={CY - R - 8} textAnchor="middle" fill="rgba(255,255,255,0.3)" fontSize="9" fontFamily="var(--font-mono), monospace">버드아이</text>
          <text x={CX} y={CY + R + 15} textAnchor="middle" fill="rgba(255,255,255,0.3)" fontSize="9" fontFamily="var(--font-mono), monospace">웜즈아이</text>
        </svg>
      </div>

      {/* Batch generation */}
      <label className={`mt-1 flex items-center justify-center gap-2 cursor-pointer ${disabled ? "opacity-40 pointer-events-none" : ""}`}>
        <input
          type="checkbox"
          checked={batchMode}
          onChange={(e) => onBatchModeChange(e.target.checked)}
          className="w-4 h-4 rounded border-white/30 bg-white/10 accent-indigo-500 cursor-pointer"
        />
        <span className="text-xs font-semibold text-white/80">
          추천 앵글 {batchCount}종 한 번에 생성
        </span>
      </label>
    </div>
  );
};

/** Numeric readout row with a drag-anywhere-on-the-row scrub, like a DAW parameter. */
export const ScrubRow: React.FC<{
  label: string;
  value: number;
  suffix?: string;
  /** Overrides the numeric readout, e.g. "원본" at the default zoom. */
  display?: string;
  min: number;
  max: number;
  step?: number;
  wrap?: boolean;
  onChange: (v: number) => void;
  disabled?: boolean;
}> = ({ label, value, suffix = "", display, min, max, step = 1, wrap = false, onChange, disabled }) => {
  const [dragging, setDragging] = useState(false);
  const last = useRef<number | null>(null);
  const valueRef = useRef(value);
  valueRef.current = value;

  const move = useCallback(
    (e: PointerEvent) => {
      if (last.current === null) return;
      const dx = e.clientX - last.current;
      last.current = e.clientX;
      const span = max - min;
      let next = valueRef.current + (dx / 220) * span;
      next = wrap ? wrap360(next) : clamp(next, min, max);
      onChange(Math.round(next / step) * step);
    },
    [min, max, step, wrap, onChange]
  );

  const end = useCallback(() => {
    last.current = null;
    setDragging(false);
  }, []);

  useEffect(() => {
    if (!dragging) return;
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
    };
  }, [dragging, move, end]);

  const pct = wrap ? (wrap360(value) / 360) * 100 : ((value - min) / (max - min)) * 100;

  return (
    <div
      onPointerDown={(e) => {
        if (disabled) return;
        last.current = e.clientX;
        setDragging(true);
      }}
      className={`relative overflow-hidden rounded-xl bg-white/[0.06] border border-white/10 select-none touch-none ${
        disabled ? "opacity-40" : dragging ? "cursor-ew-resize border-white/30" : "cursor-ew-resize hover:border-white/20"
      }`}
    >
      {/* Fill track */}
      <div
        className="absolute inset-y-0 left-0 bg-white/[0.07] pointer-events-none transition-[width] duration-75"
        style={{ width: `${pct}%` }}
      />
      <div className="relative flex items-center justify-between px-3.5 py-2.5">
        <span className="text-xs font-semibold text-white/70">{label}</span>
        <span className="font-mono text-sm font-bold text-white tabular-nums">
          {display ?? `${value}${suffix}`}
        </span>
      </div>
    </div>
  );
};
