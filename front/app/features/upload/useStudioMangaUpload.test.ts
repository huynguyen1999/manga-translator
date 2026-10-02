import assert from "node:assert/strict";
import React from "react";
import type { Dispatch, SetStateAction } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { StudioFile, TranslationBatch, TranslationSettings } from "@/types";
import type { MangaImportJob } from "./mangaImportJobs";
import {
  dismissMangaImportJobRequest,
  fetchMangaImportJobs,
  importOriginalManga,
  retryMangaImportJobRequest,
  useStudioMangaUpload,
  watchMangaImportJobs,
} from "./useStudioMangaUpload";

type State = {
  pendingFiles: StudioFile[];
  modalOpen: boolean;
  error: string | null;
  warning: string | null;
  batches: TranslationBatch[];
  importJobs: MangaImportJob[];
};

const state: State = {
  pendingFiles: [],
  modalOpen: false,
  error: null,
  warning: "old warning",
  batches: [],
  importJobs: [],
};
const update = <T,>(action: SetStateAction<T>, current: T): T =>
  typeof action === "function" ? (action as (previous: T) => T)(current) : action;
const setPendingFiles: Dispatch<SetStateAction<StudioFile[]>> = (action) => { state.pendingFiles = update(action, state.pendingFiles); };
const setModalOpen: Dispatch<SetStateAction<boolean>> = (action) => { state.modalOpen = update(action, state.modalOpen); };
const setError: Dispatch<SetStateAction<string | null>> = (action) => { state.error = update(action, state.error); };
const setWarning: Dispatch<SetStateAction<string | null>> = (action) => { state.warning = update(action, state.warning); };
const setBatches: Dispatch<SetStateAction<TranslationBatch[]>> = (action) => { state.batches = update(action, state.batches); };
const setImportJobs: Dispatch<SetStateAction<MangaImportJob[]>> = (action) => { state.importJobs = update(action, state.importJobs); };
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
    setMangaImportJobs: setImportJobs,
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
const job = (status: MangaImportJob["status"], id = "job-1"): MangaImportJob => ({
  id,
  title: "One Piece",
  status,
  createdAt: "2026-10-02T00:00:00Z",
});
const response = (payload: unknown, status = 200): Response => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => payload,
} as Response);

let requestMethod = "";
let requestUrl = "";
let requestCount = 0;
let failNextUpload = false;
let capturedForm: FormData | undefined;
class ImportRequest {
  status = 200;
  responseText = JSON.stringify({ job: { id: "job-1", title: "One Piece", status: "queued", createdAt: 1790899200000 } });
  upload = { onprogress: null as ((event: ProgressEvent) => void) | null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;

  open(method: string, url: string) {
    requestMethod = method;
    requestUrl = url;
  }

  send(form: FormData) {
    requestCount += 1;
    capturedForm = form;
    if (failNextUpload) {
      failNextUpload = false;
      this.onerror?.();
      return;
    }
    this.upload.onprogress?.({ lengthComputable: true, loaded: 3, total: 4 } as ProgressEvent);
    this.onload?.();
  }
}

const originalXMLHttpRequest = globalThis.XMLHttpRequest;
const originalFetch = globalThis.fetch;
const fetchCalls: Array<{ url: string; method: string }> = [];
globalThis.XMLHttpRequest = ImportRequest as unknown as typeof XMLHttpRequest;
globalThis.fetch = async (input, init) => {
  const url = String(input);
  const method = init?.method || "GET";
  fetchCalls.push({ url, method });
  return response({ jobs: [job("queued")] });
};
try {
  const progress: number[] = [];
  const accepted = await importOriginalManga([page], "  One Piece  ", (value) => progress.push(value), "group-1", true, "draft-1");
  assert.equal(accepted.status, "queued");
  assert.equal(accepted.createdAt, new Date(1790899200000).toISOString());
  assert.equal(requestMethod, "POST");
  assert.equal(new URL(requestUrl, "http://studio.test").pathname, "/api/manga-import-jobs");
  assert.deepEqual(progress, [75]);
  assert.ok(capturedForm);
  const form = capturedForm;
  assert.equal(form.get("mangaTitle"), "One Piece");
  assert.equal(form.get("groupId"), "group-1");
  assert.equal(form.get("mangaGroupId"), "group-1");
  assert.equal(form.get("isNewGroup"), "true");
  assert.equal(form.get("clientUploadId"), "draft-1");
  assert.deepEqual(JSON.parse(String(form.get("pageMetadata"))), [{
    originalName: "page.png",
    sourcePath: "page.png",
  }]);
  assert.ok(form.get("files") instanceof File);

  for (const status of ["queued", "processing", "completed", "failed"] as const) {
    const hydrated = await fetchMangaImportJobs(async () => response({ jobs: [job(status)] }));
    assert.equal(hydrated[0].status, status);
  }
  let visibilityState = "hidden";
  const browserListeners = new Map<string, () => void>();
  const documentListeners = new Map<string, () => void>();
  let intervalCallback: (() => void) | undefined;
  let intervalCleared = false;
  const browserWindow = {
    addEventListener: (name: string, callback: () => void) => browserListeners.set(name, callback),
    removeEventListener: (name: string) => browserListeners.delete(name),
    setInterval: (callback: () => void, duration: number) => {
      assert.equal(duration, 2000);
      intervalCallback = callback;
      return 7;
    },
    clearInterval: (timer: number) => { intervalCleared = timer === 7; },
  } as unknown as Window;
  const browserDocument = {
    get visibilityState() { return visibilityState; },
    addEventListener: (name: string, callback: () => void) => documentListeners.set(name, callback),
    removeEventListener: (name: string) => documentListeners.delete(name),
  } as unknown as Document;
  let pollCount = 0;
  const stopWatching = watchMangaImportJobs(() => { pollCount += 1; }, browserWindow, browserDocument);
  intervalCallback?.();
  browserListeners.get("focus")?.();
  browserListeners.get("online")?.();
  documentListeners.get("visibilitychange")?.();
  assert.equal(pollCount, 4, "polling and reconnect events rehydrate jobs; hidden tabs skip visibility refresh");
  visibilityState = "visible";
  documentListeners.get("visibilitychange")?.();
  assert.equal(pollCount, 5);
  stopWatching();
  assert.equal(intervalCleared, true);
  assert.equal(browserListeners.size + documentListeners.size, 0);
  const backendJob = (await fetchMangaImportJobs(async () => response({ jobs: [{
    id: "job-backend",
    title: "One Piece",
    status: "processing",
    createdAt: 1790899200000,
    updatedAt: 1790899300000,
    group: { id: "group-1" },
    fileCount: 12,
    totalPages: 10,
    processedPages: 3,
    progress: 30,
  }] })))[0];
  assert.equal(backendJob.createdAt, new Date(1790899200000).toISOString());
  assert.equal(backendJob.groupId, "group-1");
  assert.equal(backendJob.fileCount, 12);
  assert.equal(backendJob.totalPages, 10);
  assert.equal(backendJob.processedPages, 3);
  assert.equal(backendJob.progress, 30);

  const retryCalls: Array<{ url: string; method?: string }> = [];
  await retryMangaImportJobRequest("failed/1", async (input, init) => {
    retryCalls.push({ url: String(input), method: init?.method });
    return response({ job: job("queued", "failed/1") });
  });
  await dismissMangaImportJobRequest("failed/1", async (input, init) => {
    retryCalls.push({ url: String(input), method: init?.method });
    return response({});
  });
  assert.deepEqual(retryCalls.map(({ url, method }) => ({ pathname: new URL(url, "http://studio.test").pathname, method })), [
    { pathname: "/api/manga-import-jobs/failed%2F1/retry", method: "POST" },
    { pathname: "/api/manga-import-jobs/failed%2F1", method: "DELETE" },
  ]);
  assert.equal(requestCount, 1, "status hydration and retained-upload retry do not retransmit files");
  assert.ok(fetchCalls.every(({ url }) => new URL(url, "http://studio.test").pathname === "/api/manga-import-jobs"));

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
        request: (_name: string, run: () => Promise<void>) => new Promise<void>((resolve, reject) => {
          releaseUploadLock = () => { void run().then(resolve, reject); };
        }),
      },
    } as unknown as Navigator,
  });
  try {
    const confirmation = actions.handleStudioMangaUploadConfirm({
      title: "  One Piece  ",
      groupId: "group-1",
      isNewGroup: true,
    });
    for (let attempt = 0; attempt < 5 && !releaseUploadLock; attempt += 1) await Promise.resolve();

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
    await Promise.resolve();
    assert.equal(state.batches.length, 0, "transfer presentation is removed after durable acceptance");
    assert.equal(state.importJobs[0].status, "queued");
    assert.equal(requestCount, 2);
    assert.ok(fetchCalls.every(({ url }) => !/results\/import|\/batches/.test(url)));
  } finally {
    if (originalNavigator) Object.defineProperty(globalThis, "navigator", originalNavigator);
    else Reflect.deleteProperty(globalThis, "navigator");
  }

  actions.handleStudioMangaUpload([page]);
  failNextUpload = true;
  await actions.handleStudioMangaUploadConfirm("One Piece");
  assert.equal(state.batches.length, 1, "an unaccepted upload failure retains the local retry draft");
  assert.equal(state.batches[0].status, "error");
  assert.equal(state.batches[0].items[0].file, page.file);
  assert.equal(new URL(requestUrl, "http://studio.test").pathname, "/api/manga-import-jobs");
  assert.equal(requestCount, 3);
  assert.ok(fetchCalls.every(({ url }) => !/results\/import|\/batches/.test(url)));
  state.batches = [];

  actions.handleStudioMangaUpload([page]);
  assert.equal(state.modalOpen, true);
  assert.deepEqual(state.pendingFiles, [page]);
  actions.closeStudioMangaUploadModal();
  assert.equal(state.modalOpen, false);
  assert.deepEqual(state.pendingFiles, []);
  assert.equal(state.error, null);
  assert.equal(state.warning, null);
  assert.equal(state.batches.length, 0);
} finally {
  globalThis.XMLHttpRequest = originalXMLHttpRequest;
  globalThis.fetch = originalFetch;
}

console.log("studio manga upload contracts passed");
