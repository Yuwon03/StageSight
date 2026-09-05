"use client";

import React, { useState, useEffect, useRef, useCallback } from "react";
import { KoreanLocation } from "@/types";
import {
  fetchLocationsPage,
  syncCatalog,
  BackendUnreachableError,
  fetchCatalogStats,
} from "@/lib/api";
import { CategoryBar } from "@/components/catalog/CategoryBar";
import { FilterBar } from "@/components/catalog/FilterBar";
import { LocationCard } from "@/components/catalog/LocationCard";
import { ScriptMatcherPanel } from "@/components/script/ScriptMatcherPanel";
import { Film, Sparkles, Search, Compass, CheckCircle2, AlertTriangle, Loader2, Heart } from "lucide-react";
import Link from "next/link";
import { useUserState } from "@/lib/useUser";

const LIMIT = 60;

type Locale = "ko" | "en";

export function StageSightHome({ locale = "ko" }: { locale?: Locale }) {
  const en = locale === "en";
  const t = (ko: string, english: string) => (en ? english : ko);
  const [activeTab, setActiveTab] = useState<"catalog" | "script">("catalog");

  // Saved count and avatar come from the local user store until accounts exist.
  const { profile, saved } = useUserState();
  const savedCount = saved.length;
  const displayName = en && profile.displayName === "게스트" ? "Guest" : profile.displayName;
  const profileInitial = (displayName || t("게", "G")).trim().charAt(0);

  // The tab has to live in the URL, not only in state. It used to be state
  // alone, so switching to 탐색 left the address bar saying ?tab=script; opening
  // a listing and pressing Back then restored that stale URL and dropped the
  // user into the chat they had already left. Reading it from window.location
  // rather than useSearchParams keeps this page statically prerenderable.
  useEffect(() => {
    const read = () =>
      setActiveTab(new URLSearchParams(window.location.search).get("tab") === "script" ? "script" : "catalog");
    read();
    window.addEventListener("popstate", read);
    return () => window.removeEventListener("popstate", read);
  }, []);

  // replaceState, not pushState: flipping tabs is not a navigation the user
  // should have to press Back through, but the URL must still describe what is
  // on screen when they leave the page.
  const selectTab = useCallback((tab: "catalog" | "script") => {
    setActiveTab(tab);
    const url = new URL(window.location.href);
    if (tab === "script") {
      url.searchParams.set("tab", "script");
    } else {
      url.searchParams.delete("tab");
      url.searchParams.delete("chat");
    }
    window.history.replaceState(null, "", url);
  }, []);
  const [locations, setLocations] = useState<KoreanLocation[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [offline, setOffline] = useState(false);
  const [isLoadingLocations, setIsLoadingLocations] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);

  const skipRef = useRef(0);
  const hasMoreRef = useRef(true);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const versionRef = useRef(0);
  const [freshCount, setFreshCount] = useState(0);

  // Whether this server can actually ingest. On Cloud Run it cannot — the write
  // would land on an instance-local filesystem and vanish — so the button that
  // offers it must not be shown there.
  const [snapshot, setSnapshot] = useState<{ isSnapshot: boolean; takenAt: string | null }>({
    isSnapshot: false,
    takenAt: null,
  });

  useEffect(() => {
    fetchCatalogStats()
      .then((s) => setSnapshot({ isSnapshot: !!s.snapshot, takenAt: s.snapshot_taken_at ?? null }))
      .catch(() => {});
  }, []);

  const [selectedCategory, setSelectedCategory] = useState("전체");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedRegion, setSelectedRegion] = useState("전체");
  const [maxPrice, setMaxPrice] = useState(250000);
  const [windowDir, setWindowDir] = useState("전체");
  const [minParking, setMinParking] = useState(0);

  // Reference records (public filming registers) are real places but not
  // rentable listings, so the default view is bookable only and including them
  // is an explicit choice the user makes.
  const [includeReference, setIncludeReference] = useState(false);
  const loadLocations = useCallback(
    async (reset: boolean) => {
      if (reset) {
        setIsLoadingLocations(true);
        skipRef.current = 0;
        hasMoreRef.current = true;
      } else {
        if (!hasMoreRef.current || isLoadingMore) return;
        setIsLoadingMore(true);
      }
      const currentSkip = reset ? 0 : skipRef.current;

      try {
        const { items, total, version } = await fetchLocationsPage({
          category: selectedCategory,
          region: selectedRegion,
          max_price: maxPrice,
          window_dir: windowDir !== "전체" ? windowDir : undefined,
          min_parking: minParking > 0 ? minParking : undefined,
          listing_kind: includeReference ? "전체" : "bookable",
          skip: currentSkip,
          limit: LIMIT,
        });

        skipRef.current = currentSkip + items.length;
        hasMoreRef.current = skipRef.current < total && items.length > 0;
        setTotalCount(total);
        setOffline(false);
        versionRef.current = version;
        setLocations((prev) => (reset ? items : [...prev, ...items]));
      } catch (err) {
        if (err instanceof BackendUnreachableError) {
          setOffline(true);
          if (reset) setLocations([]);
        }
        console.error(err);
      } finally {
        setIsLoadingLocations(false);
        setIsLoadingMore(false);
      }
    },
    [selectedCategory, selectedRegion, maxPrice, windowDir, minParking, includeReference, isLoadingMore]
  );

  useEffect(() => {
    loadLocations(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedCategory, selectedRegion, maxPrice, windowDir, minParking, includeReference]);

  // Delta sync: on focus and on a slow timer, ask only for what changed since the
  // version we hold and patch it in. No full refetch, no scroll position lost.
  useEffect(() => {
    let cancelled = false;

    const applyDelta = async () => {
      if (cancelled || document.hidden || versionRef.current === 0) return;
      try {
        const d = await syncCatalog(versionRef.current);
        if (cancelled) return;
        versionRef.current = d.version;
        setTotalCount(d.catalog_size);
        setFreshCount(d.new_count);
        if (d.upserted.length === 0 && d.removed.length === 0) return;

        setLocations((prev) => {
          const removed = new Set(d.removed);
          const byId = new Map(d.upserted.map((l) => [l.id, l]));
          // Patch listings already on screen, drop delisted ones…
          const patched = prev
            .filter((l) => !removed.has(l.id))
            .map((l) => byId.get(l.id) ?? l);
          const known = new Set(patched.map((l) => l.id));
          // …and put genuinely new finds at the very top.
          const fresh = d.upserted.filter((l) => !known.has(l.id));
          if (fresh.length) skipRef.current += fresh.length;
          return [...fresh, ...patched];
        });
      } catch {
        // A failed sync is not worth surfacing; the next tick retries.
      }
    };

    const onVisible = () => {
      if (!document.hidden) applyDelta();
    };

    const timer = setInterval(applyDelta, 60_000);
    window.addEventListener("focus", applyDelta);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      clearInterval(timer);
      window.removeEventListener("focus", applyDelta);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  // Infinite scroll: load next page when the sentinel scrolls into view.
  // Re-attach whenever the sentinel can (re)mount — it doesn't exist during
  // the initial skeleton state or on the script tab.
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) loadLocations(false);
      },
      { rootMargin: "600px" }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [loadLocations, isLoadingLocations, activeTab]);

  // Ingest real hourplace.co.kr listings. The run is server-side and streams into
  // the catalog, so we poll for progress and refresh the grid as it fills.
  const handleResetFilters = () => {
    setSelectedCategory("전체");
    setSearchQuery("");
    setSelectedRegion("전체");
    setMaxPrice(250000);
    setWindowDir("전체");
    setMinParking(0);
  };

  const displayedLocations = locations.filter((l) => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    return (
      l.name.toLowerCase().includes(q) ||
      l.region.toLowerCase().includes(q) ||
      l.tagline.toLowerCase().includes(q) ||
      l.tags.some((t) => t.toLowerCase().includes(q))
    );
  });

  return (
    <div lang={locale} className="min-h-screen flex flex-col bg-white text-gray-900 font-sans">
      {/* Top Navigation Bar */}
      <header className="sticky top-0 z-40 bg-white/90 backdrop-blur-md border-b border-gray-200 px-4 md:px-10 xl:px-20 py-4 flex flex-wrap items-center justify-between gap-4">
        {/* Brand Logo */}
        <div
          onClick={() => selectTab("catalog")}
          className="flex items-center space-x-3 cursor-pointer group"
        >
          <div className="w-10 h-10 rounded-xl bg-indigo-600 text-white flex items-center justify-center shadow-md shadow-indigo-600/20 group-hover:scale-105 transition-transform">
            <Film className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <span className="text-xl font-bold tracking-tight text-gray-900">STAGESIGHT</span>
            </div>
          </div>
        </div>

        {/* Navigation Tabs */}
        <Link href={en ? "/" : "/en"} lang={en ? "ko" : "en"} className="text-sm font-semibold text-indigo-700 underline">
          {en ? "한국어" : "English"}
        </Link>
        <div className="flex items-center space-x-2 bg-gray-100 p-1 rounded-full text-sm font-semibold">
          <button
            onClick={() => selectTab("catalog")}
            className={`px-5 py-2 rounded-full flex items-center space-x-2 transition-all ${
              activeTab === "catalog"
                ? "bg-white text-indigo-600 shadow-sm font-bold"
                : "text-gray-500 hover:text-gray-900"
            }`}
          >
            <Compass className="w-4 h-4" />
            <span>{t("탐색", "Explore")} ({searchQuery.trim() ? displayedLocations.length : totalCount.toLocaleString(en ? "en-US" : "ko-KR")})</span>
          </button>
          <button
            onClick={() => selectTab("script")}
            className={`px-5 py-2 rounded-full flex items-center space-x-2 transition-all ${
              activeTab === "script"
                ? "bg-white text-indigo-600 shadow-sm font-bold"
                : "text-gray-500 hover:text-gray-900"
            }`}
          >
            <Sparkles className="w-4 h-4" />
            <span>{t("대본 AI 매칭", "Script AI Matching")}</span>
          </button>
        </div>

        {/* User Profile / Extras */}
        <div className="hidden md:flex items-center space-x-3 text-sm text-gray-500">
          <Link
            href={en ? "/en/me" : "/me"}
            title={t("내 활동", "My activity")}
            className="flex items-center gap-2 border border-gray-200 pl-3 pr-2 py-1.5 rounded-full shadow-sm hover:shadow-md hover:border-gray-300 transition-all bg-white"
          >
            {/* The saved count is the whole reason to visit the page, so it is
                on the button rather than behind it. */}
            <span className="flex items-center gap-1.5 text-xs font-bold text-gray-700">
              <Heart className={`w-4 h-4 ${savedCount > 0 ? "fill-rose-500 text-rose-500" : "text-gray-400"}`} />
              {savedCount}
            </span>
            <div className="w-8 h-8 bg-indigo-600 rounded-full flex items-center justify-center">
              <span className="text-white font-bold text-xs">{profileInitial}</span>
            </div>
          </Link>
        </div>
      </header>

      {/* Main Content Area */}
      <main className="flex-1 w-full max-w-[2520px] mx-auto px-4 md:px-10 xl:px-20 py-6 space-y-6">
        {activeTab === "catalog" && (
          <div className="space-y-6 animate-in fade-in duration-300">
            {/* Category Filter Bar */}
            <CategoryBar
              locale={locale}
              selectedCategory={selectedCategory}
              onSelectCategory={setSelectedCategory}
            />

            {/* Filter & Search Bar with Crawl Button */}
            <FilterBar
              locale={locale}
              searchQuery={searchQuery}
              onSearchChange={setSearchQuery}
              selectedRegion={selectedRegion}
              onRegionChange={setSelectedRegion}
              maxPrice={maxPrice}
              onMaxPriceChange={setMaxPrice}
              windowDir={windowDir}
              onWindowDirChange={setWindowDir}
              minParking={minParking}
              onMinParkingChange={setMinParking}
              onResetFilters={handleResetFilters}
            />

            {/* Backend unreachable */}
            {offline && !isLoadingLocations && (
              <div className="p-4 bg-amber-50 border border-amber-200 rounded-2xl flex items-center space-x-3 text-sm text-amber-800">
                <AlertTriangle className="w-5 h-5 text-amber-600 shrink-0" />
                <span className="font-semibold">
                  {t("백엔드 서버(localhost:8080)에 연결할 수 없습니다. 이 앱은 실제 매물만 표시하므로 대체 데이터를 보여주지 않습니다.", "The backend server (localhost:8080) is unavailable. This app only displays real listings, so it does not show substitute data.")}
                  <code className="mx-1 px-1.5 py-0.5 bg-amber-100 rounded text-xs">cd services/agent && uvicorn app.main:app --port 8080</code>
                  {t("으로 서버를 실행해주세요.", "to start the server.")}
                </span>
              </div>
            )}

            {/* Location Cards Grid */}
            {isLoadingLocations ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-6">
                {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((i) => (
                  <div
                    key={i}
                    className="aspect-square bg-gray-200 rounded-2xl animate-pulse"
                  />
                ))}
              </div>
            ) : displayedLocations.length === 0 ? (
              <div className="text-center py-24 space-y-4 bg-gray-50 rounded-3xl border border-gray-200">
                <Search className="w-12 h-12 text-gray-400 mx-auto" />
                {totalCount === 0 && !offline ? (
                  <>
                    <h3 className="text-lg font-bold text-gray-900">{t("카탈로그가 비어 있습니다", "The catalogue is empty")}</h3>
                    {/* The crawler fills this on its own; there is nothing for a
                        visitor to trigger, and on the deployed snapshot a manual
                        ingest could not have persisted anything anyway. */}
                    <p className="text-sm text-gray-500 max-w-md mx-auto">
                      {snapshot.isSnapshot
                        ? t("이 서버의 카탈로그는 배포 시점의 스냅샷입니다.", "This server uses a catalogue snapshot from deployment time.")
                        : t("수집기가 카탈로그를 자동으로 채웁니다. 잠시 후 다시 확인해주세요.", "The collector is filling the catalogue automatically. Please check again shortly.")}
                    </p>
                  </>
                ) : (
                  <>
                    <h3 className="text-lg font-bold text-gray-900">{t("검색 결과가 없습니다", "No results found")}</h3>
                    <p className="text-sm text-gray-500">{t("다른 필터를 선택하거나 검색어를 변경해보세요.", "Try another filter or search term.")}</p>
                    <button
                      onClick={handleResetFilters}
                      className="px-6 py-2 bg-gray-900 text-white text-sm font-semibold rounded-full hover:bg-gray-800 transition-colors mt-2"
                    >
                      {t("모두 지우기", "Clear all")}
                    </button>
                  </>
                )}
              </div>
            ) : (
              <>
                <div className="flex items-center justify-between text-sm font-semibold text-gray-500">
                  <span className="flex items-center gap-2">
                    {searchQuery.trim()
                      ? t(`검색 결과 ${displayedLocations.length}건`, `${displayedLocations.length} results`)
                      : t(`${totalCount.toLocaleString()}건`, `${totalCount.toLocaleString("en-US")} locations`)}
                    {freshCount > 0 && freshCount < totalCount * 0.5 && (
                      <span className="px-2 py-0.5 bg-rose-50 text-rose-700 border border-rose-200 rounded-full text-xs font-bold">
                        {t(`최근 72시간 신규 ${freshCount.toLocaleString()}건`, `${freshCount.toLocaleString("en-US")} new in 72 hours`)}
                      </span>
                    )}
                  </span>

                  <label className="flex items-center gap-2 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={includeReference}
                      onChange={(e) => setIncludeReference(e.target.checked)}
                      className="w-4 h-4 accent-indigo-600"
                    />
                    <span className="text-xs font-bold text-gray-600">
                      {t("촬영 기록 장소 포함", "Include filming references")}
                    </span>
                  </label>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-6">
                  {displayedLocations.map((loc) => (
                    <Link key={loc.id} href={`${en ? "/en" : ""}/location/${loc.id}`} className="group">
                      <LocationCard location={loc} locale={locale} />
                    </Link>
                  ))}
                </div>

                {/* Infinite-scroll sentinel + manual load-more */}
                {!searchQuery.trim() && (
                  <div ref={sentinelRef} className="flex justify-center py-8">
                    {isLoadingMore ? (
                      <div className="flex items-center space-x-2 text-gray-500 text-sm font-semibold">
                        <Loader2 className="w-5 h-5 animate-spin" />
                        <span>{t("매물 더 불러오는 중...", "Loading more locations...")}</span>
                      </div>
                    ) : locations.length < totalCount ? (
                      <button
                        onClick={() => loadLocations(false)}
                        className="px-8 py-3 bg-gray-900 text-white text-sm font-bold rounded-full hover:bg-gray-800 transition-colors"
                      >
                        {t(`매물 더보기 (${(totalCount - locations.length).toLocaleString()}건 남음)`, `Load more (${(totalCount - locations.length).toLocaleString("en-US")} remaining)`)}
                      </button>
                    ) : (
                      <span className="text-sm font-semibold text-gray-400">{t("모든 매물을 확인했습니다.", "You have reached the end of the catalogue.")}</span>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {activeTab === "script" && (
          <div className="animate-in fade-in duration-300">
            <ScriptMatcherPanel locale={locale} />
          </div>
        )}
      </main>

      <footer className="mt-auto border-t border-gray-200 bg-white px-4 md:px-10 xl:px-20 py-6 text-sm text-gray-500 flex flex-wrap items-center justify-between gap-4">
        <div>
          <span className="font-bold text-gray-700">StageSight Korea</span> © 2026
        </div>
      </footer>
    </div>
  );
}

export default function Home() {
  return <StageSightHome locale="ko" />;
}
