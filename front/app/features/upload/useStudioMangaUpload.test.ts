import assert from "node:assert/strict";
import React from "react";
import type { Dispatch, SetStateAction } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { StudioFile, TranslationBatch, TranslationSettings } from "@/types";
import { importOriginalManga, useStudioMangaUpload } from "./useStudioMangaUpload";

type State = {
  pendingFiles: StudioFile[];
  modalOpen: boolean;
  error: string | null;
  warning: string | null;
  batches: TranslationBatch[];
};

const state: State = {
  pendingFiles: [],
  modalOpen: false,
  error: null,
  warning: "old warning",
  batches: [],
};
const update = <T,>(action: SetStateAction<T>, current: T): T =>
  typeof action === "function" ? (action as (previous: T) => T)(current) : action;
const setPendingFiles: Dispatch<SetStateAction<StudioFile[]>> = (action) => { state.pendingFiles = update(action, state.pendingFiles); };
const setModalOpen: Dispatch<SetStateAction<boolean>> = (action) => { state.modalOpen = update(action, state.modalOpen); };
const setError: Dispatch<SetStateAction<string | null>> = (action) => { state.error = update(action, state.error); };
const setWarning: Dispatch<SetStateAction<string | null>> = (action) => { state.warning = update(action, state.warning); };
const setBatches: Dispatch<SetStateAction<TranslationBatch[]>> = (action) => { state.batches = update(action, state.batches); };
const settings: TranslationSettings = {
  detectionResolution: "2048",
  textDetector: "default",
  renderTextDirection: "auto",
  translator: "deepseek",
  targetLanguage: "ENG",
  inpaintingSize: "2048",
  customUnclipRatio: 2.3,
  customBoxThreshold: 0.5,
  maskDilationOffset: 20,
  inpainter: "default",
};
const confirmingRef = { current: false };
const uploadRequestsRef = { current: new Map<string, Promise<void>>() };
let clearFormCalls = 0;
let actions!: ReturnType<typeof useStudioMangaUpload>;

function Harness() {
  actions = useStudioMangaUpload({
    pendingStudioMangaFiles: state.pendingFiles,
    setPendingStudioMangaFiles: setPendingFiles,
    setStudioMangaUploadError: setError,
    setStudioMangaUploadWarning: setWarning,
    setIsStudioMangaUploadModalOpen: setModalOpen,
    setTranslationBatches: setBatches,
    isConfirmingStudioUploadRef: confirmingRef,
    studioUploadRequestsRef: uploadRequestsRef,
    getCurrentSettings: () => settings,
    loadMangaSummaries: async () => {},
    clearForm: () => { clearFormCalls += 1; },
  });
  return null;
}

const render = () => renderToStaticMarkup(React.createElement(Harness));
const file = (name: string, type: string): StudioFile => ({
  id: name,
  file: new File([name], name, { type }),
  sourcePath: name,
  addedAt: 1,
  dropOrder: 1,
});

const page = file("page.png", "image/png");
let requestMethod = "";
let requestUrl = "";
let capturedForm: FormData | undefined;
class ImportRequest {
  status = 200;
  responseText = JSON.stringify({ group: { id: "group-1" }, items: [] });
  upload = { onprogress: null as ((event: ProgressEvent) => void) | null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;

  open(method: string, url: string) {
    requestMethod = method;
    requestUrl = url;
  }

  send(form: FormData) {
    capturedForm = form;
    this.upload.onprogress?.({ lengthComputable: true, loaded: 3, total: 4 } as ProgressEvent);
    this.onload?.();
  }
}

const originalXMLHttpRequest = globalThis.XMLHttpRequest;
globalThis.XMLHttpRequest = ImportRequest as unknown as typeof XMLHttpRequest;
try {
  const progress: number[] = [];
  const response = await importOriginalManga([page], "  One Piece  ", (value) => progress.push(value), "group-1", true);
  assert.equal(response.group && typeof response.group, "object");
  assert.equal(requestMethod, "POST");
  assert.equal(new URL(requestUrl, "http://studio.test").pathname, "/results/import");
  assert.deepEqual(progress, [75]);
  assert.ok(capturedForm);
  const form = capturedForm;
  assert.equal(form.get("mangaTitle"), "One Piece");
  assert.equal(form.get("groupId"), "group-1");
  assert.equal(form.get("mangaGroupId"), "group-1");
  assert.equal(form.get("isNewGroup"), "true");
  assert.deepEqual(JSON.parse(String(form.get("pageMetadata"))), [{
    originalName: "page.png",
    sourcePath: "page.png",
  }]);
  assert.ok(form.get("files") instanceof File);
} finally {
  globalThis.XMLHttpRequest = originalXMLHttpRequest;
}

render();
actions.handleStudioMangaUpload([file("notes.txt", "text/plain")]);
assert.equal(state.error, "Use PNG, JPEG, BMP, WEBP, TIFF, GIF, or AVIF images, or CBZ / ZIP archives.");
assert.equal(state.warning, "old warning");
assert.equal(state.modalOpen, false);

actions.handleStudioMangaUpload([page]);
assert.equal(state.error, null);
assert.equal(state.warning, null);
assert.equal(state.modalOpen, true);
assert.deepEqual(state.pendingFiles, [page]);

render();
await actions.handleStudioMangaUploadConfirm("  Ungrouped  ");
assert.equal(state.error, "Enter a manga title.");
assert.equal(state.modalOpen, true);
assert.deepEqual(state.pendingFiles, [page]);
assert.equal(clearFormCalls, 0);
assert.equal(confirmingRef.current, false);

const originalNavigator = Object.getOwnPropertyDescriptor(globalThis, "navigator");
let releaseUploadLock: (() => void) | undefined;
Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: {
    locks: {
      request: () => new Promise<void>((resolve) => { releaseUploadLock = resolve; }),
    },
  } as unknown as Navigator,
});
try {
  const confirmation = actions.handleStudioMangaUploadConfirm({
    title: "  One Piece  ",
    groupId: "group-1",
    isNewGroup: true,
  });
  for (let attempt = 0; attempt < 5 && !releaseUploadLock; attempt += 1) {
    await Promise.resolve();
  }

  assert.ok(releaseUploadLock, "upload waits for its cross-tab lock before resuming");
  assert.equal(state.modalOpen, false);
  assert.deepEqual(state.pendingFiles, []);
  assert.equal(state.error, null);
  assert.equal(clearFormCalls, 1);
  assert.equal(confirmingRef.current, false);
  assert.equal(state.batches.length, 1);
  assert.equal(state.batches[0].kind, "manga-upload");
  assert.equal(state.batches[0].mangaTitle, "One Piece");
  assert.equal(state.batches[0].mangaGroupId, "group-1");
  assert.equal(state.batches[0].isNewGroup, true);
  assert.equal(state.batches[0].settings.translator, "none");
  assert.equal(state.batches[0].settings.inpainter, "original");
  assert.equal(state.batches[0].items[0].file, page.file);
  assert.equal(state.batches[0].items[0].sourcePath, page.sourcePath);

  releaseUploadLock();
  await confirmation;
} finally {
  if (originalNavigator) {
    Object.defineProperty(globalThis, "navigator", originalNavigator);
  } else {
    Reflect.deleteProperty(globalThis, "navigator");
  }
}

actions.handleStudioMangaUpload([page]);
assert.equal(state.modalOpen, true);
assert.deepEqual(state.pendingFiles, [page]);
actions.closeStudioMangaUploadModal();
assert.equal(state.modalOpen, false);
assert.deepEqual(state.pendingFiles, []);
assert.equal(state.error, null);
assert.equal(state.warning, null);
assert.equal(state.batches.length, 1);

console.log("studio manga upload contracts passed");
