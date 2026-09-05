"use client";

/**
 * React bindings for the local user store.
 *
 * Kept apart from userStore.ts so the store stays a plain module a future API
 * client can replace wholesale. useSyncExternalStore is what makes a heart in
 * the grid, the count in the header and the profile page agree without a global
 * state library: they all read the same snapshot and re-render on the same emit.
 */

import { useCallback, useEffect, useSyncExternalStore } from "react";
import {
  getServerSnapshot,
  getSnapshot,
  hydrate,
  subscribe,
  toggleSaved,
  UserState,
} from "@/lib/userStore";
import { KoreanLocation } from "@/types";

export function useUserState(): UserState {
  // The server snapshot is empty by design; hydrating in an effect means the
  // first client paint matches the server and only then fills in.
  useEffect(() => hydrate(), []);
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

export function useSavedIds(): Set<string> {
  const { saved } = useUserState();
  return new Set(saved.map((s) => s.id));
}

/** `[isSaved, toggle]` for one listing. */
export function useSavedToggle(loc: KoreanLocation | null): [boolean, () => void] {
  const { saved } = useUserState();
  const on = !!loc && saved.some((s) => s.id === loc.id);
  const toggle = useCallback(() => {
    if (loc) toggleSaved(loc);
  }, [loc]);
  return [on, toggle];
}
