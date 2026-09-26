import assert from "node:assert/strict";
import type { Dispatch, SetStateAction } from "react";
import type { FinishedImage, MangaGroupSummary } from "@/types";
import { createGalleryMutationActions } from "./galleryMutations";

type State = {
  images: FinishedImage[];
  groups: MangaGroupSummary[];
  totalManga: number;
  totalGallery: number;
  isLoading: boolean;
  revision: number;
};

const image = (id: string, folder: string, mangaTitle: string): FinishedImage => ({
  id,
  originalName: `${id}.png`,
  result: `/result/${folder}.jpg`,
  folder,
  mangaTitle,
  pageOrder: 1,
  finishedAt: new Date(0),
  settings: {},
});
const state: State = {
  images: [image("page-1", "folder-1", "Title"), image("page-2", "folder-2", "Title")],
  groups: [{ title: "Title", count: 2 }],
  totalManga: 1,
  totalGallery: 2,
  isLoading: true,
  revision: 0,
};
const update = <T,>(action: SetStateAction<T>, current: T): T =>
  typeof action === "function" ? (action as (previous: T) => T)(current) : action;
const setImages: Dispatch<SetStateAction<FinishedImage[]>> = (action) => { state.images = update(action, state.images); };
const setGroups: Dispatch<SetStateAction<MangaGroupSummary[]>> = (action) => { state.groups = update(action, state.groups); };
const setTotalManga: Dispatch<SetStateAction<number>> = (action) => { state.totalManga = update(action, state.totalManga); };
const setTotalGallery: Dispatch<SetStateAction<number>> = (action) => { state.totalGallery = update(action, state.totalGallery); };
const setIsLoading: Dispatch<SetStateAction<boolean>> = (action) => { state.isLoading = update(action, state.isLoading); };
const setRevision: Dispatch<SetStateAction<number>> = (action) => { state.revision = update(action, state.revision); };

const cache = new Map<string, { groups: MangaGroupSummary[]; totalGroups: number; totalImages: number }>([
  ["cached", { groups: state.groups, totalGroups: 1, totalImages: 2 }],
]);
let summaryLoads = 0;
const actions = createGalleryMutationActions({
  setFinishedImages: setImages,
  setMangaSummaries: setGroups,
  setTotalMangaCount: setTotalManga,
  setTotalGalleryCount: setTotalGallery,
  setIsGalleryLoading: setIsLoading,
  setGalleryRevision: setRevision,
  galleryPageCacheRef: { current: cache },
  loadMangaSummaries: async () => { summaryLoads += 1; },
});

const updatedPage = image("replacement", "folder-2", "Title");
actions.updateFinishedImage(updatedPage);
assert.equal(state.images[1], updatedPage, "matching folder replaces the existing result");

const requests: Array<{ url: string; method?: string; body?: string }> = [];
const originalFetch = globalThis.fetch;
globalThis.fetch = (async (input, init) => {
  requests.push({ url: String(input), method: init?.method, body: init?.body as string | undefined });
  return {
    ok: true,
    json: async () => ({ pages: [{ id: "replacement", pageOrder: 9 }] }),
  } as Response;
}) as typeof fetch;

try {
  await actions.deleteFinishedImages([state.images[0]]);
  assert.equal(new URL(requests[0].url, "http://studio.test").pathname, "/results/batch-delete");
  assert.equal(requests[0].method, "POST");
  assert.deepEqual(JSON.parse(requests[0].body || "{}"), { folders: ["folder-1"] });
  assert.deepEqual(state.images.map(({ id }) => id), ["replacement"]);
  assert.equal(state.totalGallery, 1);
  assert.deepEqual(state.groups.map(({ count }) => count), [1]);
  assert.equal(cache.size, 0);

  await actions.handleUpdateMangaTitle(
    ["replacement", ""],
    " New Title ",
    "Title",
    "group-1",
    ["folder-2", "invalid/path", ""],
  );
  const titleRequest = requests[1];
  assert.equal(new URL(titleRequest.url, "http://studio.test").pathname, "/results/update-meta");
  assert.deepEqual(JSON.parse(titleRequest.body || "{}"), {
    folders: ["folder-2"],
    pageIds: ["replacement"],
    oldMangaTitle: "Title",
    mangaTitle: "New Title",
    groupId: "group-1",
  });
  assert.equal(state.images[0].mangaTitle, "New Title");

  await actions.reorderMangaPages("series/group", ["replacement"]);
  const orderRequest = requests[2];
  assert.equal(new URL(orderRequest.url, "http://studio.test").pathname, "/manga/series%2Fgroup/pages/order");
  assert.equal(orderRequest.method, "PUT");
  assert.deepEqual(JSON.parse(orderRequest.body || "{}"), { pageIds: ["replacement"] });
  assert.equal(state.images[0].pageOrder, 9);
  assert.equal(summaryLoads, 2);
} finally {
  globalThis.fetch = originalFetch;
}

console.log("gallery mutation contracts passed");
