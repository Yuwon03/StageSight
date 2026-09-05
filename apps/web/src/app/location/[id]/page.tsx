"use client";

import React, { useState, useEffect, useMemo, useRef, use } from "react";
import { usePathname, useRouter } from "next/navigation";
import { KoreanLocation } from "@/types";
import {
  fetchLocationById,
  simulateAIFrame,
  resolveImageUrl,
  GeminiKeyMissingError,
  LicenseNoDerivativesError,
  researchFilmingPermits,
  PermitReport,
  BackendUnreachableError,
} from "@/lib/api";
import { OrbitAnglePicker, ScrubRow, OrbitState } from "@/components/simulator/OrbitAnglePicker";
import { FavoriteButton } from "@/components/common/FavoriteButton";
import { saveRender } from "@/lib/renderStore";
import {
  coordsForRegion,
  getSunTimes,
  getSunPosition,
  getLightPhase,
  parseWindowAzimuth,
  windowIncidence,
  directSunWindow,
  describeLight,
  bookingAdvisory,
  formatHour,
  PHASE_LABELS,
  LightPhase,
} from "@/lib/solar";
import {
  ChevronLeft,
  MapPin,
  Sun,
  Camera,
  Zap,
  Car,
  ExternalLink,
  ShieldCheck,
  Maximize2,
  Share,
  Heart,
  Sparkles,
  Loader2,
  CalendarDays,
  Video,
  Layers,
  MessageSquare,
} from "lucide-react";

// Zoom 1→16mm ultra-wide, 10→35mm normal, 20→85mm telephoto.
// Mirrors zoom_to_focal_mm() in services/agent/app/agent/tools/frame_simulator.py.
const zoomToFocal = (zoom: number) =>
  Math.round(16 * Math.pow(85 / 16, (Math.max(1, Math.min(20, zoom)) - 1) / 19));

const tiltLabel = (t: number, en = false) => {
  if (en) {
    if (t >= 78) return "Bird's-eye view";
    if (t >= 50) return "Steep high angle";
    if (t >= 22) return "High angle";
    if (t >= 8) return "Slightly elevated";
    if (t > -8) return "Eye level";
    if (t > -22) return "Slightly low angle";
    if (t > -50) return "Low angle";
    if (t > -78) return "Very low angle";
    return "Worm's-eye view";
  }
  if (t >= 78) return "버드 아이 뷰";
  if (t >= 50) return "급경사 하이 앵글";
  if (t >= 22) return "하이 앵글";
  if (t >= 8) return "약간 높은 시점";
  if (t > -8) return "아이 레벨";
  if (t > -22) return "약간 낮은 시점";
  if (t > -50) return "로우 앵글";
  if (t > -78) return "매우 낮은 앵글";
  return "웜즈 아이 뷰";
};

const tiltHint = (t: number, en = false) => {
  if (en) {
    if (t >= 78) return "A vertical overhead shot that reveals the full layout like a floor plan.";
    if (t >= 22) return "A view from above head height that makes furniture placement and movement paths easy to read.";
    if (t > -8) return "Standing eye level (about 1.6 m), offering the most natural and stable view of the space.";
    if (t > -50) return "A knee-height view looking upward, revealing the ceiling and making the space feel taller.";
    return "An extreme floor-level view that emphasises ceiling height and vertical scale.";
  }
  if (t >= 78) return "천장에서 수직으로 내려다보는 항공 샷 — 평면도처럼 전체 레이아웃이 보입니다.";
  if (t >= 22) return "머리 위 높이에서 내려다보는 앵글 — 가구 배치와 동선을 한눈에 파악할 수 있어요.";
  if (t > -8) return "서 있는 사람 눈높이(약 1.6m) — 가장 안정적이고 실제 체감에 가까운 구도입니다.";
  if (t > -50) return "무릎 높이에서 올려다보는 앵글 — 천장이 보이며 공간이 더 높고 웅장해 보여요.";
  return "바닥에 붙어 올려다보는 초근접 앵글 — 천장고와 수직감이 극대화됩니다.";
};

const rotationLabel = (r: number, en = false) => {
  const v = ((r % 360) + 360) % 360;
  if (v < 12 || v >= 348) return en ? "Original viewpoint" : "원본 시점";
  if (en) {
    const side = v < 180 ? "Right" : "Left";
    const amt = v < 180 ? v : 360 - v;
    if (amt < 55) return `${side} three-quarter view`;
    if (amt < 125) return `${side} side view`;
    return "Reverse viewpoint";
  }
  const side = v < 180 ? "오른쪽" : "왼쪽";
  const amt = v < 180 ? v : 360 - v;
  if (amt < 55) return `${side} 3/4 시점`;
  if (amt < 125) return `${side} 측면 시점`;
  return "반대편 시점";
};

// Zoom 10 = the photo's own framing, so the viewer opens on the real photo untouched.
const DEFAULT_ORBIT = { rotation: 0, tilt: 0, zoom: 10 };

interface GeneratedFrame {
  url: string;
  key: string;
  label: string;
  orbit: OrbitState;
  timeOfDay: number;
  dateStr: string;
  sourceImage: string;
}

// 12 curated rigs a location scout actually wants to see, as [rotation, tilt, zoom].
const BEST_ANGLES: Array<{ rotation: number; tilt: number; zoom: number; name: string }> = [
  { rotation: 0, tilt: 0, zoom: 6, name: "정면 와이드" },
  { rotation: 0, tilt: 90, zoom: 4, name: "평면도 뷰" },
  { rotation: 0, tilt: 30, zoom: 8, name: "하이 앵글 전경" },
  { rotation: 0, tilt: -35, zoom: 7, name: "로우 앵글 천장고" },
  { rotation: 45, tilt: 0, zoom: 8, name: "우측 3/4" },
  { rotation: 315, tilt: 0, zoom: 8, name: "좌측 3/4" },
  { rotation: 90, tilt: 10, zoom: 7, name: "우측 측면" },
  { rotation: 270, tilt: 10, zoom: 7, name: "좌측 측면" },
  { rotation: 180, tilt: 5, zoom: 6, name: "반대편 리버스" },
  { rotation: 45, tilt: 40, zoom: 9, name: "코너 하이 앵글" },
  { rotation: 0, tilt: -80, zoom: 5, name: "웜즈 아이" },
  { rotation: 20, tilt: 0, zoom: 15, name: "디테일 망원" },
];

const BEST_ANGLE_NAMES_EN: Record<string, string> = {
  "정면 와이드": "Front wide",
  "평면도 뷰": "Floor-plan view",
  "하이 앵글 전경": "High-angle wide",
  "로우 앵글 천장고": "Low angle / ceiling",
  "우측 3/4": "Right three-quarter",
  "좌측 3/4": "Left three-quarter",
  "우측 측면": "Right side",
  "좌측 측면": "Left side",
  "반대편 리버스": "Reverse viewpoint",
  "코너 하이 앵글": "Corner high angle",
  "웜즈 아이": "Worm's-eye view",
  "디테일 망원": "Telephoto detail",
};

const PHASE_COLORS: Record<LightPhase, string> = {
  night: "#334155",
  blue_hour: "#6366f1",
  golden_hour: "#f97316",
  morning: "#fcd34d",
  midday: "#fde68a",
  afternoon: "#fbbf24",
};

const TIMELINE_START = 5;
const TIMELINE_END = 22;

function todayStr(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function seasonLabelFor(dateStr: string, en = false): string {
  const m = parseInt(dateStr.split("-")[1] || "6", 10);
  if (m >= 6 && m <= 8) return en ? "Summer" : "여름";
  if (m === 12 || m <= 2) return en ? "Winter" : "겨울";
  if (m >= 3 && m <= 5) return en ? "Spring" : "봄";
  return en ? "Autumn" : "가을";
}

export default function LocationDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const router = useRouter();
  const en = usePathname().startsWith("/en/");
  const locale = en ? "en" : "ko";
  const t = (ko: string, english: string) => (en ? english : ko);
  const { id: locationId } = use(params);

  const [location, setLocation] = useState<KoreanLocation | null>(null);
  const [selectedImgIdx, setSelectedImgIdx] = useState(0);

  // Orbit camera rig
  const [orbit, setOrbit] = useState<OrbitState>({ ...DEFAULT_ORBIT });
  const [batchMode, setBatchMode] = useState(false);
  const [batchFrames, setBatchFrames] = useState<
    Array<{ name: string; url: string | null; failed?: boolean }>
  >([]);
  const [batchRunning, setBatchRunning] = useState(false);
  const [dateStr, setDateStr] = useState(todayStr());
  const [timeOfDay, setTimeOfDay] = useState(17.5);
  const [bookStart, setBookStart] = useState(14);
  const [bookEnd, setBookEnd] = useState(18);

  // Generated frame state. Nothing is generated until the user asks for it —
  // a render costs a Gemini call, so scrubbing a slider must never trigger one.
  const [render, setRender] = useState<{ url: string; key: string } | null>(null);
  const [history, setHistory] = useState<GeneratedFrame[]>([]);
  const [comparing, setComparing] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [aiUnavailable, setAiUnavailable] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [angleNotAchieved, setAngleNotAchieved] = useState(false);
  const [brokenImages, setBrokenImages] = useState<Set<number>>(new Set());

  // Two render tiers. Deliberately not labelled "빠름 / 정확": four image models
  // were scored over the same cells and the spread was 0.077 across a 4x price
  // range, with the most expensive scoring lowest. What actually differs is
  // speed and pixel count, so that is what the labels say.
  const [imageTier, setImageTier] = useState<"fast" | "detail">("fast");

  // Filming-permit research (Parallel Search API). On demand, not on load: it is
  // a live web-research call taking tens of seconds, and most visitors are
  // browsing rather than preparing a shoot.
  const [permits, setPermits] = useState<PermitReport | null>(null);
  const [permitsLoading, setPermitsLoading] = useState(false);
  const [permitsError, setPermitsError] = useState<string | null>(null);
  const frameCache = useRef<Map<string, string>>(new Map());

  useEffect(() => {
    if (locationId) {
      fetchLocationById(locationId).then(setLocation).catch(console.error);
    }
  }, [locationId]);

  const runPermitResearch = async () => {
    if (!location) return;
    setPermitsLoading(true);
    setPermitsError(null);
    try {
      const addr = location.citations?.[0]?.excerpt?.split("·")[0]?.trim() || location.region;
      setPermits(
        await researchFilmingPermits(
          location.name.replace(/\[.*?\]/, "").trim(),
          addr,
          location.region,
          undefined,
          locale
        )
      );
    } catch (err) {
      setPermitsError(
        err instanceof BackendUnreachableError
          ? t("백엔드 서버에 연결할 수 없습니다.", "The backend server is unavailable.")
          : t("허가 조사를 완료하지 못했습니다. PARALLEL_API_KEY 설정을 확인하세요.", "Permit research could not be completed. Check the PARALLEL_API_KEY configuration.")
      );
    } finally {
      setPermitsLoading(false);
    }
  };

  // ---- Solar derivations (real astronomy, per region + date) ----
  const solar = useMemo(() => {
    if (!location) return null;
    const { lat, lon } = coordsForRegion(location.region);
    const times = getSunTimes(dateStr, lat, lon);
    const pos = getSunPosition(dateStr, timeOfDay, lat, lon);
    const phase = getLightPhase(timeOfDay, times);
    const windowAz = parseWindowAzimuth(location.specs.window_direction);
    const incidence = windowIncidence(pos, windowAz);
    const sunWindow = directSunWindow(dateStr, lat, lon, windowAz, times);
    const description = describeLight(timeOfDay, times, pos, windowAz, locale);
    const advisory = bookingAdvisory(bookStart, bookEnd, times, sunWindow, seasonLabelFor(dateStr, en), locale);
    return { lat, lon, times, pos, phase, windowAz, incidence, sunWindow, description, advisory };
  }, [location, dateStr, timeOfDay, bookStart, bookEnd]);

  // Timeline gradient built from real light phases across the day
  const timelineGradient = useMemo(() => {
    if (!solar) return "";
    const stops: string[] = [];
    const span = TIMELINE_END - TIMELINE_START;
    for (let h = TIMELINE_START; h <= TIMELINE_END; h += 0.5) {
      const p = getLightPhase(h, solar.times);
      stops.push(`${PHASE_COLORS[p]} ${(((h - TIMELINE_START) / span) * 100).toFixed(1)}%`);
    }
    return `linear-gradient(to right, ${stops.join(", ")})`;
  }, [solar]);


  // The listing on the platform that actually takes bookings. Only a citation
  // the crawler collected counts; a URL is never assembled from an id.
  const sourceListing = useMemo(
    () => location?.citations?.find((c) => c.verification_status === "LIVE" && c.url) ?? location?.citations?.[0] ?? null,
    [location]
  );
  // Set when the user arrived from a script conversation, so they can get back
  // to it instead of hunting through history.
  const [returnChatId, setReturnChatId] = useState<string | null>(null);
  useEffect(() => {
    const c = new URLSearchParams(window.location.search).get("chat");
    if (c) setReturnChatId(c);
  }, []);

  const rawImage = location?.images[selectedImgIdx] || "";

  // Identifies one exact camera+light setup. Bucketed to match the backend cache
  // (15° rotation/tilt, 2-step zoom, whole hours) so near-identical setups reuse a render.
  const settingsKey = useMemo(() => {
    if (!solar) return "";
    const rb = Math.round((((orbit.rotation % 360) + 360) % 360) / 15) * 15;
    const tb = Math.round(orbit.tilt / 15) * 15;
    const zb = Math.round(orbit.zoom / 2) * 2;
    return `${rawImage}|r${rb}|t${tb}|z${zb}|${dateStr}@${Math.round(timeOfDay)}|${solar.phase}`;
  }, [rawImage, orbit, dateStr, timeOfDay, solar]);

  // Picking a different photo invalidates the render — the old frame belongs to
  // a different source image, so showing it would be misleading. Restoring a
  // frame from history is exempt: it sets the photo and the render together.
  const restoringRef = useRef(false);
  useEffect(() => {
    if (restoringRef.current) {
      restoringRef.current = false;
      return;
    }
    setRender(null);
    setAiError(null);
  }, [selectedImgIdx]);

  if (!location || !solar) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-white text-slate-500 font-sans">
        <p>{t("로케이션 정보를 불러오는 중입니다...", "Loading location details...")}</p>
      </div>
    );
  }

  const focal = zoomToFocal(orbit.zoom);
  const angleName = tiltLabel(orbit.tilt, en);
  const isDefaultZoom = orbit.zoom === DEFAULT_ORBIT.zoom;
  // Past ~60° the camera would have to stand outside a small room's walls, and
  // the model correctly declines rather than inventing an impossible viewpoint.
  const bigOrbit = (() => { const v = ((orbit.rotation % 360) + 360) % 360; return Math.min(v, 360 - v) >= 60; })();

  // The frame on screen is either the untouched source photo or a generated one.
  // There is no CSS approximation of angle, lens or light: a crop or a colour
  // filter would imply the space looks like that, and only a render can say so.
  const showingRender = render !== null;
  const isDirty = showingRender && render.key !== settingsKey;
  const originalSrc = resolveImageUrl(rawImage);
  // Holding the compare button drops back to the source photo without losing the render.
  const heroSrc = showingRender && !comparing ? render.url : originalSrc;

  const pushHistory = (frame: GeneratedFrame) =>
    setHistory((prev) => [frame, ...prev.filter((f) => f.key !== frame.key)].slice(0, 12));

  const restoreFrame = (f: GeneratedFrame) => {
    setSelectedImgIdxSilently(f.sourceImage);
    setOrbit(f.orbit);
    setTimeOfDay(f.timeOfDay);
    setDateStr(f.dateStr);
    frameCache.current.set(f.key, f.url);
    setRender({ url: f.url, key: f.key });
  };

  // Restoring a frame must point the viewer back at the photo it was made from,
  // without the photo-change effect wiping the render we are about to set.
  const setSelectedImgIdxSilently = (src: string) => {
    const idx = location.images.indexOf(src);
    if (idx >= 0 && idx !== selectedImgIdx) {
      restoringRef.current = true;
      setSelectedImgIdx(idx);
    }
  };

  const generate = async () => {
    if (!rawImage || !solar || generating || aiUnavailable) return;
    const key = settingsKey;
    const cached = frameCache.current.get(key);
    if (cached) {
      setRender({ url: cached, key });
      return;
    }
    setGenerating(true);
    setAiError(null);
    try {
      const result = await simulateAIFrame({
        image_url: rawImage,
        rotation: orbit.rotation,
        tilt: orbit.tilt,
        zoom: orbit.zoom,
        time_label: formatHour(timeOfDay),
        light_phase: solar.phase,
        phase_description: solar.description,
        window_direction: location.specs.window_direction,
        date_label: dateStr,
        sun_altitude_deg: Math.round(solar.pos.altitudeDeg * 10) / 10,
        space_category: location.category,
        location_id: location.id,
        image_tier: imageTier,
      });
      frameCache.current.set(key, result.image_data_url);
      setRender({ url: result.image_data_url, key });
      // Only claim a failure when the server actually measured one.
      setAngleNotAchieved(result.camera_moved === false);
      pushHistory({
        url: result.image_data_url,
        key,
        label: `${tiltLabel(orbit.tilt, en)} · ${formatHour(timeOfDay)}`,
        orbit: { ...orbit },
        timeOfDay,
        dateStr,
        sourceImage: rawImage,
      });
      // A render is work the user did; it belongs to them, not to this page
      // session. Saved to IndexedDB rather than the localStorage blob because a
      // single PNG would exhaust that quota. Best-effort: a storage failure
      // must not break the viewer.
      void saveRender({
        locationId: location.id,
        locationName: location.name,
        region: location.region,
        image: result.image_data_url,
        settings: {
          rotation: orbit.rotation,
          tilt: orbit.tilt,
          zoom: orbit.zoom,
          focalMm: result.focal_length_mm,
          timeLabel: formatHour(timeOfDay),
          lightPhase: solar.phase,
          dateLabel: dateStr,
        },
        cameraMoved: result.camera_moved ?? null,
      }).catch(() => {});
    } catch (err) {
      if (err instanceof LicenseNoDerivativesError) {
        // The source licences this photograph for display but forbids altering
        // it. Say so plainly rather than showing a generic failure.
        setAiError(
          t("이 사진은 출처 표시 후 게시만 허용되고 변경이 금지된 자료라, AI 재생성을 할 수 없습니다.", "This source permits attributed display but prohibits modifications, so an AI preview cannot be generated.")
        );
        setGenerating(false);
        return;
      }
      if (err instanceof GeminiKeyMissingError) {
        setAiUnavailable(true);
      } else {
        setAiError(t("생성에 실패했어요. 잠시 후 다시 시도해주세요.", "Generation failed. Please try again shortly."));
        console.error(err);
      }
    } finally {
      setGenerating(false);
    }
  };


  // Fire all 12 rigs concurrently; each card fills in the moment its render lands.
  const runBatch = async () => {
    if (!rawImage || batchRunning) return;
    setBatchRunning(true);
    setBatchFrames(BEST_ANGLES.map((a) => ({ name: en ? BEST_ANGLE_NAMES_EN[a.name] : a.name, url: null })));
    await Promise.all(
      BEST_ANGLES.map(async (a, i) => {
        try {
          const r = await simulateAIFrame({
            image_url: rawImage,
            rotation: a.rotation,
            tilt: a.tilt,
            zoom: a.zoom,
            time_label: formatHour(timeOfDay),
            light_phase: solar.phase,
            phase_description: solar.description,
            window_direction: location.specs.window_direction,
            date_label: dateStr,
            sun_altitude_deg: Math.round(solar.pos.altitudeDeg * 10) / 10,
            space_category: location.category,
          });
          setBatchFrames((prev) => {
            const next = [...prev];
            next[i] = { name: en ? BEST_ANGLE_NAMES_EN[a.name] : a.name, url: r.image_data_url };
            return next;
          });
        } catch (err) {
          if (err instanceof GeminiKeyMissingError) setAiUnavailable(true);
          setBatchFrames((prev) => {
            const next = [...prev];
            next[i] = { name: en ? BEST_ANGLE_NAMES_EN[a.name] : a.name, url: null, failed: true };
            return next;
          });
        }
      })
    );
    setBatchRunning(false);
  };
  const bookingHours = Array.from({ length: TIMELINE_END - 6 + 1 }, (_, i) => i + 6);
  const pct = (h: number) =>
    `${(((h - TIMELINE_START) / (TIMELINE_END - TIMELINE_START)) * 100).toFixed(1)}%`;

  const advisoryTone = {
    good: "bg-emerald-500/15 border-emerald-400/30 text-emerald-100",
    warn: "bg-orange-500/15 border-orange-400/30 text-orange-100",
    info: "bg-sky-500/15 border-sky-400/30 text-sky-100",
  }[solar.advisory.tone];

  return (
    <div lang={locale} className="min-h-screen bg-white text-slate-900 font-sans selection:bg-indigo-100">
      {/* Navigation Header */}
      <header className="sticky top-0 z-40 bg-white/90 backdrop-blur-md border-b border-gray-200 px-4 md:px-8 py-3.5 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <button
            onClick={() => router.back()}
            className="p-2 hover:bg-gray-100 rounded-full transition-colors flex items-center space-x-2 text-slate-900 font-bold"
          >
            <ChevronLeft className="w-5 h-5" />
            <span>{t("목록으로", "Back to catalogue")}</span>
          </button>
          {/* Arriving from a script conversation is a round trip: without this
              the only way back is browser history, which loses the thread. */}
          {returnChatId && (
            <a
              href={`${en ? "/en" : "/"}?tab=script&chat=${returnChatId}`}
              className="flex items-center gap-1.5 px-3.5 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-bold rounded-full transition-colors"
            >
              <MessageSquare className="w-4 h-4" />
              <span className="hidden sm:inline">{t("대화로 돌아가기", "Back to conversation")}</span>
            </a>
          )}
        </div>
        <div className="hidden md:flex items-center space-x-2 text-sm font-bold text-slate-700 truncate max-w-[40%]">
          <span className="truncate">{location.name.replace(/\[.*?\]/, "").trim()}</span>
        </div>
        <div className="flex items-center space-x-2">
          <button className="p-2 hover:bg-gray-100 rounded-full flex items-center space-x-2 text-slate-700 font-semibold">
            <Share className="w-4 h-4" />
            <span className="hidden sm:inline">{t("공유", "Share")}</span>
          </button>
          <button className="p-2 hover:bg-gray-100 rounded-full flex items-center space-x-2 text-slate-700 font-semibold">
            <Heart className="w-4 h-4" />
            <span className="hidden sm:inline">{t("저장", "Save")}</span>
          </button>
        </div>
      </header>

      {/* ===================== FULL-BLEED IMMERSIVE VIEWER ===================== */}
      <section className="relative w-full h-[calc(100dvh-62px)] min-h-[600px] bg-slate-950 overflow-hidden select-none">
        {/* Blurred fill so the wide viewport is filled without cropping the photo.
            Listing photos are usually portrait (e.g. 1125x2000) while this section is
            ultra-wide, so object-cover would hide most of the space. */}
        <div className="absolute inset-0 overflow-hidden" aria-hidden>
          <img
            src={heroSrc}
            alt=""
            className="w-full h-full object-cover scale-125 blur-3xl opacity-45"
          />
        </div>
        <div className="absolute inset-0 bg-slate-950/45" aria-hidden />

        {/* The photo, complete. Padded on the right so the console doesn't cover it. */}
        <div
          className={`absolute inset-0 flex items-center justify-center px-4 pt-14 pb-24 sm:pl-6 transition-[padding] duration-300 ${
            history.length > 0 ? "sm:pr-[540px]" : "sm:pr-[420px]"
          }`}
        >
          {/* Direct flex child of a definite-size box, so max-h-full actually
              resolves — an intermediate wrapper breaks the percentage chain and
              the photo overflows the viewport. */}
          <img
            key={showingRender ? render.key : rawImage}
            src={heroSrc}
            alt={location.name}
            className="max-w-full max-h-full object-contain rounded-xl shadow-2xl animate-in fade-in duration-500"
          />
        </div>

        {/* Vignette for overlay legibility */}
        <div className="absolute inset-0 pointer-events-none bg-gradient-to-t from-black/70 via-transparent to-black/50" />

        {/* Generating overlay */}
        {generating && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/40 backdrop-blur-[2px] z-10">
            <div className="flex items-center space-x-3 px-5 py-3 bg-slate-900/85 rounded-2xl border border-white/10 text-white text-sm font-bold">
              <Loader2 className="w-5 h-5 animate-spin text-indigo-300" />
              <span>{t(`${angleName} · ${formatHour(timeOfDay)}의 빛으로 생성 중...`, `Generating ${angleName} at ${formatHour(timeOfDay)}...`)}</span>
            </div>
          </div>
        )}

        {/* Top-left: title block */}
        <div className="absolute top-5 left-5 md:left-8 max-w-[60%] space-y-2 text-white drop-shadow-md">
          <div className="flex items-center flex-wrap gap-2">
            <span className="px-2.5 py-1 bg-white/15 backdrop-blur rounded-full text-[11px] font-bold border border-white/20">
              {location.category}
            </span>
          </div>
          <h1 className="text-2xl md:text-4xl font-extrabold tracking-tight leading-tight">
            {location.name.replace(/\[.*?\]/, "").trim()}
          </h1>
          <div className="flex items-center flex-wrap gap-3 text-xs md:text-sm font-semibold text-white/85">
            <span className="flex items-center gap-1"><MapPin className="w-4 h-4" />{location.region}</span>
            {location.specs.area_pyeong > 0 && <span>{en ? `${location.specs.area_sqm} m²` : `${location.specs.area_pyeong}평`}</span>}
            {location.specs.ceiling_height_m > 0 && <span>{t("천고", "Ceiling")} {location.specs.ceiling_height_m}m</span>}
            <span>{location.specs.window_direction}</span>
          </div>
          <div className="pt-1">
            <FavoriteButton location={location} showLabel locale={locale} />
          </div>
        </div>

        {/* Top-right: viewfinder metadata */}
        <div className="absolute top-5 right-5 md:right-8 text-right font-mono text-[11px] md:text-xs font-bold text-white/90 drop-shadow space-y-1">
          <div className="flex items-center justify-end space-x-2">
            <span className="w-2 h-2 bg-red-500 rounded-full animate-pulse" />
            <span>{showingRender ? (isDirty ? "OUTDATED" : "GENERATED") : "ORIGINAL"}</span>
          </div>
          <div>LENS {isDefaultZoom ? t("원본", "ORIGINAL") : `${focal}mm`} · ROT {orbit.rotation}° · TILT {orbit.tilt}°</div>
          <div>{formatHour(timeOfDay)} · {en ? ({ night: "Night", blue_hour: "Blue hour", golden_hour: "Golden hour", morning: "Morning", midday: "Midday", afternoon: "Afternoon" }[solar.phase]) : PHASE_LABELS[solar.phase]}</div>
          <div>SUN {Math.max(0, Math.round(solar.pos.altitudeDeg))}° / AZ {Math.round(solar.pos.azimuthDeg)}°</div>
        </div>

        {/* Bottom-left: thumbnails + render-mode label */}
        <div className="absolute bottom-5 left-5 md:left-8 space-y-2.5 z-10">
          <div className="flex items-center space-x-2 max-w-[52vw] overflow-x-auto pb-1">
            {location.images.map((img, i) =>
              brokenImages.has(i) ? null : (
                <button
                  key={i}
                  onClick={() => setSelectedImgIdx(i)}
                  className={`relative w-20 h-14 md:w-24 md:h-16 shrink-0 rounded-lg overflow-hidden transition-all border-2 ${
                    selectedImgIdx === i
                      ? "border-white scale-105 shadow-lg"
                      : "border-white/30 opacity-60 hover:opacity-100"
                  }`}
                >
                  <img
                    src={resolveImageUrl(img)}
                    alt=""
                    loading="lazy"
                    className="w-full h-full object-cover"
                    onError={() => setBrokenImages((prev) => new Set(prev).add(i))}
                  />
                </button>
              )
            )}
          </div>
          <div className="flex items-center gap-2">
            <div className="inline-flex items-center space-x-1.5 px-2.5 py-1 bg-black/45 backdrop-blur rounded-full text-[10px] font-bold text-white/85 border border-white/15">
              <Sparkles className="w-3 h-3" />
              <span>
                {comparing
                  ? t("원본 매물 사진 (비교 중)", "Original listing photo (comparing)")
                  : showingRender
                  ? isDirty
                    ? t("이전 설정으로 생성된 이미지 · 다시 생성 필요", "Image from previous settings · regenerate required")
                    : t("Gemini 생성 이미지", "Gemini-generated image")
                  : t("원본 매물 사진", "Original listing photo")}
              </span>
            </div>

            {/* Hold to drop back to the source photo — the fastest way to judge a render */}
            {showingRender && (
              <button
                onPointerDown={() => setComparing(true)}
                onPointerUp={() => setComparing(false)}
                onPointerLeave={() => setComparing(false)}
                onPointerCancel={() => setComparing(false)}
                className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[10px] font-bold border transition-colors ${
                  comparing
                    ? "bg-white text-slate-900 border-white"
                    : "bg-black/45 backdrop-blur text-white/85 border-white/15 hover:bg-black/65"
                }`}
              >
                <Layers className="w-3 h-3" />
                <span>{comparing ? t("원본 표시 중", "Showing original") : t("길게 눌러 원본 비교", "Hold to compare original")}</span>
              </button>
            )}
          </div>
        </div>

        {/* Generated frames stack up immediately left of the console */}
        {history.length > 0 && (
          <div className="hidden sm:flex absolute bottom-5 right-[436px] md:right-[448px] top-16 z-20 flex-col items-end gap-2 overflow-y-auto pr-0.5">
            <span className="sticky top-0 px-2 py-0.5 bg-black/55 backdrop-blur rounded-full font-mono text-[9px] uppercase tracking-wider text-white/60">
              {t("생성", "Generated")} {history.length}
            </span>
            {history.map((f) => {
              const active = render?.key === f.key && !isDirty;
              return (
                <button
                  key={f.key}
                  onClick={() => restoreFrame(f)}
                  title={`${f.label} · ${zoomToFocal(f.orbit.zoom)}mm`}
                  className={`group relative w-[92px] shrink-0 rounded-lg overflow-hidden border-2 transition-all ${
                    active
                      ? "border-white shadow-lg"
                      : "border-white/25 opacity-75 hover:opacity-100 hover:border-white/60"
                  }`}
                >
                  <img src={f.url} alt={f.label} className="w-full aspect-[4/3] object-cover" />
                  <span className="absolute inset-x-0 bottom-0 px-1.5 py-1 bg-gradient-to-t from-black/85 to-transparent text-[9px] font-bold text-white text-left leading-tight">
                    {f.label}
                  </span>
                </button>
              );
            })}
          </div>
        )}

        {/* ============ Bottom-right: CONTROL CONSOLE ============ */}
        <div className="absolute bottom-5 right-5 md:right-8 w-[calc(100%-2.5rem)] sm:w-[400px] max-h-[calc(100%-2.5rem)] z-20 flex flex-col">
          <div className="bg-slate-900/80 backdrop-blur-xl border border-white/12 rounded-2xl p-4 md:p-5 space-y-4 text-white shadow-2xl overflow-y-auto">

            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2 text-sm font-extrabold">
                <Camera className="w-4 h-4 text-indigo-300" />
                <span>{t("카메라 · 조명 시뮬레이터", "Camera · lighting simulator")}</span>
              </div>
              {showingRender && (
                <button
                  onClick={() => setRender(null)}
                  className="text-[11px] font-bold text-white/55 hover:text-white transition-colors"
                >
                  {t("원본 보기", "View original")}
                </button>
              )}
            </div>
            {aiUnavailable && (
              <p className="text-[11px] font-semibold text-amber-200/90 -mt-2">
                {t("GEMINI_API_KEY가 설정되지 않아 생성 기능을 쓸 수 없어요. 원본 사진만 표시됩니다.", "GEMINI_API_KEY is not configured, so generation is unavailable. Only the original photo is shown.")}
              </p>
            )}
            {aiError && (
              <p className="text-[11px] font-semibold text-orange-200/90 -mt-2">{aiError}</p>
            )}

            {/* Orbit rig — drag the globe, or scrub the three readouts */}
            <OrbitAnglePicker
              value={orbit}
              onChange={setOrbit}
              thumbnail={resolveImageUrl(rawImage)}
              focalMm={focal}
              batchMode={batchMode}
              onBatchModeChange={setBatchMode}
              batchCount={BEST_ANGLES.length}
              disabled={batchRunning}
              locale={locale}
            />

            <div className="space-y-1.5">
              <ScrubRow
                label={t("회전", "Rotation")} value={orbit.rotation} suffix="°" min={0} max={360} wrap
                onChange={(v) => setOrbit({ ...orbit, rotation: v })} disabled={batchRunning}
              />
              <ScrubRow
                label={t("상하 각도", "Tilt")} value={orbit.tilt} suffix="°" min={-90} max={90}
                onChange={(v) => setOrbit({ ...orbit, tilt: v })} disabled={batchRunning}
              />
              <ScrubRow
                label={t("줌", "Zoom")} value={orbit.zoom} min={1} max={20}
                display={isDefaultZoom ? t("원본", "Original") : `${focal}mm`}
                onChange={(v) => setOrbit({ ...orbit, zoom: v })} disabled={batchRunning}
              />
              <p className="px-1 pt-0.5 text-[11px] leading-relaxed font-semibold text-white/65">
                <span className="text-white/85">
                  {angleName} · {rotationLabel(orbit.rotation, en)} · {isDefaultZoom ? t("원본 화각", "Original field of view") : `${focal}mm`}
                </span>
                {" — "}{tiltHint(orbit.tilt, en)}
                {bigOrbit && (
                  <span className="text-amber-200/90">
                    {" "}{t("큰 각도로 도는 구도는 넓은 공간(야외·대형 스튜디오)에서 잘 나오고, 좁은 실내는 카메라가 벽 밖으로 나가 원본과 비슷하게 나올 수 있어요.", "Large rotations work best in spacious locations. In a small interior, the camera may hit a wall and the result can remain close to the original.")}
                  </span>
                )}
              </p>
            </div>

            {/* Date + season quick chips */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-[11px] font-bold text-white/60">
                <span className="flex items-center gap-1"><CalendarDays className="w-3.5 h-3.5" /> {t("촬영 날짜", "Shoot date")}</span>
                <span className="text-white/85">
                  {seasonLabelFor(dateStr, en)} · {t("일출", "Sunrise")} {formatHour(solar.times.sunrise)} / {t("일몰", "Sunset")} {formatHour(solar.times.sunset)}
                </span>
              </div>
              <div className="flex items-center gap-1.5">
                <input
                  type="date"
                  value={dateStr}
                  onChange={(e) => e.target.value && setDateStr(e.target.value)}
                  className="flex-1 px-2.5 py-1.5 bg-white/10 border border-white/15 rounded-lg text-xs font-bold text-white [color-scheme:dark] focus:outline-none focus:border-indigo-400"
                />
                {[
                  { label: t("여름", "Summer"), date: `${new Date().getFullYear()}-06-21` },
                  { label: t("겨울", "Winter"), date: `${new Date().getFullYear()}-12-21` },
                ].map((s) => (
                  <button
                    key={s.label}
                    onClick={() => setDateStr(s.date)}
                    className={`px-2.5 py-1.5 rounded-lg text-xs font-bold transition-colors ${
                      dateStr === s.date ? "bg-white text-slate-900" : "bg-white/10 text-white/75 hover:bg-white/20"
                    }`}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Time-of-day timeline */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-[11px] font-bold text-white/60">
                <span className="flex items-center gap-1"><Sun className="w-3.5 h-3.5" /> {t("시간대", "Time of day")}</span>
                <span className="px-2 py-0.5 bg-white/15 rounded-md text-white font-mono">{formatHour(timeOfDay)}</span>
              </div>

              <div className="relative pt-1 pb-4">
                {/* Phase gradient track */}
                <div className="h-2.5 rounded-full overflow-hidden" style={{ background: timelineGradient }} />

                {/* Booking window bracket */}
                <div
                  className="absolute top-0 h-[18px] border-x-2 border-t-2 border-white/80 rounded-t-sm pointer-events-none"
                  style={{ left: pct(bookStart), width: `calc(${pct(bookEnd)} - ${pct(bookStart)})` }}
                />

                {/* Sunrise / sunset ticks */}
                {[solar.times.sunrise, solar.times.sunset].map((t, i) => (
                  <div key={i} className="absolute -bottom-0 -translate-x-1/2 text-[9px] font-mono font-bold text-white/60" style={{ left: pct(t) }}>
                    <div className="w-px h-2 bg-white/60 mx-auto mb-0.5" />
                    {formatHour(t)}
                  </div>
                ))}

                {/* Range input on top */}
                <input
                  type="range"
                  min={TIMELINE_START}
                  max={TIMELINE_END}
                  step={0.25}
                  value={timeOfDay}
                  onChange={(e) => setTimeOfDay(parseFloat(e.target.value))}
                  className="absolute top-0.5 left-0 w-full h-3 appearance-none bg-transparent cursor-pointer
                    [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:h-4
                    [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-white [&::-webkit-slider-thumb]:shadow-lg
                    [&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-slate-900
                    [&::-moz-range-thumb]:w-4 [&::-moz-range-thumb]:h-4 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:bg-white [&::-moz-range-thumb]:border-2 [&::-moz-range-thumb]:border-slate-900"
                />
              </div>

              {/* Light description */}
              <p className="text-[11.5px] leading-relaxed font-semibold text-white/85">
                {solar.description}
              </p>
            </div>

            {/* Render tier. Labels state what was measured — a render time and a
                pixel count — not "accurate", because four image models scored
                within 0.077 of each other and the dearest scored lowest. */}
            <div className="flex items-center gap-1 p-1 rounded-xl bg-white/[0.06] border border-white/10">
              {([
                ["fast", t("빠르게", "Faster"), t("약 15초 · 1.4K", "About 15 sec · 1.4K")],
                ["detail", t("크게", "Larger"), t("약 40초 · 2.7K", "About 40 sec · 2.7K")],
              ] as const).map(([key, label, hint]) => (
                <button
                  key={key}
                  onClick={() => setImageTier(key)}
                  className={`flex-1 px-3 py-2 rounded-lg text-center transition-colors ${
                    imageTier === key ? "bg-white text-slate-900" : "text-white/70 hover:text-white"
                  }`}
                >
                  <div className="text-xs font-bold">{label}</div>
                  <div className={`text-[10px] font-medium ${imageTier === key ? "text-slate-500" : "text-white/40"}`}>
                    {hint}
                  </div>
                </button>
              ))}
            </div>

            {/* Generate — nothing calls the model until this is pressed */}
            <button
              onClick={generate}
              disabled={generating || aiUnavailable || batchRunning || (showingRender && !isDirty)}
              className={`w-full py-3 font-extrabold text-sm rounded-xl transition-all flex items-center justify-center gap-2 disabled:cursor-default ${
                showingRender && !isDirty
                  ? "bg-white/10 text-white/50"
                  : "bg-indigo-500 hover:bg-indigo-400 text-white disabled:opacity-50"
              }`}
            >
              {generating ? (
                <><Loader2 className="w-4 h-4 animate-spin" /> {t("생성 중...", "Generating...")}</>
              ) : showingRender && !isDirty ? (
                <>{t("현재 설정으로 생성 완료", "Generated with current settings")}</>
              ) : (
                <><Sparkles className="w-4 h-4" /> {isDirty ? t("변경한 설정으로 다시 생성", "Regenerate with changed settings") : t("이 설정으로 생성", "Generate with these settings")}</>
              )}
            </button>
            {angleNotAchieved && !isDirty && !generating && (
              <p className="-mt-2 px-1 text-[11px] font-semibold text-amber-200/90">
                {t("이 각도는 이 공간에서 구현되지 않았어요 — 좁은 공간에서는 카메라가 그만큼 이동할 수 없어 원본과 비슷한 구도로 나옵니다. 각도를 줄이거나 다른 사진으로 시도해보세요.", "This angle could not be achieved in the space. In a narrow room, the camera cannot move that far, so the composition remains close to the original. Try a smaller rotation or another photo.")}
              </p>
            )}
            {isDirty && !generating && (
              <p className="-mt-2 px-1 text-[11px] font-semibold text-amber-200/90">
                {t("설정을 바꿨어요. 화면은 아직 이전 생성 결과입니다.", "Settings changed. The viewer is still showing the previous result.")}
              </p>
            )}

            {batchMode && (
              <button
                onClick={runBatch}
                disabled={batchRunning || aiUnavailable}
                className="w-full py-2.5 bg-indigo-500 hover:bg-indigo-400 disabled:opacity-50 text-white font-bold text-xs rounded-xl transition-colors flex items-center justify-center gap-2"
              >
                {batchRunning ? (
                  <><Loader2 className="w-4 h-4 animate-spin" /> {t(`${BEST_ANGLES.length}종 생성 중...`, `Generating ${BEST_ANGLES.length} angles...`)}</>
                ) : (
                  <><Sparkles className="w-4 h-4" /> {t(`추천 앵글 ${BEST_ANGLES.length}종 생성`, `Generate ${BEST_ANGLES.length} recommended angles`)}</>
                )}
              </button>
            )}

            {/* Where this listing actually lives.
                A booking form here would be theatre: StageSight does not take
                reservations, and the only honest action is to hand the scout
                over to the platform that does. The URL is the one the crawler
                collected, never a guessed one. */}
            <div className="space-y-2 pt-3 border-t border-white/10">
              <div className={`px-3 py-2.5 rounded-xl border text-[11.5px] leading-relaxed font-semibold ${advisoryTone}`}>
                {solar.advisory.text}
              </div>

              {/* Filming-permit research — Parallel Search API.
                  A scout's second question after "does it look right?" is
                  "can I actually shoot here?", and that answer is not on any
                  rental listing: it lives in council bylaws and national noise
                  law. Every claim below is shown with the source it came from,
                  because an unsourced permit answer is worse than none. */}
              <div className="space-y-2 pt-3 border-t border-white/10">
                <button
                  onClick={runPermitResearch}
                  disabled={permitsLoading}
                  className="w-full py-2.5 px-3 rounded-xl bg-white/10 hover:bg-white/15 border border-white/15 text-xs font-bold text-white transition-colors flex items-center justify-center gap-2 disabled:opacity-60"
                >
                  {permitsLoading ? (
                    <><Loader2 className="w-3.5 h-3.5 animate-spin" />{t("촬영 허가 조건 조사 중…", "Researching filming permit requirements...")}</>
                  ) : (
                    <><ShieldCheck className="w-3.5 h-3.5" />{permits ? t("허가 조건 다시 조사", "Research permits again") : t("촬영 허가 조건 조사", "Research filming permits")}</>
                  )}
                </button>

                {permitsError && (
                  <div className="px-3 py-2 rounded-xl bg-rose-500/15 border border-rose-400/30 text-[11px] font-semibold text-rose-100">
                    {permitsError}
                  </div>
                )}

                {permits && (
                  <div className="space-y-1.5 animate-in fade-in duration-300">
                    {/* A blank field means no retrieved source stated the rule.
                        Saying so beats leaving a gap that reads as "no limit". */}
                    {permits.note && (
                      <div className="px-3 py-2 rounded-xl bg-amber-500/12 border border-amber-400/25 text-[10.5px] leading-relaxed font-semibold text-amber-100">
                        {permits.note}
                      </div>
                    )}
                    {([
                      [t("허가", "Permits"), permits.permit_requirements],
                      [t("시간 제한", "Filming hours"), permits.curfew_hours],
                      [t("소음", "Noise"), permits.noise_limits],
                      [t("주차·상하차", "Parking / loading"), permits.parking_and_loading],
                    ] as const).map(([label, value]) =>
                      value ? (
                        <div key={label} className="px-3 py-2 rounded-xl bg-white/5 border border-white/10">
                          <div className="text-[10px] font-bold text-white/50 mb-0.5">{label}</div>
                          <div className="text-[11px] leading-relaxed text-white/85">{value}</div>
                        </div>
                      ) : (
                        <div key={label} className="px-3 py-2 rounded-xl bg-white/[0.03] border border-white/5">
                          <div className="text-[10px] font-bold text-white/35 mb-0.5">{label}</div>
                          <div className="text-[11px] text-white/40">{t("조사된 출처에 관련 규정이 없었습니다", "No relevant rule was established by the retrieved sources")}</div>
                        </div>
                      )
                    )}

                    {permits.citations.length > 0 && (
                      <div className="pt-1">
                        <div className="text-[10px] font-bold text-white/50 mb-1">
                          {t(`출처 ${permits.citations.length}건`, `${permits.citations.length} sources`)}
                        </div>
                        <div className="space-y-1">
                          {permits.citations.slice(0, 5).map((c, i) => (
                            <a
                              key={i}
                              href={c.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="flex items-start gap-1.5 text-[10.5px] text-indigo-200 hover:text-indigo-100 transition-colors"
                            >
                              <ExternalLink className="w-3 h-3 mt-0.5 shrink-0" />
                              <span className="line-clamp-1">{c.title || c.url}</span>
                            </a>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {sourceListing ? (
                <a
                  href={sourceListing.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="w-full py-3 px-4 bg-white hover:bg-slate-100 text-slate-900 font-extrabold text-sm rounded-xl transition-colors flex items-center justify-center gap-2"
                >
                  <ExternalLink className="w-4 h-4" />
                  {t("원본 페이지에서 예약하기", "Book on the original listing page")}
                </a>
              ) : (
                <div className="w-full py-3 px-4 bg-white/10 border border-white/15 text-white/70 font-bold text-xs rounded-xl text-center">
                  {t("원본 매물 링크를 확인할 수 없습니다", "Original listing link is unavailable")}
                </div>
              )}
            </div>
          </div>
        </div>
      </section>

      {/* ===================== BATCH ANGLE GALLERY ===================== */}
      {batchFrames.length > 0 && (
        <section className="max-w-[1400px] mx-auto px-4 md:px-8 pt-10">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xl font-extrabold text-slate-900 tracking-tight">
              {t(`추천 앵글 ${BEST_ANGLES.length}종`, `${BEST_ANGLES.length} recommended angles`)}
            </h2>
            <span className="text-sm font-semibold text-slate-500">
              {batchFrames.filter((f) => f.url).length}/{batchFrames.length} {t("완료", "complete")}
              {" · "}{t(`${formatHour(timeOfDay)} 기준`, `at ${formatHour(timeOfDay)}`)}
            </span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
            {batchFrames.map((f, i) => (
              <button
                key={i}
                onClick={() => {
                  if (!f.url || !solar) return;
                  const a = BEST_ANGLES[i];
                  const next = { rotation: a.rotation, tilt: a.tilt, zoom: a.zoom };
                  setOrbit(next);
                  // Adopt this frame as the current render under its own settings key,
                  // so the console shows it as up to date rather than stale.
                  const rb = Math.round((((next.rotation % 360) + 360) % 360) / 15) * 15;
                  const tb = Math.round(next.tilt / 15) * 15;
                  const zb = Math.round(next.zoom / 2) * 2;
                  const key = `${rawImage}|r${rb}|t${tb}|z${zb}|${dateStr}@${Math.round(timeOfDay)}|${solar.phase}`;
                  frameCache.current.set(key, f.url);
                  setRender({ url: f.url, key });
                  pushHistory({
                    url: f.url, key, label: en ? BEST_ANGLE_NAMES_EN[a.name] : a.name, orbit: next,
                    timeOfDay, dateStr, sourceImage: rawImage,
                  });
                  window.scrollTo({ top: 0, behavior: "smooth" });
                }}
                disabled={!f.url}
                className="group text-left"
              >
                <div className="relative aspect-[4/3] rounded-xl overflow-hidden bg-gray-100 border border-gray-200">
                  {f.url ? (
                    <img src={f.url} alt={f.name} className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-105" />
                  ) : f.failed ? (
                    <div className="w-full h-full flex items-center justify-center text-xs font-semibold text-gray-400">{t("생성 실패", "Generation failed")}</div>
                  ) : (
                    <div className="w-full h-full flex items-center justify-center bg-gray-100 animate-pulse">
                      <Loader2 className="w-5 h-5 animate-spin text-gray-400" />
                    </div>
                  )}
                </div>
                <p className="mt-2 text-sm font-bold text-slate-800">{f.name}</p>
                <p className="text-xs font-medium text-slate-500">
                  {t("회전", "Rotation")} {BEST_ANGLES[i].rotation}° · {t("각도", "Tilt")} {BEST_ANGLES[i].tilt}° · {zoomToFocal(BEST_ANGLES[i].zoom)}mm
                </p>
              </button>
            ))}
          </div>
        </section>
      )}

      {/* ===================== BELOW THE FOLD ===================== */}
      <main className="max-w-[1400px] mx-auto px-4 md:px-8 py-12 space-y-12">
        {/* Price row */}
        <div className="flex flex-wrap items-center justify-between gap-4 pb-8 border-b border-gray-200">
          <div className="text-3xl font-extrabold text-slate-900 tracking-tight">
            {location.price_per_hour > 0 ? (
              <>₩{location.price_per_hour.toLocaleString()}<span className="text-base font-semibold text-slate-500 ml-1">{t("/ 시간", "/ hour")}</span></>
            ) : (
              <span className="text-xl text-slate-600">{t("가격 문의 (원본 매물 페이지 확인)", "Price on request (check the original listing)")}</span>
            )}
          </div>
          <div className="flex items-center gap-3 text-sm font-semibold text-slate-500">
            {solar.sunWindow ? (
              <span className="flex items-center gap-1.5 px-3 py-1.5 bg-orange-50 border border-orange-200 rounded-full text-orange-700">
                <Sun className="w-4 h-4" />
                {t("오늘 직사광", "Direct sunlight today")} {formatHour(solar.sunWindow.start)}–{formatHour(solar.sunWindow.end)}
              </span>
            ) : (
              <span className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-50 border border-slate-200 rounded-full">
                <Sun className="w-4 h-4" />
                {solar.windowAz === null ? t("암막 스튜디오", "Blackout studio") : t("간접광 위주 공간", "Primarily indirect light")}
              </span>
            )}
            <span className="px-3 py-1.5 bg-slate-50 border border-slate-200 rounded-full">
              {t("골든아워", "Golden hour")} {formatHour(solar.times.goldenEveningStart)}–{formatHour(solar.times.sunset)}
            </span>
          </div>
        </div>

        {/* Specs & Permit */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-12">
          <div className="space-y-6">
            <h2 className="text-2xl font-extrabold text-slate-900 tracking-tight">{t("공간 스펙", "Space specifications")}</h2>
            <div className="grid grid-cols-2 gap-6 text-sm">
              <div className="space-y-1">
                <div className="text-slate-500 font-semibold flex items-center space-x-1.5">
                  <Maximize2 className="w-4 h-4" />
                  <span>{t("면적 및 천고", "Area and ceiling")}</span>
                </div>
                <div className="font-bold text-slate-900">
                  {location.specs.area_pyeong > 0
                    ? en ? `${location.specs.area_sqm} m² / ${location.specs.ceiling_height_m}m ceiling` : `${location.specs.area_pyeong}평 / 천고 ${location.specs.ceiling_height_m}m`
                    : t("매물 문의", "Ask the listing provider")}
                </div>
              </div>
              <div className="space-y-1">
                <div className="text-slate-500 font-semibold flex items-center space-x-1.5">
                  <Sun className="w-4 h-4" />
                  <span>{t("채광 및 방향", "Light and orientation")}</span>
                </div>
                <div className="font-bold text-slate-900">{location.specs.window_direction}</div>
              </div>
              <div className="space-y-1">
                <div className="text-slate-500 font-semibold flex items-center space-x-1.5">
                  <Zap className="w-4 h-4" />
                  <span>{t("전력 용량", "Power capacity")}</span>
                </div>
                <div className="font-bold text-slate-900">{location.specs.power_capacity}</div>
              </div>
              <div className="space-y-1">
                <div className="text-slate-500 font-semibold flex items-center space-x-1.5">
                  <Car className="w-4 h-4" />
                  <span>{t("주차 및 하역", "Parking and loading")}</span>
                </div>
                <div className="font-bold text-slate-900">
                  {location.specs.parking_spots > 0 ? t(`주차 ${location.specs.parking_spots}대`, `${location.specs.parking_spots} parking spaces`) : t("매물 문의", "Ask the listing provider")}
                </div>
              </div>
            </div>

            <div className="pt-6">
              <h3 className="text-sm font-bold text-slate-500 mb-3 uppercase tracking-wider">{t("공간 태그", "Space tags")}</h3>
              <div className="flex flex-wrap gap-2">
                {location.tags.map((tag, i) => (
                  <span key={i} className="px-3 py-1.5 bg-slate-100 text-slate-700 text-xs font-bold rounded-md">
                    #{tag}
                  </span>
                ))}
              </div>
            </div>
          </div>

          <div className="space-y-6">
            <h2 className="text-2xl font-extrabold text-slate-900 tracking-tight">
              {t("인허가 및 출처", "Permits and sources")}
            </h2>
            <div className="p-6 bg-slate-50 border border-slate-200 rounded-2xl space-y-4">
              <div className="flex items-center space-x-2 text-sm font-extrabold text-slate-800">
                <ShieldCheck className="w-5 h-5 text-indigo-600" />
                <span>{t("수집 출처 리포트", "Collected source report")}</span>
              </div>
              <p className="text-sm text-slate-700 leading-relaxed font-medium">
                {location.permit_summary}
              </p>

              <div className="space-y-3 pt-3">
                {location.citations.map((cite, idx) => (
                  <div key={idx} className="p-4 bg-white rounded-xl border border-slate-200 shadow-sm text-sm space-y-2">
                    <div className="flex items-center justify-between gap-2">
                      <a
                        href={cite.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="font-bold text-indigo-600 hover:text-indigo-800 flex items-center space-x-1.5 transition-colors"
                      >
                        <span>{cite.title}</span>
                        <ExternalLink className="w-3.5 h-3.5 shrink-0" />
                      </a>
                      <span className={`shrink-0 px-2 py-0.5 rounded-full text-[10px] font-extrabold ${
                        cite.verification_status === "LIVE"
                          ? "bg-emerald-100 text-emerald-700"
                          : cite.verification_status === "DEMO"
                          ? "bg-amber-100 text-amber-700"
                          : "bg-slate-100 text-slate-600"
                      }`}>
                        {cite.verification_status === "LIVE" ? t("실시간 수집", "Live source") : cite.verification_status}
                      </span>
                    </div>
                    <p className="text-slate-600 font-medium">"{cite.excerpt}"</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
