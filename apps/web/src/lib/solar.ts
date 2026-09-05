// Deterministic solar ephemeris for Korean filming locations.
// NOAA simplified equations — accurate to within a few minutes for sunrise/sunset,
// which is sufficient for lighting planning (not for astronomy).

export interface SunTimes {
  sunrise: number; // decimal hours, local clock (KST)
  sunset: number;
  solarNoon: number;
  goldenMorningEnd: number;
  goldenEveningStart: number;
  dayLengthH: number;
}

export interface SunPosition {
  altitudeDeg: number; // above horizon
  azimuthDeg: number; // 0=N, 90=E, 180=S, 270=W
}

export type LightPhase =
  | "night"
  | "blue_hour"
  | "golden_hour"
  | "morning"
  | "midday"
  | "afternoon";

const KST_MERIDIAN = 135.0; // UTC+9 reference meridian

// Region name → coordinates. Matched by substring, first hit wins.
const REGION_COORDS: Array<{ match: string[]; lat: number; lon: number }> = [
  { match: ["파주"], lat: 37.76, lon: 126.78 },
  { match: ["양평"], lat: 37.49, lon: 127.49 },
  { match: ["남양주"], lat: 37.64, lon: 127.22 },
  { match: ["인천"], lat: 37.46, lon: 126.71 },
  { match: ["부산", "해운대"], lat: 35.16, lon: 129.16 },
  { match: ["제주", "서귀포"], lat: 33.25, lon: 126.56 },
  { match: ["강릉"], lat: 37.75, lon: 128.88 },
  { match: ["경기"], lat: 37.44, lon: 127.14 },
  { match: ["서울"], lat: 37.5665, lon: 126.978 },
];

export function coordsForRegion(region: string): { lat: number; lon: number } {
  for (const r of REGION_COORDS) {
    if (r.match.some((m) => region.includes(m))) return { lat: r.lat, lon: r.lon };
  }
  return { lat: 37.5665, lon: 126.978 }; // default Seoul
}

// "남서향 (220°)" → 220 · "서향 (오후광/일몰광)" → 270 · "완전 암막" → null
export function parseWindowAzimuth(windowDirection: string): number | null {
  const explicit = windowDirection.match(/(\d{1,3})\s*°/);
  if (explicit) return parseInt(explicit[1], 10);
  if (windowDirection.includes("암막")) return null;
  if (windowDirection.includes("남서")) return 225;
  if (windowDirection.includes("남동")) return 135;
  if (windowDirection.includes("북서")) return 315;
  if (windowDirection.includes("북동")) return 45;
  if (windowDirection.includes("서")) return 270;
  if (windowDirection.includes("동")) return 90;
  if (windowDirection.includes("남")) return 180;
  if (windowDirection.includes("북")) return 0;
  return 180;
}

export function windowDirectionLabel(azimuth: number | null): string {
  if (azimuth === null) return "암막 (자연광 없음)";
  const names = ["북향", "북동향", "동향", "남동향", "남향", "남서향", "서향", "북서향"];
  return names[Math.round((((azimuth % 360) + 360) % 360) / 45) % 8];
}

function dayOfYear(date: Date): number {
  const start = new Date(date.getFullYear(), 0, 0);
  return Math.floor((date.getTime() - start.getTime()) / 86400000);
}

function declinationDeg(doy: number): number {
  return 23.45 * Math.sin(((2 * Math.PI) / 365) * (doy - 81));
}

function equationOfTimeMin(doy: number): number {
  const b = ((2 * Math.PI) / 365) * (doy - 81);
  return 9.87 * Math.sin(2 * b) - 7.53 * Math.cos(b) - 1.5 * Math.sin(b);
}

// Local clock hour ↔ solar hour conversion offset (hours to ADD to clock time)
function solarOffsetH(lon: number, doy: number): number {
  return (4 * (lon - KST_MERIDIAN) + equationOfTimeMin(doy)) / 60;
}

export function getSunTimes(dateStr: string, lat: number, lon: number): SunTimes {
  const date = new Date(`${dateStr}T12:00:00`);
  const doy = dayOfYear(date);
  const decl = (declinationDeg(doy) * Math.PI) / 180;
  const latRad = (lat * Math.PI) / 180;

  // Hour angle at sunrise/sunset (sun altitude = -0.833° for refraction + disc radius)
  const cosH0 =
    (Math.sin((-0.833 * Math.PI) / 180) - Math.sin(latRad) * Math.sin(decl)) /
    (Math.cos(latRad) * Math.cos(decl));
  const h0Deg = (Math.acos(Math.max(-1, Math.min(1, cosH0))) * 180) / Math.PI;

  const offset = solarOffsetH(lon, doy);
  const solarNoonClock = 12 - offset;
  const sunrise = solarNoonClock - h0Deg / 15;
  const sunset = solarNoonClock + h0Deg / 15;

  return {
    sunrise,
    sunset,
    solarNoon: solarNoonClock,
    goldenMorningEnd: sunrise + 1.0,
    goldenEveningStart: sunset - 1.0,
    dayLengthH: (2 * h0Deg) / 15,
  };
}

export function getSunPosition(
  dateStr: string,
  hourDecimal: number,
  lat: number,
  lon: number
): SunPosition {
  const date = new Date(`${dateStr}T12:00:00`);
  const doy = dayOfYear(date);
  const decl = (declinationDeg(doy) * Math.PI) / 180;
  const latRad = (lat * Math.PI) / 180;

  const solarTime = hourDecimal + solarOffsetH(lon, doy);
  const hourAngleDeg = 15 * (solarTime - 12);
  const hourAngleRad = (hourAngleDeg * Math.PI) / 180;

  const sinAlt =
    Math.sin(latRad) * Math.sin(decl) +
    Math.cos(latRad) * Math.cos(decl) * Math.cos(hourAngleRad);
  const altRad = Math.asin(Math.max(-1, Math.min(1, sinAlt)));

  const cosAz =
    (Math.sin(decl) * Math.cos(latRad) -
      Math.cos(decl) * Math.sin(latRad) * Math.cos(hourAngleRad)) /
    Math.cos(altRad);
  let azDeg = (Math.acos(Math.max(-1, Math.min(1, cosAz))) * 180) / Math.PI;
  if (hourAngleDeg > 0) azDeg = 360 - azDeg;

  return { altitudeDeg: (altRad * 180) / Math.PI, azimuthDeg: azDeg };
}

export function getLightPhase(hour: number, times: SunTimes): LightPhase {
  if (hour < times.sunrise - 0.5 || hour > times.sunset + 0.5) return "night";
  if (hour < times.sunrise || hour > times.sunset) return "blue_hour";
  if (hour <= times.goldenMorningEnd || hour >= times.goldenEveningStart) return "golden_hour";
  if (hour < times.solarNoon - 1.5) return "morning";
  if (hour <= times.solarNoon + 1.5) return "midday";
  return "afternoon";
}

export const PHASE_LABELS: Record<LightPhase, string> = {
  night: "야간",
  blue_hour: "블루아워",
  golden_hour: "골든아워",
  morning: "오전광",
  midday: "정오광",
  afternoon: "오후광",
};

// 0..1 — how directly the sun is shining into this window right now
export function windowIncidence(pos: SunPosition, windowAzimuth: number | null): number {
  if (windowAzimuth === null || pos.altitudeDeg <= 0) return 0;
  const diff = Math.abs(((pos.azimuthDeg - windowAzimuth + 180) % 360) - 180);
  if (diff >= 90) return 0;
  const azFactor = Math.cos((diff * Math.PI) / 180);
  // Low sun penetrates deep into a room; high sun grazes the window
  const altFactor = pos.altitudeDeg < 35 ? 1 : Math.max(0.25, 1 - (pos.altitudeDeg - 35) / 55);
  return azFactor * altFactor;
}

function fmt(h: number): string {
  // Round to whole minutes first, then split. Rounding the minutes on their own
  // yields 60 for any fraction above 0.9917 — a 18.995h sunset printed as
  // "18:60" on the detail page rather than 19:00.
  const total = Math.round(h * 60);
  const hh = Math.floor(total / 60) % 24;
  const mm = total % 60;
  return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
}
export const formatHour = fmt;

// The window (clock hours) during which direct sun actually enters this window today
export function directSunWindow(
  dateStr: string,
  lat: number,
  lon: number,
  windowAzimuth: number | null,
  times: SunTimes
): { start: number; end: number } | null {
  if (windowAzimuth === null) return null;
  let start: number | null = null;
  let end: number | null = null;
  for (let h = times.sunrise; h <= times.sunset; h += 0.25) {
    const inc = windowIncidence(getSunPosition(dateStr, h, lat, lon), windowAzimuth);
    if (inc > 0.15) {
      if (start === null) start = h;
      end = h;
    }
  }
  return start !== null && end !== null ? { start, end } : null;
}

// User-friendly one-liner describing the light at this moment, in this room
export function describeLight(
  hour: number,
  times: SunTimes,
  pos: SunPosition,
  windowAzimuth: number | null
): string {
  const phase = getLightPhase(hour, times);
  const dirLabel = windowDirectionLabel(windowAzimuth);
  const inc = windowIncidence(pos, windowAzimuth);

  if (windowAzimuth === null) {
    return "암막 스튜디오 — 시간대와 무관하게 조명을 100% 자유롭게 제어할 수 있어요.";
  }
  switch (phase) {
    case "night":
      return `일몰(${fmt(times.sunset)}) 이후 완전한 야간이에요. 인공조명 세팅이 필수입니다.`;
    case "blue_hour":
      return `블루아워 — 창밖이 짙은 파란빛으로 물드는 10~20분의 짧은 마법 같은 시간이에요.`;
    case "golden_hour":
      return inc > 0.3
        ? `골든아워 — ${dirLabel} 창으로 낮은 황금빛 직사광이 방 깊숙이 들어와요. 역광/림라이트 촬영 최적기입니다.`
        : `골든아워지만 해가 ${dirLabel} 창 반대편에 있어 실내엔 부드러운 간접광만 들어와요.`;
    case "morning":
      return inc > 0.3
        ? `오전광 — ${dirLabel} 창으로 맑고 차분한 직사광이 들어오는 시간이에요.`
        : `오전 — ${dirLabel} 창엔 아직 직사광이 없어 균일하고 부드러운 확산광 상태예요.`;
    case "midday":
      return inc > 0.3
        ? `정오 — 태양 고도가 높아(${Math.round(pos.altitudeDeg)}°) ${dirLabel} 창가에 짧고 강한 빛이 떨어져요. 콘트라스트가 강한 시간대입니다.`
        : `정오 — 실내는 밝지만 직사광 없이 플랫한 톤이에요. 인터뷰나 제품 촬영에 무난합니다.`;
    case "afternoon":
      return inc > 0.3
        ? `오후광 — ${dirLabel} 창으로 따뜻해지기 시작한 빛이 점점 깊게 들어와요.`
        : `오후 — 해가 ${dirLabel} 창 쪽으로 넘어가기 전이라 아직 간접광 위주예요.`;
  }
}

// Booking-window advisory: given a booking [start,end], summarize what light to expect
export function bookingAdvisory(
  bookStart: number,
  bookEnd: number,
  times: SunTimes,
  sunWindow: { start: number; end: number } | null,
  seasonLabel: string
): { tone: "good" | "warn" | "info"; text: string } {
  const goldenS = times.goldenEveningStart;
  const goldenE = times.sunset;

  if (bookStart >= times.sunset) {
    return {
      tone: "warn",
      text: `${seasonLabel} 일몰은 ${fmt(times.sunset)}이에요. 예약 시간 전체가 일몰 이후라 자연광 촬영이 불가능합니다 — 조명 장비를 준비하세요.`,
    };
  }
  if (bookEnd > times.sunset && bookStart < times.sunset) {
    const naturalH = Math.max(0, times.sunset - bookStart);
    return {
      tone: "warn",
      text: `${fmt(times.sunset)}에 해가 져요. 예약 앞부분 ${naturalH.toFixed(1)}시간만 자연광 촬영이 가능하고, 이후는 인공조명이 필요합니다.`,
    };
  }
  if (bookStart <= goldenE && bookEnd >= goldenS) {
    return {
      tone: "good",
      text: `예약 시간에 골든아워(${fmt(goldenS)}–${fmt(goldenE)})가 포함돼요 — 이 공간의 자연광이 가장 아름다운 시간입니다.`,
    };
  }
  if (sunWindow && bookStart <= sunWindow.end && bookEnd >= sunWindow.start) {
    return {
      tone: "good",
      text: `예약 시간 중 ${fmt(Math.max(bookStart, sunWindow.start))}–${fmt(Math.min(bookEnd, sunWindow.end))}에 창으로 직사광이 들어와요.`,
    };
  }
  if (sunWindow) {
    return {
      tone: "info",
      text: `이 날짜에 직사광이 창으로 들어오는 시간은 ${fmt(sunWindow.start)}–${fmt(sunWindow.end)}이에요. 지금 예약 시간대엔 부드러운 간접광 위주입니다.`,
    };
  }
  return {
    tone: "info",
    text: `예약 시간대엔 확산 간접광 위주예요. 골든아워는 ${fmt(goldenS)}–${fmt(goldenE)}입니다.`,
  };
}
