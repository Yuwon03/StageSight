"use client";

/**
 * AI-generated frames the user made in the simulator.
 *
 * These live in IndexedDB, not in the localStorage blob the rest of the user
 * data uses: a single render is a base64 PNG of a megabyte or more, and a
 * handful of them would blow localStorage's ~5MB quota and take the saved
 * listings down with them. Metadata and image share a record here so there is
 * no second store to keep in sync.
 *
 * Records are pruned against the live catalog on read — a render of a listing
 * that has since been delisted is removed, because its detail page is gone and
 * a scout must not plan around a venue that can no longer be booked.
 */

const DB = "stagesight";
const STORE = "renders";
const VERSION = 1;

export interface RenderRecord {
  id: string;
  locationId: string;
  locationName: string;
  region: string;
  /** data:image/png;base64,… as returned by the simulator. */
  image: string;
  /** What the user dialled in, so the profile can describe the shot. */
  settings: {
    rotation: number;
    tilt: number;
    zoom: number;
    focalMm: number;
    timeLabel: string;
    lightPhase: string;
    dateLabel: string;
  };
  /** Measured server-side: false means the space could not take that angle. */
  cameraMoved: boolean | null;
  createdAt: string;
}

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        const os = db.createObjectStore(STORE, { keyPath: "id" });
        os.createIndex("locationId", "locationId", { unique: false });
        os.createIndex("createdAt", "createdAt", { unique: false });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

/** Every read and write is best-effort: private windows and storage-blocked
 *  browsers throw on open, and losing a saved render must never break a page. */
async function withStore<T>(
  mode: IDBTransactionMode,
  fn: (s: IDBObjectStore) => IDBRequest | null,
  fallback: T
): Promise<T> {
  try {
    const db = await open();
    return await new Promise<T>((resolve) => {
      const tx = db.transaction(STORE, mode);
      const req = fn(tx.objectStore(STORE));
      if (!req) {
        tx.oncomplete = () => resolve(fallback);
        return;
      }
      req.onsuccess = () => resolve((req.result as T) ?? fallback);
      req.onerror = () => resolve(fallback);
    });
  } catch {
    return fallback;
  }
}

const listeners = new Set<() => void>();
export function subscribeRenders(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
const emit = () => listeners.forEach((l) => l());

export async function saveRender(r: Omit<RenderRecord, "id" | "createdAt">): Promise<void> {
  const rec: RenderRecord = {
    ...r,
    id: `r_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`,
    createdAt: new Date().toISOString(),
  };
  await withStore("readwrite", (s) => s.put(rec), undefined);
  emit();
}

export async function allRenders(): Promise<RenderRecord[]> {
  const rows = await withStore<RenderRecord[]>("readonly", (s) => s.getAll(), []);
  return rows.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

export async function deleteRender(id: string): Promise<void> {
  await withStore("readwrite", (s) => s.delete(id), undefined);
  emit();
}

/**
 * Drop renders whose listing is no longer in the catalog.
 *
 * `stillListed` is asked once per distinct location id. A network failure must
 * not be read as "delisted" — the caller returns null for "could not tell" and
 * those records are kept.
 */
export async function pruneDelisted(
  stillListed: (locationId: string) => Promise<boolean | null>
): Promise<{ kept: number; removed: string[] }> {
  const rows = await allRenders();
  const verdicts = new Map<string, boolean | null>();
  for (const id of new Set(rows.map((r) => r.locationId))) {
    verdicts.set(id, await stillListed(id));
  }
  const removed: string[] = [];
  for (const r of rows) {
    if (verdicts.get(r.locationId) === false) {
      await withStore("readwrite", (s) => s.delete(r.id), undefined);
      removed.push(r.id);
    }
  }
  if (removed.length) emit();
  return { kept: rows.length - removed.length, removed };
}
