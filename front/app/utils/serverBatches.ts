import type {
  PipelineRerunMode,
  QueuedImage,
  TranslationBatch,
  TranslationBatchKind,
  TranslationSettings,
  TranslatorKey,
} from "@/types";
import { apiUrl } from "./api";

export interface ServerBatchItem {
  id: string;
  name: string;
  mangaGroupId?: string | null;
  pageId?: string | null;
  pageOrder?: number | null;
  sourcePath?: string | null;
  mangaTitle?: string;
  status: "queued" | "processing" | "completed" | "error";
  stage?: string | null;
  stageStartedAt?: number | string | null;
  error?: string | null;
  addedAt?: number | string;
  inputUrl?: string | null;
  resultFolder?: string | null;
  resultUrl?: string | null;
  batchPreviewUrl?: string | null;
  coverUrl?: string | null;
  detailPreviewUrl?: string | null;
  readerUrl?: string | null;
  fullUrl?: string | null;
  requestId?: string;
  model?: { offline_model?: string; gemini_model?: string };
  excludeColor?: boolean;
  needsReview?: boolean;
  retryFromStage?: string | null;
}

export interface ServerBatchSummary {
  id: string;
  kind?: TranslationBatchKind | null;
  title: string;
  mangaTitle: string;
  mangaGroupId?: string | null;
  isNewGroup?: boolean;
  addedAt: number | string;
  updatedAt?: number | string;
  settings: TranslationSettings;
  status: "waiting" | "processing" | "paused" | "completed" | "error" | "stopping";
  dismissed: boolean;
  priority?: boolean;
  totalItems: number;
  completedCount: number;
  queuedCount: number;
  processingCount: number;
  failedCount: number;
  needsReviewCount: number;
  currentStage?: string;
  currentStagePassedCount?: number;
}

export interface ServerBatch extends ServerBatchSummary {
  items: ServerBatchItem[];
}

export interface PipelineRerunRequest {
  pageIds?: string[];
  groupId?: string;
  mode: PipelineRerunMode;
  settingsOverrides?: Partial<TranslationSettings>;
}

export const resultFor = (item: QueuedImage) =>
  item.status !== "finished"
    ? null
    : item.result instanceof Blob && item.result.size < 1000 && item.folder
    ? apiUrl(`/result/${item.folder}/final.png`)
    : typeof item.result === "string" ? apiUrl(item.result) : (item.result || (item.folder ? apiUrl(`/result/${item.folder}/final.png`) : null));

export const jobThumbnailCandidates = (item: QueuedImage): string[] => {
  const result = resultFor(item);
  const candidates = [
    item.batchPreviewUrl ? apiUrl(item.batchPreviewUrl) : null,
    item.folder ? apiUrl(`/result/${item.folder}/batch.webp`) : null,
    typeof result === "string" ? result : null,
  ];
  return [...new Set(candidates.filter((url): url is string => Boolean(url)))];
};

const getBatchTimestamp = (batch: TranslationBatch): number => {
  const t = batch.addedAt instanceof Date ? batch.addedAt.getTime() : typeof batch.addedAt === "number" ? batch.addedAt : 0;
  if (!Number.isNaN(t) && t > 0) return t;
  const u = batch.updatedAt instanceof Date ? batch.updatedAt.getTime() : typeof batch.updatedAt === "number" ? batch.updatedAt : 0;
  if (!Number.isNaN(u) && u > 0) return u;
  return 0;
};

const getStatusRank = (status: TranslationBatch["status"]): number => {
  switch (status) {
    case "processing":
    case "uploading":
    case "stopping":
      return 0; // Active working batches first
    case "waiting":
    case "paused":
      return 1; // Queued/paused next
    case "error":
      return 2; // Errors
    case "completed":
    default:
      return 3; // Completed summaries
  }
};

export const sortBatchesLatestFirst = (batches: TranslationBatch[]): TranslationBatch[] =>
  batches
    .filter((batch) => !batch.dismissed)
    .slice()
    .sort((a, b) => {
      // 1. Priority batches on top
      const prioDiff = (b.priority ? 1 : 0) - (a.priority ? 1 : 0);
      if (prioDiff !== 0) return prioDiff;

      // 2. Active batches (uploading/processing/waiting) before completed
      const rankDiff = getStatusRank(a.status) - getStatusRank(b.status);
      if (rankDiff !== 0) return rankDiff;

      // 3. Latest submission on top
      const timeDiff = getBatchTimestamp(b) - getBatchTimestamp(a);
      if (timeDiff !== 0) return timeDiff;

      return b.id.localeCompare(a.id);
    });

export const canChangeBatchTranslator = (batch: TranslationBatch): boolean =>
  ["waiting", "processing", "paused"].includes(batch.status) ||
  (batch.status === "error" && (
    (batch.failedCount ?? 0) > 0 || batch.items.some((item) => item.status === "error")
  ));

export const getBatchKind = (batch: { id: string; kind?: TranslationBatchKind | null }): TranslationBatchKind =>
  batch.kind ?? (batch.id.startsWith("upload-") || batch.id.startsWith("original-") ? "manga-upload" : batch.id.startsWith("rerun-") || batch.id.startsWith("rerender-") ? "pipeline-rerun" : "translation");

export const formatStage = (step?: string): string => {
  if (!step) return "Starting";
  const professionalChunk = /^(drafting|editing):(\d+)\/(\d+):(\d+)\/(\d+)$/.exec(step);
  if (professionalChunk) {
    const [, phase, storyIndex, storyCount, chunkIndex, chunkCount] = professionalChunk;
    return `${phase === "drafting" ? "First draft" : "Editor pass"} · Story ${storyIndex}/${storyCount} · Chunk ${chunkIndex}/${chunkCount}`;
  }
  switch (step) {
    case "awaiting_translation":
      return "Awaiting translation";
    case "colorizing":
    case "colorization":
      return "Colorizing";
    case "upscaling":
    case "upscale":
      return "Upscaling";
    case "detection":
      return "Detecting text";
    case "bubble_detection":
      return "Detecting speech bubbles";
    case "ocr":
      return "Recognizing text";
    case "textline_merge":
    case "text_grouping":
      return "Merging text lines";
    case "mask-generation":
    case "mask_generation":
      return "Generating mask";
    case "layout":
      return "Laying out text";
    case "inpainting":
      return "Inpainting";
    case "translating":
    case "translation":
      return "Translating with AI";
    case "translation_remap":
      return "Remapping translations";
    case "analyzing-story":
      return "Analyzing story";
    case "after-translating":
      return "Processing translation";
    case "rendering":
      return "Rendering text";
    case "saving":
      return "Saving";
    case "downscaling":
      return "Downscaling";
    case "reserved":
      return "Queued in batch";
    case "finished":
      return "Finished";
    case "input":
      return "Starting";
    case "finalize":
      return "Finalizing";
    default:
      return step.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }
};

export const formatStageElapsed = (startedAt: Date, now = Date.now()): string => {
  const seconds = Math.max(0, Math.floor((now - startedAt.getTime()) / 1000));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`;
};

const asDate = (value: number | string | undefined) =>
  new Date(value === undefined ? Date.now() : typeof value === "number" ? value : value);

export const toTranslationBatch = (batch: ServerBatch | ServerBatchSummary): TranslationBatch => ({
  id: batch.id,
  kind: getBatchKind(batch),
  addedAt: asDate(batch.addedAt),
  updatedAt: asDate(batch.updatedAt),
  mangaTitle: batch.mangaTitle || batch.title || "Ungrouped",
  mangaGroupId: batch.mangaGroupId || ("items" in batch && batch.items[0]?.mangaGroupId) || null,
  isNewGroup: batch.isNewGroup,
  settings: batch.settings,
  totalItems: batch.totalItems,
  completedCount: batch.completedCount,
  queuedCount: batch.queuedCount,
  processingCount: batch.processingCount,
  failedCount: batch.failedCount,
  needsReviewCount: batch.needsReviewCount,
  currentStage: batch.currentStage,
  currentStagePassedCount: batch.currentStagePassedCount,
  status: batch.status,
  priority: batch.priority,
  dismissed: batch.dismissed,
  detailsLoaded: "items" in batch,
  items: ("items" in batch ? batch.items : []).map((item): QueuedImage => ({
    id: item.id,
    mangaGroupId: item.mangaGroupId,
    pageId: item.pageId,
    pageOrder: item.pageOrder,
    sourcePath: item.sourcePath,
    file: new File([], item.name),
    addedAt: asDate(item.addedAt ?? batch.addedAt),
    status: item.status === "completed" ? "finished" : item.status,
    mangaTitle: item.mangaTitle || batch.mangaTitle || batch.title,
    step: item.stage || undefined,
    stepStartedAt: item.stageStartedAt == null ? undefined : asDate(item.stageStartedAt),
    error: item.error || undefined,
    folder: item.resultFolder || undefined,
    inputUrl: item.inputUrl,
    resultUrl: item.resultUrl,
    batchPreviewUrl: item.batchPreviewUrl,
    coverUrl: item.coverUrl,
    detailPreviewUrl: item.detailPreviewUrl,
    readerUrl: item.readerUrl,
    fullUrl: item.fullUrl,
    result: item.resultUrl || undefined,
    offlineModel: item.model?.offline_model,
    geminiModel: item.model?.gemini_model,
    excludeColor: item.excludeColor,
    needsReview: item.needsReview,
  })),
});

const sameValue = (left: unknown, right: unknown): boolean => {
  if (left instanceof Date && right instanceof Date) return left.getTime() === right.getTime();
  if (left && right && typeof left === "object" && typeof right === "object") {
    if (Array.isArray(left) || Array.isArray(right)) {
      return Array.isArray(left) && Array.isArray(right)
        && left.length === right.length
        && left.every((value, index) => sameValue(value, right[index]));
    }
    const leftRecord = left as Record<string, unknown>;
    const rightRecord = right as Record<string, unknown>;
    const keys = Object.keys(leftRecord);
    return keys.length === Object.keys(rightRecord).length
      && keys.every((key) => sameValue(leftRecord[key], rightRecord[key]));
  }
  return Object.is(left, right);
};

const sameQueuedImage = (left: QueuedImage, right: QueuedImage): boolean =>
  left.file.name === right.file.name
  && Object.keys(left).filter((key) => key !== "file").length === Object.keys(right).filter((key) => key !== "file").length
  && Object.keys(left).every((key) => key === "file" || sameValue(
    (left as unknown as Record<string, unknown>)[key],
    (right as unknown as Record<string, unknown>)[key],
  ));

const sameBatch = (left: TranslationBatch, right: TranslationBatch): boolean =>
  Object.keys(left).filter((key) => key !== "items").length === Object.keys(right).filter((key) => key !== "items").length
  && Object.keys(left).every((key) => key === "items" || sameValue(
    (left as unknown as Record<string, unknown>)[key],
    (right as unknown as Record<string, unknown>)[key],
  )) && left.items.length === right.items.length
  && left.items.every((item, index) => item === right.items[index]);

export const mergeServerBatchDetails = (
  current: TranslationBatch,
  remote: ServerBatch,
): TranslationBatch => {
  const translated = toTranslationBatch(remote);
  const previousItems = new Map(current.items.map((item) => [item.id, item]));
  const items = translated.items.map((item) => {
    const previous = previousItems.get(item.id);
    if (!previous) return item;
    return sameQueuedImage(previous, item) ? previous : { ...item, file: previous.file };
  });
  const merged = { ...translated, items, detailsLoaded: true };
  return sameBatch(current, merged) ? current : merged;
};

export const mergeServerBatches = (
  current: TranslationBatch[],
  remote: ServerBatchSummary[],
  locallyDismissed: ReadonlySet<string>,
  optimisticTranslators: ReadonlyMap<string, TranslatorKey> = new Map(),
): TranslationBatch[] => {
  const remoteIds = new Set(remote.map((batch) => batch.id));
  const currentById = new Map(current.map((batch) => [batch.id, batch]));
  const localUploads = current.filter(
    (batch) => batch.status === "uploading" && !remoteIds.has(batch.id),
  );
  return [
    ...localUploads,
    ...remote.map((batch) => {
      const translated = toTranslationBatch(batch);
      const existing = currentById.get(batch.id);
      const merged = existing?.detailsLoaded
        ? { ...translated, items: existing.items, detailsLoaded: true }
        : translated;
      const optimisticTranslator = optimisticTranslators.get(batch.id);
      const withOptimisticTranslator = optimisticTranslator && merged.settings.translator !== optimisticTranslator
        ? { ...merged, settings: { ...merged.settings, translator: optimisticTranslator } }
        : merged;
      const withDismissal = locallyDismissed.has(batch.id) && !withOptimisticTranslator.dismissed
        ? { ...withOptimisticTranslator, dismissed: true }
        : withOptimisticTranslator;
      return existing && sameBatch(existing, withDismissal) ? existing : withDismissal;
    }),
  ];
};

export const fetchServerBatches = async (): Promise<ServerBatchSummary[]> => {
  const response = await fetch(apiUrl("/api/batches"));
  if (!response.ok) throw new Error(`Could not load batches (${response.status})`);
  return (await response.json()) as ServerBatchSummary[];
};

export const fetchServerBatch = async (batchId: string): Promise<ServerBatch> => {
  const response = await fetch(apiUrl(`/api/batches/${encodeURIComponent(batchId)}`));
  if (!response.ok) throw new Error(`Could not load batch (${response.status})`);
  return (await response.json()) as ServerBatch;
};

export const rerunPipeline = async ({
  pageIds,
  groupId,
  mode,
  settingsOverrides,
}: PipelineRerunRequest): Promise<ServerBatch> => {
  const response = await fetch(apiUrl("/api/results/rerun"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pageIds, groupId, mode, settingsOverrides }),
  });
  if (!response.ok) {
    const detail = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(detail?.detail || `Could not queue pipeline rerun (${response.status})`);
  }
  return (await response.json()) as ServerBatch;
};

export const rerenderPages = async (pageIds: string[]): Promise<ServerBatch> => {
  return rerunPipeline({ pageIds, mode: "typesetting" });
};


export const subscribeServerBatches = (
  onBatches: (batches: ServerBatchSummary[]) => void,
  onError?: () => void,
  onBatchDetails?: (batch: ServerBatch) => void,
): EventSource => {
  const source = new EventSource(apiUrl("/api/batches/events"));
  source.onmessage = (event) => onBatches(JSON.parse(event.data) as ServerBatchSummary[]);
  source.addEventListener("batch_details", (event) => {
    onBatchDetails?.(JSON.parse((event as MessageEvent<string>).data) as ServerBatch);
  });
  source.onerror = () => onError?.();
  return source;
};

export const submitServerBatch = async (
  batch: TranslationBatch,
  onProgress?: (progress: number) => void,
): Promise<ServerBatch> => {
  const cleanBatchTitle = (batch.mangaTitle || "Ungrouped").trim() || "Ungrouped";
  const form = new FormData();
  form.append(
    "manifest",
    new Blob([
      JSON.stringify({
        id: batch.id,
        kind: batch.kind,
        title: cleanBatchTitle,
        mangaTitle: cleanBatchTitle,
        mangaGroupId: batch.mangaGroupId || null,
        isNewGroup: Boolean(batch.isNewGroup),
        addedAt: batch.addedAt.getTime(),
        settings: batch.settings,
        status: batch.status === "completed" ? "completed" : "waiting",
        dismissed: Boolean(batch.dismissed),
        totalItems: batch.totalItems,
        completedCount: batch.completedCount,
        priority: Boolean(batch.priority),
        items: batch.items.map((item) => ({
          id: item.id,
          name: item.file.name,
          mangaGroupId: item.mangaGroupId || batch.mangaGroupId || null,
          pageId: item.pageId,
          pageOrder: item.pageOrder,
          sourcePath: item.sourcePath,
          mangaTitle: (item.mangaTitle || cleanBatchTitle).trim() || cleanBatchTitle,
          addedAt: item.addedAt.getTime(),
          status: item.status === "finished" ? "completed" : item.status,
          resultFolder: item.folder,
          excludeColor: item.excludeColor,
          requestId: `${batch.id}:${item.id}`,
        })),
      }),
    ], { type: "application/json" }),
    "manifest.json"
  );
  batch.items.forEach((item) => {
    if (item.status !== "finished") form.append(item.id, item.file, item.file.name);
  });

  const url = apiUrl(`/api/batches/${encodeURIComponent(batch.id)}`);

  if (typeof XMLHttpRequest !== "undefined") {
    return new Promise<ServerBatch>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("PUT", url);
      if (onProgress && xhr.upload) {
        xhr.upload.onprogress = (event) => {
          if (event.lengthComputable && event.total > 0) {
            onProgress(Math.round((event.loaded / event.total) * 100));
          }
        };
      }
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            const parsed = JSON.parse(xhr.responseText) as ServerBatch;
            resolve(parsed);
          } catch {
            reject(new Error("Malformed JSON response from server"));
          }
        } else {
          reject(new Error(xhr.responseText || `Could not submit batch (${xhr.status})`));
        }
      };
      xhr.onerror = () => reject(new Error("Network error while submitting batch"));
      xhr.ontimeout = () => reject(new Error("Batch submission timed out"));
      xhr.send(form);
    });
  }

  const response = await fetch(url, {
    method: "PUT",
    body: form,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Could not submit batch (${response.status})`);
  }
  return (await response.json()) as ServerBatch;
};

export const batchAction = async (batchId: string, action: "pause" | "resume" | "dismiss") => {
  const response = await fetch(apiUrl(`/api/batches/${encodeURIComponent(batchId)}/${action}`), {
    method: "POST",
  });
  if (!response.ok) throw new Error(`Batch action failed (${response.status})`);
  return (await response.json()) as ServerBatch;
};

export const updateBatchTranslator = async (batchId: string, translator: TranslatorKey) => {
  const response = await fetch(apiUrl(`/api/batches/${encodeURIComponent(batchId)}`), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ translator }),
  });
  if (!response.ok) throw new Error(`Translator update failed (${response.status})`);
};

export const updateBatchManualReview = async (batchId: string, enabled: boolean) => {
  const response = await fetch(apiUrl(`/api/batches/${encodeURIComponent(batchId)}`), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ keep_failed_pages_for_editing: enabled }),
  });
  if (!response.ok) throw new Error(`Manual review setting update failed (${response.status})`);
};

export const updateBatchTitle = async (batchId: string, mangaTitle: string) => {
  const cleanTitle = mangaTitle.trim() || "Ungrouped";
  const response = await fetch(apiUrl(`/api/batches/${encodeURIComponent(batchId)}`), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mangaTitle: cleanTitle }),
  });
  if (!response.ok) throw new Error(`Batch title update failed (${response.status})`);
};

export const updateBatchPriority = async (batchId: string, priority: boolean) => {
  const response = await fetch(apiUrl(`/api/batches/${encodeURIComponent(batchId)}`), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ priority }),
  });
  if (!response.ok) throw new Error(`Batch priority update failed (${response.status})`);
  return (await response.json()) as ServerBatch;
};

export const retryBatchItem = async (
  batchId: string,
  itemId: string,
  keepFailedPagesForEditing = false,
  fromStage?: string,
) => {
  const response = await fetch(apiUrl(`/api/batches/${encodeURIComponent(batchId)}/items/${encodeURIComponent(itemId)}/retry`), {
    method: "POST",
    ...(keepFailedPagesForEditing || fromStage
      ? {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ...(keepFailedPagesForEditing ? { keep_failed_pages_for_editing: true } : {}),
            ...(fromStage ? { fromStage } : {}),
          }),
        }
      : {}),
  });
  if (!response.ok) throw new Error(`Retry failed (${response.status})`);
  return (await response.json()) as ServerBatch;
};

export const updateBatchItem = async (batchId: string, itemId: string, excludeColor: boolean) => {
  const response = await fetch(apiUrl(`/api/batches/${encodeURIComponent(batchId)}/items/${encodeURIComponent(itemId)}`), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ excludeColor }),
  });
  if (!response.ok) throw new Error(`Page update failed (${response.status})`);
};

export const removeBatchItem = async (batchId: string, itemId: string) => {
  const response = await fetch(apiUrl(`/api/batches/${encodeURIComponent(batchId)}/items/${encodeURIComponent(itemId)}`), { method: "DELETE" });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || `Page removal failed (${response.status})`);
  }
};

export const removeServerBatch = async (batchId: string) => {
  const response = await fetch(apiUrl(`/api/batches/${encodeURIComponent(batchId)}`), { method: "DELETE" });
  if (!response.ok && response.status !== 404) throw new Error(`Batch removal failed (${response.status})`);
};
