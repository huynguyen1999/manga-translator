import assert from "node:assert/strict";
import React from "react";
import type { Dispatch, SetStateAction } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { FinishedImage } from "@/types";
import { useMangaReaderActions, type ExitedReadPosition, type ReadingMangaState } from "./useMangaReaderActions";

const image: FinishedImage = {
  id: "page-1",
  groupId: "group-1",
  originalName: "page_1.png",
  pageOrder: 1,
  result: "/result/page-1/final.png",
  finishedAt: new Date(0),
  settings: {},
};
const state: {
  reading: ReadingMangaState | null;
  loading: string | null;
  error: string | null;
  exited: ExitedReadPosition | null;
  editing: FinishedImage | null;
} = { reading: null, loading: null, error: null, exited: null, editing: null };
const update = <T,>(action: SetStateAction<T>, current: T): T =>
  typeof action === "function" ? (action as (previous: T) => T)(current) : action;
const setReading: Dispatch<SetStateAction<ReadingMangaState | null>> = (action) => {
  state.reading = update(action, state.reading);
};
const setLoading: Dispatch<SetStateAction<string | null>> = (action) => {
  state.loading = update(action, state.loading);
};
const setError: Dispatch<SetStateAction<string | null>> = (action) => {
  state.error = update(action, state.error);
};
const setExited: Dispatch<SetStateAction<ExitedReadPosition | null>> = (action) => {
  state.exited = update(action, state.exited);
};
const setEditing: Dispatch<SetStateAction<FinishedImage | null>> = (action) => {
  state.editing = update(action, state.editing);
};
let openReaderArgs: unknown[] = [];
const overlayEvents: string[] = [];
let openedEditFolder: string | undefined;
let actions!: ReturnType<typeof useMangaReaderActions>;
function Harness() {
  actions = useMangaReaderActions({
    mangaGroups: [{ id: "group-1", title: "Chapter 1", isLoaded: true }],
    mangaImages: { "Chapter 1": [image] },
    loadMangaImagesIfNeeded: async () => [],
    readingManga: state.reading,
    setReadingManga: setReading,
    readerLoadingTitle: state.loading,
    setReaderLoadingTitle: setLoading,
    setReaderLoadError: setError,
    setLastExitedReadPosition: setExited,
    setEditingImage: setEditing,
    onOpenPageEdit: (folder) => { openedEditFolder = folder; },
    onCloseOverlay: () => { overlayEvents.push("closed"); },
    onOpenReader: (...args) => { openReaderArgs = args; },
  });
  return null;
}
renderToStaticMarkup(React.createElement(Harness));

const originalFetch = globalThis.fetch;
globalThis.fetch = (async () => ({
  ok: true,
  json: async () => ({ series: null }),
}) as Response) as typeof fetch;

try {
  await actions.handleReadManga("Chapter 1", [image], 0);
  assert.deepEqual(openReaderArgs, ["group-1", 0]);
  assert.deepEqual(state.reading, {
    groupId: "group-1",
    title: "Chapter 1",
    images: [image],
    initialPageIndex: 0,
    series: null,
  } satisfies ReadingMangaState);
  assert.equal(state.loading, null);
  assert.equal(state.error, null);

  renderToStaticMarkup(React.createElement(Harness));
  actions.handleCloseReader(0, "page-1");
  assert.equal(state.reading, null);
  assert.deepEqual(state.exited, {
    mangaTitle: "Chapter 1",
    mangaId: "group-1",
    pageIndex: 0,
    imageId: "page-1",
  });
  assert.deepEqual(overlayEvents, ["closed"]);

  state.reading = {
    groupId: "group-1",
    title: "Chapter 1",
    images: [image],
    series: null,
  };
  renderToStaticMarkup(React.createElement(Harness));
  actions.handleEditReaderImage({ ...image, folder: "page-folder" });
  assert.equal(state.reading, null);
  assert.equal(openedEditFolder, "page-folder");
  assert.equal(state.editing, null);
} finally {
  globalThis.fetch = originalFetch;
}

console.log("manga reader action contracts passed");
