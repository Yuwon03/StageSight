"use client";

/**
 * Everything that belongs to *this user* rather than to the catalog: saved
 * locations and script-matching conversations.
 *
 * There is no account system yet. This keeps the data in localStorage behind an
 * interface shaped like the API that will eventually replace it — the record
 * types are what a server would return, every mutation goes through one
 * function, and reads go through hooks. When accounts arrive, `load`/`persist`
 * become fetch calls and no component changes.
 *
 * Deliberate choices:
 * - One key, one JSON blob. The data is small (ids and short strings) and it is
 *   always read and written together, so splitting it only invites the halves to
 *   disagree.
 * - `schema` is stored alongside. A future migration can read the old shape
 *   instead of throwing the user's saved list away.
 * - Every localStorage access is wrapped. Private windows and storage-blocked
 *   browsers throw on access, and a scout losing the catalog because their saved
 *   list could not be read would be absurd.
 * - Snapshots are frozen module state, not fresh objects, because
 *   useSyncExternalStore compares them by identity.
 */

import { ChatMessage, KoreanLocation } from "@/types";

const KEY = "stagesight:user:v1";
const SCHEMA = 1;

/** A listing the user hearted. Denormalised on purpose: the profile page must
 *  render the saved list without waiting on 12,000 listings to load, and it must
 *  still say something useful if the listing is later delisted. */
export interface SavedLocation {
  id: string;
  name: string;
  region: string;
  category: string;
  image: string;
  pricePerHour: number;
  savedAt: string;
}

/** One script-matching thread.
 *
 *  A thread owns the whole working state, not just the transcript: reopening it
 *  has to put the screenplay back in the textarea and the matched venues back in
 *  the list, or "continue this conversation" is a lie — the model still has the
 *  context but the user is looking at a blank editor.
 */
export interface Conversation {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  messages: ChatMessage[];
  /** Scene lines from the latest analysis, sent back as context on follow-ups. */
  sceneContext?: string;
  /** How many analyses have run in this thread; shown as "[분석 #n]". */
  analysisCount: number;
  /** The screenplay as it was when this thread was last used. */
  scriptText?: string;
  /** Name of the uploaded file, if the script came from one. */
  scriptFilename?: string;
  /** Every venue surfaced in this thread, analysis and chat alike, newest
   *  first. Stored denormalised so the list renders without refetching. */
  locations?: ThreadLocation[];
  /** Per-scene match cards from the most recent analysis. */
  scenes?: ThreadScene[];
}

/** A venue as it appeared in a thread. Trimmed to what the list renders. */
export interface ThreadLocation {
  id: string;
  name: string;
  region: string;
  category: string;
  image: string;
  pricePerHour: number;
  /** Which turn produced it, so the list can group by origin. */
  origin: string;
}

export interface ThreadScene {
  sceneNumber: string;
  sceneTitle: string;
  sceneSummary: string;
  timeOfDay: string;
  reason: string;
  locationId: string;
}

export interface Profile {
  id: string;
  displayName: string;
  createdAt: string;
}

export interface UserState {
  schema: number;
  profile: Profile;
  saved: SavedLocation[];
  conversations: Conversation[];
}

// ── State ───────────────────────────────────────────────────────────────────

function newId(prefix: string): string {
  // crypto.randomUUID is unavailable on http:// origins in some browsers.
  const rand =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID().slice(0, 8)
      : Math.random().toString(36).slice(2, 10);
  return `${prefix}_${Date.now().toString(36)}${rand}`;
}

function emptyState(): UserState {
  return {
    schema: SCHEMA,
    profile: { id: newId("local"), displayName: "게스트", createdAt: new Date().toISOString() },
    saved: [],
    conversations: [],
  };
}

/** The snapshot the server renders. Must be a stable reference and must be the
 *  *empty* state — the server cannot know what is in the browser's storage, and
 *  rendering a guess would produce a hydration mismatch. */
const SERVER_STATE: UserState = Object.freeze(emptyState()) as UserState;

let state: UserState = SERVER_STATE;
let hydrated = false;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((l) => l());
}

function load(): UserState {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return emptyState();
    const parsed = JSON.parse(raw) as Partial<UserState>;
    // Tolerate anything: a half-written or hand-edited blob must not take the
    // page down, it must degrade to "nothing saved yet".
    return {
      schema: SCHEMA,
      profile: parsed.profile?.id ? parsed.profile : emptyState().profile,
      saved: Array.isArray(parsed.saved) ? parsed.saved.filter((s) => s && s.id) : [],
      conversations: Array.isArray(parsed.conversations)
        ? parsed.conversations.filter((c) => c && c.id && Array.isArray(c.messages))
        : [],
    };
  } catch {
    return emptyState();
  }
}

function persist() {
  try {
    localStorage.setItem(KEY, JSON.stringify(state));
  } catch {
    // Quota exceeded or storage blocked. The in-memory state is still correct
    // for this session; silently losing the write is better than crashing.
  }
}

/** Called once from the client before the first read. */
export function hydrate() {
  if (hydrated || typeof window === "undefined") return;
  hydrated = true;
  state = load();
  emit();
}

function update(fn: (s: UserState) => UserState) {
  if (!hydrated) hydrate();
  state = fn(state);
  persist();
  emit();
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getSnapshot(): UserState {
  return state;
}

export function getServerSnapshot(): UserState {
  return SERVER_STATE;
}

// ── Saved locations ─────────────────────────────────────────────────────────

export function isSaved(id: string): boolean {
  return state.saved.some((s) => s.id === id);
}

export function toggleSaved(loc: KoreanLocation): boolean {
  let nowSaved = false;
  update((s) => {
    const existing = s.saved.find((x) => x.id === loc.id);
    if (existing) return { ...s, saved: s.saved.filter((x) => x.id !== loc.id) };
    nowSaved = true;
    const entry: SavedLocation = {
      id: loc.id,
      name: loc.name,
      region: loc.region,
      category: loc.category,
      image: loc.images?.[0] ?? "",
      pricePerHour: loc.price_per_hour ?? 0,
      savedAt: new Date().toISOString(),
    };
    // Newest first — the profile page reads in this order.
    return { ...s, saved: [entry, ...s.saved] };
  });
  return nowSaved;
}

export function removeSaved(id: string) {
  update((s) => ({ ...s, saved: s.saved.filter((x) => x.id !== id) }));
}

// ── Conversations ───────────────────────────────────────────────────────────

/** First line of the first user message, which is what the thread is about. */
function deriveTitle(messages: ChatMessage[]): string {
  const first = messages.find((m) => m.role === "user");
  if (!first) return "새 대화";
  const line = first.content.trim().split("\n")[0];
  return line.length > 38 ? `${line.slice(0, 38)}…` : line || "새 대화";
}

export function createConversation(seed: ChatMessage[] = []): string {
  const id = newId("chat");
  const now = new Date().toISOString();
  update((s) => ({
    ...s,
    conversations: [
      { id, title: "새 대화", createdAt: now, updatedAt: now, messages: seed, analysisCount: 0 },
      ...s.conversations,
    ],
  }));
  return id;
}

export function saveConversation(
  id: string,
  patch: {
    messages?: ChatMessage[];
    sceneContext?: string;
    analysisCount?: number;
    scriptText?: string;
    scriptFilename?: string;
    locations?: ThreadLocation[];
    scenes?: ThreadScene[];
    /** An explicit name. Wins over the derived one and is never overwritten. */
    title?: string;
  }
) {
  update((s) => ({
    ...s,
    conversations: s.conversations.map((c) => {
      if (c.id !== id) return c;
      const messages = patch.messages ?? c.messages;
      return {
        ...c,
        ...patch,
        messages,
        // Precedence: an explicit title (the model's, or the user's rename)
        // always wins; otherwise an untitled thread takes its first question.
        title: patch.title?.trim() || (c.title === "새 대화" ? deriveTitle(messages) : c.title),
        updatedAt: new Date().toISOString(),
      };
    }),
  }));
}

export function renameConversation(id: string, title: string) {
  const clean = title.trim().slice(0, 60);
  if (!clean) return;
  update((s) => ({
    ...s,
    conversations: s.conversations.map((c) => (c.id === id ? { ...c, title: clean } : c)),
  }));
}

export function deleteConversation(id: string) {
  update((s) => ({ ...s, conversations: s.conversations.filter((c) => c.id !== id) }));
}

export function getConversation(id: string): Conversation | undefined {
  return state.conversations.find((c) => c.id === id);
}

/** Append venues to a thread's running list, newest first, without duplicates.
 *  The same venue often comes back from several turns; the first sighting keeps
 *  its position so the list does not reshuffle under the user. */
export function addThreadLocations(id: string, incoming: ThreadLocation[]) {
  if (!incoming.length) return;
  update((s) => ({
    ...s,
    conversations: s.conversations.map((c) => {
      if (c.id !== id) return c;
      const have = new Set((c.locations ?? []).map((l) => l.id));
      const fresh = incoming.filter((l) => !have.has(l.id));
      if (!fresh.length) return c;
      return { ...c, locations: [...fresh, ...(c.locations ?? [])], updatedAt: new Date().toISOString() };
    }),
  }));
}

// ── Profile ─────────────────────────────────────────────────────────────────

export function setDisplayName(name: string) {
  const clean = name.trim().slice(0, 40);
  if (!clean) return;
  update((s) => ({ ...s, profile: { ...s.profile, displayName: clean } }));
}

/** Wipes everything this browser holds. Used by the profile page. */
export function clearAll() {
  update(() => emptyState());
}
