"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchLocationsPage, resolveImageUrl, simulateAIFrame, researchFilmingPermits, PermitReport } from "@/lib/api";
import { KoreanLocation } from "@/types";

const categories = [
  ["전체", "All locations"], ["모던 스튜디오", "Modern studios"],
  ["전통 한옥", "Traditional houses"], ["자연/야외", "Outdoors"],
  ["빈티지/창고", "Vintage / warehouses"], ["럭셔리 하우스", "Luxury houses"],
  ["카페/갤러리", "Cafés / galleries"],
];
const phases = [
  { value: "morning", label: "Morning", time: "08:00", altitude: 25 },
  { value: "midday", label: "Midday", time: "12:00", altitude: 60 },
  { value: "golden_hour", label: "Golden hour", time: "18:30", altitude: 5 },
  { value: "blue_hour", label: "Blue hour", time: "19:30", altitude: -4 },
  { value: "night", label: "Night", time: "22:00", altitude: -28 },
];

export default function EnglishScout() {
  const [category, setCategory] = useState("전체");
  const [locations, setLocations] = useState<KoreanLocation[]>([]);
  const [selected, setSelected] = useState<KoreanLocation | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [phase, setPhase] = useState("golden_hour");
  const [rendering, setRendering] = useState(false);
  const [result, setResult] = useState("");
  const [page, setPage] = useState(0);
  const [total, setTotal] = useState(0);
  const [permit, setPermit] = useState<PermitReport | null>(null);
  const [researching, setResearching] = useState(false);

  async function research() {
    if (!selected) return;
    setResearching(true);
    setError("");
    try {
      setPermit(await researchFilmingPermits(selected.name, selected.region, selected.region, undefined, "en"));
    } catch { setError("Permit research is unavailable. Please try again shortly."); }
    finally { setResearching(false); }
  }

  useEffect(() => {
    const previous = document.documentElement.lang;
    document.documentElement.lang = "en";
    return () => { document.documentElement.lang = previous; };
  }, []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    fetchLocationsPage({ category, skip: page * 24, limit: 24 })
      .then(({ items, total: count }) => { if (active) { setLocations(items); setTotal(count); } })
      .catch(() => { if (active) setError("The catalogue is temporarily unavailable. Please try again shortly."); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [category, page]);

  async function render() {
    if (!selected?.images[0]) return;
    const target = phases.find((item) => item.value === phase)!;
    setRendering(true);
    setError("");
    setResult("");
    try {
      const output = await simulateAIFrame({
        image_url: selected.images[0], location_id: selected.id,
        rotation: 0, tilt: 0, zoom: 10, light_phase: phase,
        time_label: target.time, sun_altitude_deg: target.altitude,
        space_category: selected.category, window_direction: selected.specs.window_direction,
      });
      setResult(output.image_data_url);
    } catch {
      setError("The preview could not be generated. Please try again; no result has been saved.");
    } finally { setRendering(false); }
  }

  return (
    <main lang="en" className="mx-auto max-w-7xl px-5 py-8 text-gray-900">
      <header className="flex items-center justify-between border-b pb-5">
        <Link href="/en" className="text-2xl font-bold text-indigo-700">STAGESIGHT</Link>
        <Link href="/" lang="ko" className="rounded-full border px-4 py-2">한국어</Link>
      </header>
      <h1 className="mt-8 text-3xl font-bold">Scout a location. Explore its light.</h1>
      <p className="mt-3 max-w-3xl text-gray-600">Browse real Korean filming locations and compare a source photo with an AI lighting preview. No account is required. Listing names and addresses retain their original Korean wording.</p>
      <p className="mt-2 text-sm text-gray-500">This English workspace covers browsing and lighting previews. Script matching and personal activity currently remain in the Korean workspace.</p>
      {error && <p role="alert" className="my-5 rounded-xl bg-amber-50 p-4 text-amber-900">{error}</p>}
      {selected ? (
        <section className="mt-8 space-y-5">
          <button disabled={rendering || researching} onClick={() => { setSelected(null); setResult(""); setPermit(null); setError(""); }} className="rounded-lg border px-4 py-2 disabled:opacity-50">← Back to locations</button>
          <h2 lang="ko" className="text-2xl font-semibold">{selected.name}</h2>
          <p><span lang="ko">{selected.region}</span> · {selected.specs.area_sqm > 0 ? `${selected.specs.area_sqm} m²` : "Area not provided"} · {selected.price_per_hour > 0 ? `KRW ${selected.price_per_hour.toLocaleString("en-US")} / hour` : "Price on request"}</p>
          {selected.source_url && <a href={selected.source_url} target="_blank" rel="noreferrer" className="inline-block text-indigo-700 underline">View original listing and confirm availability ↗</a>}
          <div className="flex flex-wrap items-center gap-3">
            <label htmlFor="lighting">Lighting scenario</label>
            <select id="lighting" value={phase} disabled={rendering} onChange={(event) => { setPhase(event.target.value); setResult(""); }} className="rounded-lg border p-3">
              {phases.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
            <button disabled={rendering || selected.no_derivatives || !selected.images[0]} onClick={render} className="rounded-lg bg-indigo-600 px-5 py-3 text-white disabled:opacity-50">{rendering ? "Generating preview…" : "Generate lighting preview"}</button>
          </div>
          <p className="text-sm text-gray-600">{selected.no_derivatives ? "This source does not permit photo modifications, so generation is disabled." : "AI previews are illustrative. They do not verify real illumination, dimensions, or unseen geometry. Confirm the location in person before filming."}</p>
          <section className="rounded-xl border p-5 space-y-3">
            <h3 className="text-lg font-semibold">Filming permits and practical constraints</h3>
            <p className="text-sm text-gray-600">Research public sources with Parallel Search. Confirm permissions with the venue and relevant authority.</p>
            <button disabled={researching} onClick={research} className="rounded-lg border px-4 py-2 disabled:opacity-50">{researching ? "Researching sources…" : "Research this location"}</button>
            {permit && <div aria-live="polite" className="space-y-3">
              {!permit.researched && <p>No sources were retrieved. No permit rules have been verified.</p>}
              {([["Permits", permit.permit_requirements], ["Filming hours", permit.curfew_hours], ["Noise", permit.noise_limits], ["Parking / loading", permit.parking_and_loading]]).map(([label, value]) => <p key={label}><strong>{label}: </strong>{value || "Not established by the retrieved sources."}</p>)}
              {permit.citations.map((citation, index) => <a key={`${citation.url}-${index}`} href={citation.url} target="_blank" rel="noreferrer" className="block text-indigo-700 underline">Source {index + 1}: {citation.title}</a>)}
            </div>}
          </section>
          <div className="grid gap-5 md:grid-cols-2">
            <figure><figcaption className="mb-2 font-semibold">Original photograph</figcaption>{selected.images[0] && <img src={resolveImageUrl(selected.images[0])} alt="Original filming location" className="w-full rounded-xl" />}</figure>
            <figure aria-live="polite"><figcaption className="mb-2 font-semibold">AI lighting preview</figcaption>{result ? <><img src={result} alt={`${phases.find((item) => item.value === phase)?.label} lighting preview`} className="w-full rounded-xl" /><a href={result} download={`${selected.id}-${phase}-ai-preview.png`} className="mt-3 inline-block text-indigo-700 underline">Download preview</a></> : <div className="rounded-xl bg-gray-100 p-12 text-gray-500">{rendering ? "The preview may take up to a minute." : "Choose a scenario and generate a preview."}</div>}</figure>
          </div>
        </section>
      ) : (
        <section className="mt-8">
          <label htmlFor="category" className="mr-3 font-semibold">Location type</label>
          <select id="category" value={category} onChange={(event) => { setCategory(event.target.value); setPage(0); }} className="rounded-lg border p-3">{categories.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
          <p role="status" className="my-4 text-sm text-gray-500">{loading ? "Loading locations…" : `${total.toLocaleString("en-US")} locations · Page ${page + 1}`}</p>
          {!loading && <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">{locations.map((location) => <button key={location.id} onClick={() => { setSelected(location); setResult(""); }} className="overflow-hidden rounded-2xl border text-left hover:border-indigo-500">
            {location.images[0] && <img src={resolveImageUrl(location.images[0])} alt="Location photograph" loading="lazy" className="aspect-video w-full object-cover" />}
            <div className="space-y-2 p-4"><h2 lang="ko" className="font-semibold">{location.name}</h2><p className="text-sm text-gray-600">{categories.find(([key]) => key === location.category)?.[1] || "Filming location"}</p><p>{location.price_per_hour > 0 ? `KRW ${location.price_per_hour.toLocaleString("en-US")} / hour` : "Price on request"}</p><span className="inline-block text-indigo-700">Explore lighting →</span></div>
          </button>)}</div>}
          <div className="mt-6 flex gap-3"><button disabled={loading || page === 0} onClick={() => setPage(page - 1)} className="rounded-lg border px-4 py-2 disabled:opacity-40">Previous</button><button disabled={loading || (page + 1) * 24 >= total} onClick={() => setPage(page + 1)} className="rounded-lg border px-4 py-2 disabled:opacity-40">Next</button></div>
        </section>
      )}
    </main>
  );
}
