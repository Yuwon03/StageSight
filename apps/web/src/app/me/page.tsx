"use client";

/**
 * The user's own page: profile, saved locations, and script-matching history.
 *
 * There is no account system yet, so everything here comes from this browser.
 * The page says so plainly rather than implying the data follows the user
 * around — and it is laid out the way the signed-in version will be, so adding
 * accounts is a change of data source, not a redesign.
 */

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  Heart,
  MessageSquare,
  Pencil,
  Trash2,
  Film,
  Info,
  Clock,
  ChevronDown,
} from "lucide-react";
import { resolveImageUrl } from "@/lib/api";
import { useUserState } from "@/lib/useUser";
import {
  clearAll,
  deleteConversation,
  removeSaved,
  setDisplayName,
} from "@/lib/userStore";
import {
  allRenders,
  deleteRender,
  pruneDelisted,
  RenderRecord,
  subscribeRenders,
} from "@/lib/renderStore";
import { fetchLocationById, BackendUnreachableError } from "@/lib/api";
import { Camera } from "lucide-react";

function timeAgo(iso: string): string {
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (mins < 1) return "방금 전";
  if (mins < 60) return `${mins}분 전`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}시간 전`;
  const days = Math.round(hrs / 24);
  return days < 30 ? `${days}일 전` : new Date(iso).toLocaleDateString("ko-KR");
}

export default function MyPage() {
  const { profile, saved, conversations } = useUserState();
  const [tab, setTab] = useState<"saved" | "chats" | "renders">("saved");
  const [renders, setRenders] = useState<RenderRecord[]>([]);
  const [pruned, setPruned] = useState<number>(0);
  const [pruning, setPruning] = useState(true);

  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const refreshRenders = useCallback(async () => setRenders(await allRenders()), []);

  // One group per space, spaces ordered by their most recent render, and each
  // group's frames newest first. allRenders() already sorts, so a single pass
  // preserves that order without re-sorting.
  const renderGroups = React.useMemo(() => {
    const by = new Map<string, RenderRecord[]>();
    for (const r of renders) {
      const list = by.get(r.locationId);
      if (list) list.push(r);
      else by.set(r.locationId, [r]);
    }
    return [...by.entries()].map(([locationId, items]) => ({
      locationId,
      locationName: items[0].locationName,
      region: items[0].region,
      cover: items[0].image,
      items,
    }));
  }, [renders]);

  useEffect(() => {
    void refreshRenders();
    return subscribeRenders(() => void refreshRenders());
  }, [refreshRenders]);

  // Renders of listings that have since been delisted are dropped: their detail
  // page is gone and a scout must not plan a shoot around a venue that can no
  // longer be booked. A 404 is the only thing read as "delisted" — a network
  // failure returns null and the record is kept, because being unable to reach
  // the API is not evidence that a venue disappeared.
  useEffect(() => {
    let alive = true;
    (async () => {
      const stillListed = async (id: string): Promise<boolean | null> => {
        try {
          await fetchLocationById(id);
          return true;
        } catch (err) {
          if (err instanceof BackendUnreachableError) return null;
          // Only a genuine 404 means delisted; any other error is inconclusive.
          return /HTTP 404/.test(String((err as Error).message)) ? false : null;
        }
      };
      const { removed } = await pruneDelisted(stillListed);
      if (!alive) return;
      setPruned(removed.length);
      setPruning(false);
      if (removed.length) void refreshRenders();
    })();
    return () => {
      alive = false;
    };
  }, [refreshRenders]);
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState("");

  const initial = (profile.displayName || "게").trim().charAt(0);

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="sticky top-0 z-40 bg-white/90 backdrop-blur-md border-b border-gray-200 px-4 md:px-10 xl:px-20 py-4">
        <div className="max-w-5xl mx-auto flex items-center gap-3">
          <Link
            href="/"
            className="flex items-center gap-2 text-sm font-semibold text-gray-600 hover:text-gray-900 transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            <span>탐색으로</span>
          </Link>
          <div className="flex items-center gap-2 ml-auto">
            <div className="w-8 h-8 rounded-xl bg-indigo-600 text-white flex items-center justify-center">
              <Film className="w-4 h-4" />
            </div>
            <span className="font-bold tracking-tight text-gray-900">STAGESIGHT</span>
          </div>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-4 md:px-6 py-8 space-y-8">
        {/* Profile */}
        <section className="bg-white border border-gray-200 rounded-3xl p-6 md:p-8 shadow-sm">
          <div className="flex flex-wrap items-center gap-5">
            <div className="w-20 h-20 rounded-2xl bg-indigo-600 text-white flex items-center justify-center text-3xl font-bold shadow-md shadow-indigo-600/20">
              {initial}
            </div>
            <div className="flex-1 min-w-[200px]">
              {editingName ? (
                <div className="flex items-center gap-2">
                  <input
                    autoFocus
                    value={nameDraft}
                    onChange={(e) => setNameDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        setDisplayName(nameDraft);
                        setEditingName(false);
                      }
                      if (e.key === "Escape") setEditingName(false);
                    }}
                    maxLength={40}
                    className="px-3 py-2 border border-gray-300 rounded-xl text-lg font-bold text-gray-900 focus:outline-none focus:ring-2 focus:ring-indigo-600"
                  />
                  <button
                    onClick={() => {
                      setDisplayName(nameDraft);
                      setEditingName(false);
                    }}
                    className="px-4 py-2 bg-indigo-600 text-white text-sm font-semibold rounded-xl"
                  >
                    저장
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => {
                    setNameDraft(profile.displayName);
                    setEditingName(true);
                  }}
                  className="group flex items-center gap-2 text-left"
                >
                  <h1 className="text-2xl font-bold text-gray-900">{profile.displayName}</h1>
                  <Pencil className="w-4 h-4 text-gray-400 group-hover:text-gray-700 transition-colors" />
                </button>
              )}
              <p className="text-sm text-gray-500 font-medium mt-1">
                이 브라우저에 저장된 활동 · {new Date(profile.createdAt).toLocaleDateString("ko-KR")}부터
              </p>
            </div>

            <div className="flex items-center gap-6">
              <div className="text-center">
                <div className="text-2xl font-bold text-gray-900">{saved.length}</div>
                <div className="text-xs font-semibold text-gray-500 mt-0.5">저장한 공간</div>
              </div>
              <div className="text-center">
                <div className="text-2xl font-bold text-gray-900">{conversations.length}</div>
                <div className="text-xs font-semibold text-gray-500 mt-0.5">대본 대화</div>
              </div>
              <div className="text-center">
                <div className="text-2xl font-bold text-gray-900">{renders.length}</div>
                <div className="text-xs font-semibold text-gray-500 mt-0.5">생성한 컷</div>
              </div>
            </div>
          </div>

          {/* Say what this is, rather than implying an account exists. */}
          <div className="mt-6 flex items-start gap-2 p-3 bg-amber-50 border border-amber-200 rounded-2xl text-sm text-amber-900">
            <Info className="w-4 h-4 mt-0.5 shrink-0" />
            <span>
              아직 계정 기능이 없어 저장한 공간과 대화는 <b>이 브라우저에만</b> 보관됩니다. 다른 기기에서는
              보이지 않고, 브라우저 데이터를 지우면 함께 사라집니다.
            </span>
          </div>
        </section>

        {/* Tabs */}
        <div className="flex items-center gap-2 bg-gray-100 p-1 rounded-full text-sm font-semibold w-fit">
          <button
            onClick={() => setTab("saved")}
            className={`px-5 py-2 rounded-full flex items-center gap-2 transition-all ${
              tab === "saved" ? "bg-white text-indigo-600 shadow-sm font-bold" : "text-gray-500 hover:text-gray-900"
            }`}
          >
            <Heart className="w-4 h-4" />
            <span>저장한 공간 ({saved.length})</span>
          </button>
          <button
            onClick={() => setTab("chats")}
            className={`px-5 py-2 rounded-full flex items-center gap-2 transition-all ${
              tab === "chats" ? "bg-white text-indigo-600 shadow-sm font-bold" : "text-gray-500 hover:text-gray-900"
            }`}
          >
            <MessageSquare className="w-4 h-4" />
            <span>대본 대화 ({conversations.length})</span>
          </button>
          <button
            onClick={() => setTab("renders")}
            className={`px-5 py-2 rounded-full flex items-center gap-2 transition-all ${
              tab === "renders" ? "bg-white text-indigo-600 shadow-sm font-bold" : "text-gray-500 hover:text-gray-900"
            }`}
          >
            <Camera className="w-4 h-4" />
            <span>생성한 컷 ({renders.length})</span>
          </button>
        </div>

        {pruned > 0 && (
          <div className="flex items-start gap-2 p-3 bg-gray-100 border border-gray-200 rounded-2xl text-sm text-gray-700 font-medium">
            <Info className="w-4 h-4 mt-0.5 shrink-0" />
            대관이 종료된 공간의 컷 {pruned}건을 자동으로 정리했습니다.
          </div>
        )}

        {/* Saved locations */}
        {tab === "saved" &&
          (saved.length === 0 ? (
            <EmptyState
              icon={<Heart className="w-7 h-7 text-gray-400" />}
              title="아직 저장한 공간이 없어요"
              body="마음에 드는 공간의 하트를 누르면 여기에 모입니다."
              cta={{ href: "/", label: "공간 둘러보기" }}
            />
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
              {saved.map((s) => (
                <div
                  key={s.id}
                  className="group relative bg-white border border-gray-200 rounded-2xl overflow-hidden shadow-sm hover:shadow-md transition-shadow"
                >
                  <Link href={`/location/${s.id}`}>
                    <div className="aspect-[4/3] bg-gray-100 overflow-hidden">
                      {s.image ? (
                        <img
                          src={resolveImageUrl(s.image)}
                          alt=""
                          className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500"
                        />
                      ) : (
                        <div className="w-full h-full flex items-center justify-center text-gray-400 text-sm">
                          이미지 없음
                        </div>
                      )}
                    </div>
                    <div className="p-4 space-y-1">
                      <div className="text-sm font-bold text-gray-900 line-clamp-1">{s.region}</div>
                      <div className="text-sm text-gray-600 line-clamp-1">{s.name}</div>
                      <div className="pt-1 text-sm font-bold text-gray-900">
                        {s.pricePerHour > 0 ? `₩${s.pricePerHour.toLocaleString()} / 시간` : "가격 문의"}
                      </div>
                      <div className="text-xs text-gray-400 font-medium pt-1">{timeAgo(s.savedAt)} 저장</div>
                    </div>
                  </Link>
                  <button
                    onClick={() => removeSaved(s.id)}
                    aria-label="저장 취소"
                    className="absolute top-2.5 right-2.5 p-2 rounded-full bg-black/35 backdrop-blur-sm hover:bg-black/55 transition-colors"
                  >
                    <Heart className="w-5 h-5 fill-rose-500 text-rose-500" />
                  </button>
                </div>
              ))}
            </div>
          ))}

        {/* Conversations */}
        {tab === "chats" &&
          (conversations.length === 0 ? (
            <EmptyState
              icon={<MessageSquare className="w-7 h-7 text-gray-400" />}
              title="아직 대화가 없어요"
              body="대본을 분석하면 대화가 하나씩 쌓이고, 나중에 이어서 물어볼 수 있어요."
              cta={{ href: "/?tab=script", label: "대본 AI 매칭 열기" }}
            />
          ) : (
            <div className="space-y-3">
              {conversations.map((c) => {
                const last = c.messages[c.messages.length - 1];
                return (
                  <div
                    key={c.id}
                    className="group flex items-start gap-4 p-5 bg-white border border-gray-200 rounded-2xl shadow-sm hover:shadow-md transition-shadow"
                  >
                    <div className="w-10 h-10 rounded-xl bg-indigo-50 border border-indigo-100 flex items-center justify-center shrink-0">
                      <MessageSquare className="w-5 h-5 text-indigo-600" />
                    </div>
                    <Link href={`/?tab=script&chat=${c.id}`} className="flex-1 min-w-0">
                      <div className="font-bold text-gray-900 line-clamp-1">{c.title}</div>
                      {last && (
                        <div className="text-sm text-gray-500 line-clamp-2 mt-1 leading-relaxed">
                          {last.content}
                        </div>
                      )}
                      <div className="flex items-center gap-3 mt-2 text-xs text-gray-400 font-medium">
                        <span className="flex items-center gap-1">
                          <Clock className="w-3.5 h-3.5" />
                          {timeAgo(c.updatedAt)}
                        </span>
                        <span>{c.messages.length}개 메시지</span>
                        {c.analysisCount > 0 && <span>분석 {c.analysisCount}회</span>}
                      </div>
                    </Link>
                    <button
                      onClick={() => deleteConversation(c.id)}
                      aria-label="대화 삭제"
                      className="p-2 rounded-lg text-gray-400 hover:text-rose-600 hover:bg-rose-50 transition-colors"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                );
              })}
            </div>
          ))}

        {/* Generated frames, grouped by the space they belong to.
            A flat wall of images does not scale: a scout who renders a dozen
            angles of eight venues gets 96 tiles with no way to see which
            belong together. Grouping restores the unit of work — the space —
            and keeps each group's angles side by side for comparison. */}
        {tab === "renders" &&
          (pruning && renders.length === 0 ? (
            <div className="bg-white border border-gray-200 rounded-3xl p-12 text-center text-sm font-semibold text-gray-400">
              불러오는 중…
            </div>
          ) : renders.length === 0 ? (
            <EmptyState
              icon={<Camera className="w-7 h-7 text-gray-400" />}
              title="아직 생성한 컷이 없어요"
              body="공간 상세 페이지의 카메라·조명 시뮬레이터에서 앵글과 시간대를 바꿔 생성하면 여기에 남습니다."
              cta={{ href: "/", label: "공간 둘러보기" }}
            />
          ) : (
            <div className="space-y-4">
              {renderGroups.map((g) => {
                const open = expanded[g.locationId] ?? true;
                return (
                  <section key={g.locationId} className="bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden">
                    <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-100">
                      <img src={g.cover} alt="" className="w-11 h-11 rounded-lg object-cover bg-gray-100 shrink-0" />
                      <div className="min-w-0 flex-1">
                        <Link
                          href={`/location/${g.locationId}`}
                          className="text-sm font-bold text-gray-900 hover:text-indigo-600 transition-colors line-clamp-1"
                        >
                          {g.locationName.replace(/\[.*?\]/, "").trim()}
                        </Link>
                        <div className="text-xs text-gray-500 font-medium">
                          {g.region} · 컷 {g.items.length}장 · 최근 {timeAgo(g.items[0].createdAt)}
                        </div>
                      </div>
                      <button
                        onClick={() => setExpanded((e) => ({ ...e, [g.locationId]: !open }))}
                        className="p-2 rounded-lg text-gray-400 hover:bg-gray-100 transition-colors shrink-0"
                        aria-expanded={open}
                        aria-label={open ? "접기" : "펼치기"}
                      >
                        <ChevronDown className={`w-4 h-4 transition-transform ${open ? "" : "-rotate-90"}`} />
                      </button>
                    </div>

                    {open && (
                      <div className="p-4 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
                        {g.items.map((r) => (
                          <div key={r.id} className="group relative rounded-xl overflow-hidden border border-gray-200">
                            <img src={r.image} alt="" className="w-full aspect-[4/3] object-cover bg-gray-900" />
                            <div className="px-2 py-1.5 space-y-1">
                              <div className="flex flex-wrap gap-1">
                                <span className="px-1.5 py-0.5 bg-gray-100 text-gray-700 rounded text-[10px] font-mono font-bold">
                                  {r.settings.focalMm}mm
                                </span>
                                <span className="px-1.5 py-0.5 bg-indigo-50 text-indigo-700 rounded text-[10px] font-bold">
                                  {r.settings.timeLabel}
                                </span>
                              </div>
                              <div className="text-[10px] font-mono text-gray-400">
                                ROT {r.settings.rotation}° · TILT {r.settings.tilt}°
                              </div>
                              {/* Measured server-side, not guessed. Saying so keeps a
                                  near-copy from being filed as a new angle. */}
                              {r.cameraMoved === false && (
                                <div className="text-[10px] font-bold text-amber-700">앵글 미반영</div>
                              )}
                            </div>
                            <button
                              onClick={() => void deleteRender(r.id)}
                              aria-label="컷 삭제"
                              className="absolute top-1.5 right-1.5 p-1.5 rounded-full bg-black/45 backdrop-blur-sm hover:bg-black/70 text-white transition-all opacity-0 group-hover:opacity-100"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </section>
                );
              })}
            </div>
          ))}

        {(saved.length > 0 || conversations.length > 0) && (
          <div className="pt-4 border-t border-gray-200">
            <button
              onClick={() => {
                if (confirm("저장한 공간과 대화를 모두 삭제할까요? 되돌릴 수 없습니다.")) clearAll();
              }}
              className="text-sm font-semibold text-gray-500 hover:text-rose-600 transition-colors"
            >
              이 브라우저의 활동 기록 전체 삭제
            </button>
          </div>
        )}
      </main>
    </div>
  );
}

const EmptyState: React.FC<{
  icon: React.ReactNode;
  title: string;
  body: string;
  cta: { href: string; label: string };
}> = ({ icon, title, body, cta }) => (
  <div className="bg-white border border-gray-200 rounded-3xl p-12 text-center shadow-sm">
    <div className="w-14 h-14 rounded-2xl bg-gray-100 flex items-center justify-center mx-auto">{icon}</div>
    <h3 className="text-lg font-bold text-gray-900 mt-4">{title}</h3>
    <p className="text-sm text-gray-500 mt-1.5 font-medium">{body}</p>
    <Link
      href={cta.href}
      className="inline-block mt-5 px-5 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-bold rounded-xl transition-colors"
    >
      {cta.label}
    </Link>
  </div>
);
