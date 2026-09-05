"use client";

/**
 * Script → location workspace.
 *
 * Three panes at a fixed viewport height so each scrolls independently and the
 * page itself never grows: thread sidebar (collapsible), the screenplay editor
 * over the running list of venues, and the conversation.
 *
 * The thread owns the whole working state — screenplay, scene cards, every
 * venue surfaced so far — not just the transcript. Reopening a thread restores
 * what the user was looking at, which is the only reading of "continue this
 * conversation" that is not a lie.
 */

import React, { useState, useRef, useEffect, useCallback } from "react";
import { ScriptAnalysisResponse, ChatMessage, KoreanLocation } from "@/types";
import { useUserState } from "@/lib/useUser";
import {
  addThreadLocations,
  createConversation,
  deleteConversation,
  renameConversation,
  saveConversation,
  ThreadLocation,
  ThreadScene,
} from "@/lib/userStore";
import {
  matchScript,
  sendScoutingChatMessage,
  uploadScriptFile,
  resolveImageUrl,
  BackendUnreachableError,
  UploadedScript,
} from "@/lib/api";
import {
  Sparkles, Send, Bot, Film, ArrowRight, RefreshCw, Trees, DollarSign,
  Landmark, Truck, Upload, ShieldAlert, History, Plus, Trash2, X,
  PanelLeftClose, PanelLeft, Quote, MapPin, AlertTriangle, Pencil,
} from "lucide-react";
import Link from "next/link";

const DEFAULT_SCRIPT = `[씬 14: 실내 다이닝룸 - 일몰]
엘레나와 마커스가 묵직한 원목 식탁을 사이에 두고 마주 앉아 있다.
엘레나 뒤편 서쪽 창문으로 쏟아지는 눈부신 황금빛 일몰 햇살이 식어가는 찻잔의 김을 비춘다.
카메라는 두 인물을 담는 와이드 투샷(Wide Two-shot)으로 시작하여, 둘 사이의 팽팽한 침묵 속으로 서서히 앞으로 돌리-인(Dolly-in)한다.

[씬 15: 야외 깊은 숲속 오솔길 - 황혼에서 밤]
엘레나가 문을 박차고 나와 안개 낀 숲길로 뛰어 들어간다.
키 큰 잣나무들 사이로 푸른빛 박명이 깔리고, 멀리서 바스락거리는 추격자의 발소리가 들려온다.
카메라는 핸드헬드로 흔들리며 나무 사이를 가로지르는 엘레나의 긴박한 호흡을 따라간다.`;

const GREETING =
  "안녕하세요! 각본 기반 AI 로케이션 어시스턴트입니다. 각본을 입력하고 분석을 누르면 씬별로 장소를 찾아드립니다. 각본에서 특정 대목을 드래그해 첨부하면 그 대목만 놓고 상의할 수도 있어요.";

const DEFAULT_SCRIPT_EN = `[SCENE 14: INTERIOR DINING ROOM - SUNSET]
Elena and Marcus sit opposite each other across a heavy wooden table.
Brilliant golden sunlight pours through the west-facing window behind Elena and catches the steam rising from a cooling teacup.
The camera begins on a wide two-shot and slowly dollies into the tense silence between them.

[SCENE 15: EXTERIOR FOREST PATH - DUSK TO NIGHT]
Elena bursts through the door and runs into a misty forest path.
Blue twilight settles between tall pine trees as a pursuer's footsteps rustle in the distance.
The handheld camera follows Elena's urgent breathing as she cuts between the trees.`;

const GREETING_EN =
  "Hello! I am your screenplay-based AI location assistant. Add a script and run the analysis to find locations for each scene. You can also highlight and attach a specific passage to discuss it in the chat.";

function timeAgo(iso: string, locale: "ko" | "en" = "ko"): string {
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (locale === "en") {
    if (mins < 1) return "now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.round(hrs / 24)}d ago`;
  }
  if (mins < 1) return "방금";
  if (mins < 60) return `${mins}분 전`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}시간 전`;
  return `${Math.round(hrs / 24)}일 전`;
}

const toThreadLocation = (l: KoreanLocation, origin: string): ThreadLocation => ({
  id: l.id,
  name: l.name,
  region: l.region,
  category: l.category,
  image: l.images?.[0] ?? "",
  pricePerHour: l.price_per_hour ?? 0,
  origin,
});

export const ScriptMatcherPanel: React.FC<{ locale?: "ko" | "en" }> = ({ locale = "ko" }) => {
  const en = locale === "en";
  const t = (ko: string, english: string) => (en ? english : ko);
  const displayTitle = (title: string) => (en && title === "새 대화" ? "New conversation" : title);
  const initialScript = en ? DEFAULT_SCRIPT_EN : DEFAULT_SCRIPT;
  const greeting = en ? GREETING_EN : GREETING;
  const localizeDefaultScript = (text?: string) => {
    if (!text) return initialScript;
    if (en && text === DEFAULT_SCRIPT) return DEFAULT_SCRIPT_EN;
    if (!en && text === DEFAULT_SCRIPT_EN) return DEFAULT_SCRIPT;
    return text;
  };
  const localizeSystemMessages = (items: ChatMessage[]): ChatMessage[] =>
    items.map((message) => {
      if (message.role !== "assistant") return message;
      if (en && message.content === GREETING) return { ...message, content: GREETING_EN };
      if (!en && message.content === GREETING_EN) return { ...message, content: GREETING };
      return message;
    });
  const { conversations } = useUserState();
  const [activeId, setActiveId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const pickedInitial = useRef(false);

  const active = conversations.find((c) => c.id === activeId) ?? null;
  const messages: ChatMessage[] = localizeSystemMessages(active?.messages ?? []);
  const threadLocations = active?.locations ?? [];
  const threadScenes = active?.scenes ?? [];

  const [scriptText, setScriptText] = useState(initialScript);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [inputMsg, setInputMsg] = useState("");
  const [isChatting, setIsChatting] = useState(false);
  const [excerpt, setExcerpt] = useState<string | null>(null);
  const [hasSelection, setHasSelection] = useState(false);

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadInfo, setUploadInfo] = useState<UploadedScript | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const MAX_CLIENT_BYTES = 10 * 1024 * 1024;

  // Pick a thread once the store has hydrated: the one the profile linked to,
  // else the most recent, else a fresh one. Guarded so a re-render cannot spawn
  // a second empty conversation.
  useEffect(() => {
    if (pickedInitial.current) return;
    const wanted = new URLSearchParams(window.location.search).get("chat");
    if (wanted && conversations.some((c) => c.id === wanted)) {
      pickedInitial.current = true;
      setActiveId(wanted);
      return;
    }
    if (conversations.length > 0) {
      pickedInitial.current = true;
      setActiveId(conversations[0].id);
      return;
    }
    if (typeof window !== "undefined") {
      pickedInitial.current = true;
      setActiveId(createConversation([{ role: "assistant", content: greeting }]));
    }
  }, [conversations]);

  // Restoring the editor is what makes a thread resumable. Keyed on the id, not
  // on the thread object, or every autosave would overwrite what is being typed.
  useEffect(() => {
    if (!activeId) return;
    const c = conversations.find((x) => x.id === activeId);
    setScriptText(localizeDefaultScript(c?.scriptText));
    setUploadInfo(null);
    setUploadError(null);
    setExcerpt(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages.length, isChatting]);

  const ensureThread = (): string => {
    if (activeId) return activeId;
    const id = createConversation([{ role: "assistant", content: greeting }]);
    setActiveId(id);
    return id;
  };

  const startNewThread = () => {
    setActiveId(createConversation([{ role: "assistant", content: greeting }]));
    setScriptText(initialScript);
    setExcerpt(null);
  };

  // Persist the screenplay as the user edits, debounced so every keystroke is
  // not a localStorage write.
  useEffect(() => {
    if (!activeId) return;
    const t = setTimeout(() => saveConversation(activeId, { scriptText }), 700);
    return () => clearTimeout(t);
  }, [scriptText, activeId]);

  const readSelection = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    setHasSelection(el.selectionEnd - el.selectionStart >= 4);
  }, []);

  const attachSelection = () => {
    const el = textareaRef.current;
    if (!el) return;
    const text = scriptText.slice(el.selectionStart, el.selectionEnd).trim();
    if (text.length < 4) return;
    setExcerpt(text);
    setHasSelection(false);
  };

  const handleFilePicked = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setUploadError(null);
    setUploadInfo(null);

    // Cheap client-side gate. The server still decides from magic bytes — an
    // extension is trivially forged and is not a security check.
    if (!/\.(pdf|docx)$/i.test(file.name)) {
      setUploadError(t("PDF 또는 Word(.docx) 파일만 올릴 수 있습니다.", "Only PDF or Word (.docx) files are supported."));
      return;
    }
    if (file.size > MAX_CLIENT_BYTES) {
      setUploadError(t(`파일이 너무 큽니다 (${Math.round(file.size / 1024 / 1024)}MB). 최대 10MB.`, `The file is too large (${Math.round(file.size / 1024 / 1024)}MB). Maximum 10MB.`));
      return;
    }
    setIsUploading(true);
    try {
      const result = await uploadScriptFile(file);
      setScriptText(result.script_text);
      setUploadInfo(result);
      if (activeId) saveConversation(activeId, { scriptText: result.script_text, scriptFilename: result.filename });
    } catch (err) {
      setUploadError(
        err instanceof BackendUnreachableError ? t("백엔드 서버에 연결할 수 없습니다.", "The backend server is unavailable.") : (err as Error).message
      );
    } finally {
      setIsUploading(false);
    }
  };

  const handleRunScriptAnalysis = async () => {
    const threadId = ensureThread();
    const thread = conversations.find((c) => c.id === threadId);
    const base = localizeSystemMessages(thread?.messages ?? []);
    const run = (thread?.analysisCount ?? 0) + 1;

    setIsAnalyzing(true);
    try {
      const res: ScriptAnalysisResponse = await matchScript(scriptText, en ? "Untitled project" : "마지막 일몰", locale);

      const suggested = Array.from(
        new Map(res.scenes.map((s) => [s.primary_location.id, s.primary_location])).values()
      );
      const sceneLines = res.scenes
        .map((s) => `· ${s.scene_number} ${s.scene_title} → ${s.primary_location.name.slice(0, 30)}`)
        .join("\n");

      const scenes: ThreadScene[] = res.scenes.map((s) => ({
        sceneNumber: s.scene_number,
        sceneTitle: s.scene_title,
        sceneSummary: s.scene_summary,
        timeOfDay: s.time_of_day,
        reason: s.ai_recommendation_reason,
        locationId: s.primary_location.id,
      }));

      saveConversation(threadId, {
        messages: [
          ...base,
          { role: "user", content: t(`각본 분석 실행 (#${run})`, `Run screenplay analysis (#${run})`) },
          {
            role: "assistant",
            content:
              t(`[분석 #${run}] ${res.total_scenes_detected}개 씬을 찾아 장소를 매칭했습니다.\n${sceneLines}\n\n${res.overall_production_advice}\n\n이 결과를 두고 계속 물어보세요.`, `[Analysis #${run}] Matched locations to ${res.total_scenes_detected} scenes.\n${sceneLines}\n\n${res.overall_production_advice}\n\nYou can continue asking questions about these results.`),
            suggested_locations: suggested,
          },
        ],
        sceneContext: sceneLines,
        analysisCount: run,
        scriptText,
        scenes,
        // The model named the thread in the same call that matched the scenes.
        // Only on the first analysis: a later re-run must not rename a thread
        // the user has been working in, still less one they renamed themselves.
        title: run === 1 ? res.thread_title || undefined : undefined,
      });
      addThreadLocations(threadId, suggested.map((l) => toThreadLocation(l, t(`분석 #${run}`, `Analysis #${run}`))));
    } catch (err) {
      saveConversation(threadId, {
        messages: [
          ...base,
          {
            role: "assistant",
            error: true,
            content:
              err instanceof BackendUnreachableError
                ? t("백엔드 서버에 연결할 수 없어 분석하지 못했습니다.", "The screenplay could not be analysed because the backend is unavailable.")
                : t(`각본 분석에 실패했습니다. (${(err as Error).message})`, `Screenplay analysis failed. (${(err as Error).message})`),
          },
        ],
      });
    } finally {
      setIsAnalyzing(false);
    }
  };

  const handleSendChat = async (textToSend?: string) => {
    const query = (textToSend || inputMsg).trim();
    if (!query) return;

    const threadId = ensureThread();
    const thread = conversations.find((c) => c.id === threadId);
    const attached = excerpt;
    const updated: ChatMessage[] = [
      ...localizeSystemMessages(thread?.messages ?? []),
      { role: "user", content: query, script_excerpt: attached ?? undefined },
    ];

    saveConversation(threadId, { messages: updated });
    if (!textToSend) setInputMsg("");
    setExcerpt(null);
    setIsChatting(true);

    try {
      const res = await sendScoutingChatMessage(updated, thread?.sceneContext, undefined, attached ?? undefined, locale);
      saveConversation(threadId, {
        messages: [
          ...updated,
          {
            role: "assistant",
            content: res.reply,
            suggested_locations: res.suggested_locations,
            applied_filter_summary: res.applied_filter_summary,
            model: res.model,
          },
        ],
      });
      addThreadLocations(threadId, (res.suggested_locations ?? []).map((l) => toThreadLocation(l, t("대화 추천", "Chat recommendation"))));
    } catch (err) {
      // The assistant must not appear to answer when it did not. Say what broke.
      const msg = (err as Error).message || "";
      saveConversation(threadId, {
        messages: [
          ...updated,
          {
            role: "assistant",
            error: true,
            content: msg.includes("503") || msg.includes("GEMINI_API_KEY")
              ? t("AI 응답을 받을 수 없습니다 — GEMINI_API_KEY가 설정되지 않았습니다. 키 없이 임의로 추천하지 않습니다.", "An AI response is unavailable because GEMINI_API_KEY is not configured. The app will not invent recommendations without it.")
              : err instanceof BackendUnreachableError
              ? t("백엔드 서버에 연결할 수 없습니다.", "The backend server is unavailable.")
              : t(`AI 응답에 실패했습니다. (${msg})`, `AI response failed. (${msg})`),
          },
        ],
      });
    } finally {
      setIsChatting(false);
    }
  };

  return (
    <div className="flex gap-4 h-[calc(100dvh-13rem)] min-h-[560px]">
      {/* ── Thread sidebar ─────────────────────────────────────────────── */}
      {sidebarOpen ? (
        <aside className="w-60 shrink-0 flex flex-col bg-white border border-gray-200 rounded-2xl overflow-hidden shadow-sm">
          <div className="px-3 py-3 flex items-center justify-between border-b border-gray-100">
            <span className="text-sm font-bold text-gray-900 flex items-center gap-1.5">
              <History className="w-4 h-4 text-indigo-600" />
              {t("대화", "Conversations")} {conversations.length}
            </span>
            <button
              onClick={() => setSidebarOpen(false)}
              title={t("사이드바 접기", "Close sidebar")}
              className="p-1.5 rounded-lg text-gray-400 hover:bg-gray-100 transition-colors"
            >
              <PanelLeftClose className="w-4 h-4" />
            </button>
          </div>
          <div className="p-2.5">
            <button
              onClick={startNewThread}
              className="w-full flex items-center gap-2 px-3 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-bold transition-colors"
            >
              <Plus className="w-4 h-4" />{t("새 대화", "New conversation")}
            </button>
          </div>
          <div className="flex-1 overflow-y-auto px-2.5 pb-2.5 space-y-1">
            {conversations.map((c) => (
              <div
                key={c.id}
                className={`group flex items-start rounded-xl transition-colors ${
                  c.id === activeId ? "bg-indigo-50 border border-indigo-200" : "hover:bg-gray-50 border border-transparent"
                }`}
              >
                {renamingId === c.id ? (
                  <input
                    autoFocus
                    value={renameDraft}
                    maxLength={60}
                    onChange={(e) => setRenameDraft(e.target.value)}
                    onBlur={() => { renameConversation(c.id, renameDraft); setRenamingId(null); }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") { renameConversation(c.id, renameDraft); setRenamingId(null); }
                      if (e.key === "Escape") setRenamingId(null);
                    }}
                    className="flex-1 min-w-0 mx-2 my-1.5 px-2 py-1.5 border border-indigo-300 rounded-lg text-[13px] font-semibold text-gray-900 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  />
                ) : (
                  <>
                    <button
                      onClick={() => setActiveId(c.id)}
                      onDoubleClick={() => { setRenameDraft(c.title); setRenamingId(c.id); }}
                      className="flex-1 min-w-0 text-left px-2.5 py-2"
                    >
                      <div className={`text-[13px] font-semibold truncate ${c.id === activeId ? "text-indigo-900" : "text-gray-800"}`}>
                        {displayTitle(c.title)}
                      </div>
                      <div className="text-[11px] text-gray-400 font-medium mt-0.5 truncate">
                        {timeAgo(c.updatedAt, locale)} · {t("장소", "locations")} {c.locations?.length ?? 0}
                      </div>
                    </button>
                    <div className="flex items-center opacity-0 group-hover:opacity-100 transition-opacity">
                      <button
                        onClick={() => { setRenameDraft(c.title); setRenamingId(c.id); }}
                        title={t("이름 바꾸기", "Rename")}
                        className="p-1.5 mt-1.5 rounded-lg text-gray-300 hover:text-indigo-600 hover:bg-indigo-50 transition-colors"
                      >
                        <Pencil className="w-3.5 h-3.5" />
                      </button>
                      <button
                        onClick={() => {
                          deleteConversation(c.id);
                          if (c.id === activeId) {
                            pickedInitial.current = false;
                            setActiveId(null);
                          }
                        }}
                        title={t("삭제", "Delete")}
                        className="p-1.5 mt-1.5 mr-1 rounded-lg text-gray-300 hover:text-rose-600 hover:bg-rose-50 transition-colors"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </>
                )}
              </div>
            ))}
          </div>
        </aside>
      ) : (
        <button
          onClick={() => setSidebarOpen(true)}
          title={t("대화 목록 열기", "Open conversations")}
          className="shrink-0 h-fit p-2.5 bg-white border border-gray-200 rounded-xl shadow-sm hover:border-gray-300 transition-colors"
        >
          <PanelLeft className="w-4 h-4 text-gray-600" />
        </button>
      )}

      {/* ── Script editor (top) + running venue list (bottom) ──────────── */}
      <div className="flex-1 min-w-0 flex flex-col gap-4">
        <div className="h-[46%] min-h-[240px] flex flex-col bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden">
          <div className="px-5 py-3 flex items-center justify-between flex-wrap gap-2 border-b border-gray-100">
            <div className="flex items-center gap-2 font-bold text-gray-900 text-sm">
              <Film className="w-4 h-4 text-indigo-600" />
              {t("대본 입력 및 AI 씬 추출", "Screenplay input and AI scene extraction")}
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={isUploading}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-gray-700 bg-white border border-gray-300 rounded-full hover:border-gray-400 transition-colors disabled:opacity-60"
              >
                {isUploading ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
                {isUploading ? t("확인 중", "Checking") : t("파일", "File")}
              </button>
              <button
                onClick={() => { setScriptText(initialScript); setUploadInfo(null); setUploadError(null); }}
                className="text-xs text-indigo-600 hover:text-indigo-700 font-semibold underline"
              >
                {t("샘플", "Sample")}
              </button>
            </div>
          </div>

          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            className="hidden"
            onChange={handleFilePicked}
          />

          <div className="px-5 pt-3 space-y-2">
            {uploadError && (
              <div className="flex items-start gap-2 p-2.5 bg-rose-50 border border-rose-200 rounded-xl text-xs font-semibold text-rose-800">
                <ShieldAlert className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                {uploadError}
              </div>
            )}
            {uploadInfo && (
              <div className="p-2.5 bg-emerald-50 border border-emerald-200 rounded-xl text-xs font-bold text-emerald-900">
                {uploadInfo.filename} · {uploadInfo.chars.toLocaleString()} {t("자", "characters")}
              </div>
            )}
          </div>

          <div className="flex-1 min-h-0 px-5 py-3 relative">
            <textarea
              ref={textareaRef}
              value={scriptText}
              onChange={(e) => setScriptText(e.target.value)}
              onSelect={readSelection}
              onKeyUp={readSelection}
              onMouseUp={readSelection}
              className="w-full h-full resize-none p-3.5 bg-gray-50 border border-gray-200 rounded-xl text-sm font-medium text-gray-800 focus:outline-none focus:ring-2 focus:ring-indigo-600 focus:border-transparent leading-relaxed"
              placeholder={t("각본 또는 씬 묘사를 입력하세요. 특정 대목을 드래그하면 챗에 첨부할 수 있어요.", "Enter a screenplay or scene description. Highlight a passage to attach it to the chat.")}
            />
            {hasSelection && (
              <button
                onClick={attachSelection}
                className="absolute bottom-6 right-8 flex items-center gap-1.5 px-3.5 py-2 bg-gray-900 hover:bg-black text-white text-xs font-bold rounded-full shadow-lg transition-colors animate-in fade-in"
              >
                <Quote className="w-3.5 h-3.5" />
                {t("이 대목을 챗에 첨부", "Attach passage to chat")}
              </button>
            )}
          </div>

          <div className="px-5 pb-4">
            <button
              onClick={handleRunScriptAnalysis}
              disabled={isAnalyzing}
              className="w-full py-3 bg-indigo-600 hover:bg-indigo-700 text-white font-bold text-sm rounded-xl shadow-md shadow-indigo-600/20 transition-all flex items-center justify-center gap-2 disabled:opacity-50"
            >
              {isAnalyzing ? <><RefreshCw className="w-4 h-4 animate-spin" />{t("Gemini가 분석 중…", "Gemini is analysing...")}</> : <><Sparkles className="w-4 h-4" />{t("분석 및 장소 매칭 시작", "Analyse and match locations")}</>}
            </button>
          </div>
        </div>

        {/* Running list of every venue this thread has surfaced. */}
        <div className="flex-1 min-h-0 flex flex-col bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden">
          <div className="px-5 py-3 border-b border-gray-100 flex items-center justify-between">
            <div className="flex items-center gap-2 font-bold text-gray-900 text-sm">
              <MapPin className="w-4 h-4 text-indigo-600" />
              {t("이 대화에서 나온 장소", "Locations in this conversation")}
              <span className="px-2 py-0.5 bg-gray-100 text-gray-600 rounded-full text-[11px]">
                {threadLocations.length}
              </span>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto p-4 space-y-2">
            {threadLocations.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center text-center text-sm text-gray-400 font-medium gap-1">
                <MapPin className="w-7 h-7 text-gray-300" />
                {t("아직 없습니다. 분석을 돌리거나 AI에게 물어보세요.", "No locations yet. Run the analysis or ask the AI.")}
              </div>
            ) : (
              threadLocations.map((l) => {
                const scene = threadScenes.find((s) => s.locationId === l.id);
                return (
                  <Link key={l.id} href={`${en ? "/en" : ""}/location/${l.id}?chat=${activeId ?? ""}`}>
                    <div className="group flex items-center gap-3 p-2.5 border border-gray-200 hover:border-indigo-300 hover:bg-indigo-50/40 rounded-xl transition-all mb-2">
                      <img
                        src={resolveImageUrl(l.image)}
                        alt=""
                        className="w-16 h-16 rounded-lg object-cover bg-gray-100 shrink-0"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5">
                          <span className="px-1.5 py-0.5 bg-gray-100 text-gray-600 rounded text-[10px] font-bold">
                            {l.origin}
                          </span>
                          {scene && (
                            <span className="px-1.5 py-0.5 bg-indigo-50 text-indigo-700 rounded text-[10px] font-bold truncate">
                              {scene.sceneNumber} {scene.timeOfDay}
                            </span>
                          )}
                        </div>
                        <div className="text-sm font-bold text-gray-900 truncate mt-1">{l.name}</div>
                        <div className="text-xs text-gray-500 font-medium">
                          {l.region} · {l.pricePerHour > 0 ? `₩${l.pricePerHour.toLocaleString()}/h` : t("가격 문의", "Price on request")}
                        </div>
                      </div>
                      <ArrowRight className="w-4 h-4 text-gray-300 group-hover:text-indigo-600 transition-colors shrink-0" />
                    </div>
                  </Link>
                );
              })
            )}
          </div>
        </div>
      </div>

      {/* ── Conversation ───────────────────────────────────────────────── */}
      <div className="w-[36%] min-w-[340px] shrink-0 flex flex-col bg-white border border-gray-200 rounded-2xl overflow-hidden shadow-sm">
        <div className="px-5 py-3 border-b border-gray-200 flex items-center gap-2.5">
          <Bot className="w-5 h-5 text-indigo-600 shrink-0" />
          <div className="min-w-0 flex-1">
            <div className="font-bold text-gray-900 text-sm truncate">{active ? displayTitle(active.title) : t("새 대화", "New conversation")}</div>
            <div className="text-[11px] text-gray-400 font-medium">
              {t(`${messages.length}개 메시지`, `${messages.length} messages`)}{active && ` · ${timeAgo(active.updatedAt, locale)}`}
            </div>
          </div>
        </div>

        <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-3 bg-gray-50">
          {messages.map((msg, i) => (
            <div key={i} className={`flex flex-col gap-1.5 ${msg.role === "user" ? "items-end" : "items-start"}`}>
              {msg.script_excerpt && (
                <div className="max-w-[88%] px-3 py-2 bg-gray-900 text-gray-100 rounded-xl text-[11px] leading-relaxed border-l-2 border-indigo-400">
                  <div className="flex items-center gap-1 font-bold text-indigo-300 mb-1">
                    <Quote className="w-3 h-3" />{t("각본 발췌", "Screenplay excerpt")}
                  </div>
                  <div className="whitespace-pre-wrap line-clamp-4">{msg.script_excerpt}</div>
                </div>
              )}
              <div
                className={`max-w-[88%] p-3.5 rounded-2xl text-sm leading-relaxed shadow-sm whitespace-pre-wrap ${
                  msg.role === "user"
                    ? "bg-indigo-600 text-white font-medium rounded-tr-sm"
                    : msg.error
                    ? "bg-rose-50 text-rose-900 border border-rose-200 rounded-tl-sm font-semibold"
                    : "bg-white text-gray-800 border border-gray-200 rounded-tl-sm"
                }`}
              >
                {msg.error && (
                  <div className="flex items-center gap-1.5 font-bold mb-1">
                    <AlertTriangle className="w-3.5 h-3.5" />{t("응답 실패", "Response failed")}
                  </div>
                )}
                {msg.content}
              </div>

              {/* Proof the answer came from a model, not from a lookup table. */}
              {msg.role === "assistant" && msg.model && (
                <div className="text-[10px] font-mono font-semibold text-gray-400 px-1">
                  {msg.model}
                  {msg.applied_filter_summary && ` · ${msg.applied_filter_summary}`}
                </div>
              )}

              {msg.suggested_locations && msg.suggested_locations.length > 0 && (
                <div className="w-full max-w-[92%] space-y-2 pt-0.5">
                  {Array.from(new Map(msg.suggested_locations.map((l) => [l.id, l])).values()).map((loc) => (
                    <Link key={loc.id} href={`${en ? "/en" : ""}/location/${loc.id}?chat=${activeId ?? ""}`}>
                      <div className="cursor-pointer p-2.5 bg-white hover:bg-gray-50 border border-gray-200 hover:border-indigo-300 rounded-xl flex items-center gap-3 transition-all shadow-sm mb-2">
                        <img src={resolveImageUrl(loc.images[0])} alt="" className="w-12 h-12 rounded-lg object-cover bg-gray-100" />
                        <div className="min-w-0">
                          <div className="font-bold text-gray-900 text-[13px] line-clamp-1">{loc.name}</div>
                          <div className="text-[11px] text-gray-500 mt-0.5">
                            {loc.region} · {loc.price_per_hour > 0 ? `₩${loc.price_per_hour.toLocaleString()}/h` : t("가격 문의", "Price on request")}
                          </div>
                        </div>
                      </div>
                    </Link>
                  ))}
                </div>
              )}
            </div>
          ))}

          {isChatting && (
            <div className="flex items-center gap-2 text-gray-500 text-sm py-1">
              <RefreshCw className="w-4 h-4 animate-spin text-indigo-500" />
              {t("Gemini가 카탈로그를 살펴보는 중…", "Gemini is searching the catalogue...")}
            </div>
          )}
        </div>

        {excerpt && (
          <div className="px-4 py-2.5 bg-gray-900 border-t border-gray-800 flex items-start gap-2">
            <Quote className="w-3.5 h-3.5 text-indigo-300 mt-0.5 shrink-0" />
            <div className="flex-1 min-w-0 text-[11px] text-gray-200 leading-relaxed line-clamp-2 whitespace-pre-wrap">
              {excerpt}
            </div>
            <button onClick={() => setExcerpt(null)} className="p-1 rounded text-gray-400 hover:text-white shrink-0">
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        <div className="px-4 py-2.5 bg-white border-t border-gray-200 flex items-center gap-2 overflow-x-auto scrollbar-none">
          {[
            { label: t("자연/숲", "Nature / forest"), icon: Trees, query: t("좀 더 자연이나 숲 느낌이 나는 곳으로 추천해줘", "Recommend locations with more of a natural or forest atmosphere") },
            { label: t("10만원 이하", "Under KRW 100k"), icon: DollarSign, query: t("시간당 10만원 이하 가성비 좋은 곳으로 찾아줘", "Find good-value locations under KRW 100,000 per hour") },
            { label: t("전통 한옥", "Traditional house"), icon: Landmark, query: t("전통 한옥이나 고택 느낌 장소도 추천해줘", "Recommend traditional Korean houses or heritage homes") },
            { label: t("주차 넓은 곳", "Easy truck parking"), icon: Truck, query: t("대형 탑차 주차가 편한 곳은?", "Which locations have convenient parking for a large production truck?") },
          ].map((chip, idx) => (
            <button
              key={idx}
              onClick={() => handleSendChat(chip.query)}
              disabled={isChatting}
              className="px-3.5 py-1.5 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-full text-xs font-semibold whitespace-nowrap flex items-center gap-1.5 transition-colors shrink-0 disabled:opacity-50"
            >
              <chip.icon className="w-3.5 h-3.5" />
              {chip.label}
            </button>
          ))}
        </div>

        <div className="p-3.5 bg-white border-t border-gray-200 flex items-center gap-2.5">
          <input
            type="text"
            value={inputMsg}
            onChange={(e) => setInputMsg(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSendChat();
              }
            }}
            placeholder={excerpt ? t("첨부한 대목에 대해 물어보세요…", "Ask about the attached passage...") : t("AI에게 조건을 말해보세요…", "Tell the AI what you need...")}
            className="flex-1 px-4 py-2.5 bg-gray-100 border-transparent rounded-full text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-indigo-600 focus:bg-white transition-all font-medium"
          />
          <button
            onClick={() => handleSendChat()}
            disabled={!inputMsg.trim() || isChatting}
            className="p-3 rounded-full bg-indigo-600 hover:bg-indigo-700 text-white disabled:opacity-50 transition-all shadow-md"
          >
            <Send className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
};
