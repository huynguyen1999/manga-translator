import assert from "node:assert/strict";
import React from "react";
import type { Dispatch, SetStateAction } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { MangaGroupSummary } from "@/types";
import type { ParsedRoute } from "@/utils/routeState";
import { useMangaSummaries } from "./useMangaSummaries";

const state = {
  groups: [] as MangaGroupSummary[],
  totalGroups: 0,
  totalImages: 0,
  loading: true,
};
const update = <T,>(action: SetStateAction<T>, current: T): T =>
  typeof action === "function" ? (action as (previous: T) => T)(current) : action;
const loadingChanges: boolean[] = [];
const setGroups: Dispatch<SetStateAction<MangaGroupSummary[]>> = (action) => { state.groups = update(action, state.groups); };
const setTotalGroups: Dispatch<SetStateAction<number>> = (action) => { state.totalGroups = update(action, state.totalGroups); };
const setTotalImages: Dispatch<SetStateAction<number>> = (action) => { state.totalImages = update(action, state.totalImages); };
const setLoading: Dispatch<SetStateAction<boolean>> = (action) => {
  state.loading = update(action, state.loading);
  loadingChanges.push(state.loading);
};
const route: ParsedRoute = {
  view: "gallery",
  overlay: "none",
  galleryPage: 2,
  galleryPageSize: 50,
  gallerySearch: "One Piece",
  gallerySort: "alpha-desc",
  galleryStatus: "translated",
  reviewOnly: true,
  rawPath: "/gallery",
};
const cache = new Map<string, { groups: MangaGroupSummary[]; totalGroups: number; totalImages: number }>();
const requestIdRef = { current: 0 };
let load!: ReturnType<typeof useMangaSummaries>;

function Harness() {
  load = useMangaSummaries({
    parsedRoute: route,
    setMangaSummaries: setGroups,
    setTotalMangaCount: setTotalGroups,
    setTotalGalleryCount: setTotalImages,
    setIsGalleryLoading: setLoading,
    galleryLoadRequestRef: requestIdRef,
    galleryPageCacheRef: { current: cache },
  });
  return null;
}
renderToStaticMarkup(React.createElement(Harness));

const responseGroups = [{ title: "One Piece", count: 3 }] as MangaGroupSummary[];
let requestedUrl = "";
const originalFetch = globalThis.fetch;
globalThis.fetch = (async (input) => {
  requestedUrl = String(input);
  return {
    ok: true,
    json: async () => ({ groups: responseGroups, totalGroups: 4, totalImages: 12 }),
  } as Response;
}) as typeof fetch;

try {
  await load();
  const request = new URL(requestedUrl, "http://studio.test");
  assert.equal(request.pathname, "/results/groups");
  assert.deepEqual(Object.fromEntries(request.searchParams), {
    limit: "50",
    offset: "50",
    search: "One Piece",
    sort: "alpha-desc",
    status: "translated",
  });
  assert.deepEqual(state.groups, responseGroups);
  assert.equal(state.totalGroups, 4);
  assert.equal(state.totalImages, 12);
  assert.deepEqual(loadingChanges, [true, false]);
  assert.deepEqual(cache.get("2:50:One Piece:alpha-desc:translated"), {
    groups: responseGroups,
    totalGroups: 4,
    totalImages: 12,
  });
} finally {
  globalThis.fetch = originalFetch;
}

console.log("manga summaries loading contracts passed");
