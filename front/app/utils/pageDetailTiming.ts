import type { FinishedImage, PipelineRunManifest, PipelineRunStage } from "@/types";
import { apiUrl } from "@/utils/api";
import { resultFolderFromUrl } from "@/utils/resultPaths";

export const formatTimestamp = (value?: string | number | Date | null): string => {
  if (value == null) return "—";
  const date = value instanceof Date ? value : new Date(value);
  if (!Number.isFinite(date.getTime())) return "—";
  return [date.getHours(), date.getMinutes(), date.getSeconds()].map((part) => String(part).padStart(2, "0")).join(":");
};

export const timestampTooltip = (value?: string | number | Date | null): string | undefined => {
  if (value == null) return undefined;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isFinite(date.getTime()) ? `${date.toISOString()} · ${date.toLocaleString()}` : undefined;
};

export const resolveTranslationTiming = (
  manifest: PipelineRunManifest | null | undefined,
  finishedAt?: string | number | Date | null,
  startedAt?: string | number | Date | null,
  durationMs?: number | null,
) => {
  const startAt = startedAt || manifest?.createdAt || null;
  const endAt = finishedAt || manifest?.updatedAt || null;
  const startMs = startAt instanceof Date ? startAt.getTime() : startAt ? Date.parse(String(startAt)) : NaN;
  const endMs = endAt instanceof Date ? endAt.getTime() : endAt ? Date.parse(String(endAt)) : NaN;
  const stageDuration = manifest?.stages?.reduce<number | null>(
    (total, stage) => Number.isFinite(stage.durationMs) ? (total ?? 0) + (stage.durationMs || 0) : total, null,
  ) ?? null;
  return {
    startAt,
    endAt,
    durationMs: stageDuration ?? (durationMs != null && Number.isFinite(durationMs)
      ? durationMs
      : Number.isFinite(startMs) && Number.isFinite(endMs) && endMs >= startMs ? endMs - startMs : null),
  };
};

export const resolveStagesToRetry = (stages: PipelineRunStage[] | undefined, selectedStageId: string): string[] => {
  const canonical = (id: string) => ({ upscaling: "upscale", textline_merge: "text_grouping" }[id] ?? id);
  const selected = canonical(selectedStageId);
  if (!stages) return [];
  const manifestStages = new Map(stages.map((stage) => [canonical(stage.id), stage]));
  if (!manifestStages.has(selected)) return [];
  const affected = new Set([selected]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const [stageId, stage] of manifestStages) {
      const required = (stage.dependsOn || []).map(canonical);
      if (!affected.has(stageId) && required.some((id) => affected.has(id))) { affected.add(stageId); changed = true; }
    }
  }
  return Array.from(manifestStages).filter(([stageId]) => affected.has(stageId))
    .map(([, stage]) => stage).flatMap((stage) => !stage || stage.status === "skipped" ? [] : [stage]).map((stage) => stage.label || stage.id);
};

export const shouldLoadTranslationArtifacts = (sourceType?: FinishedImage["sourceType"]): boolean => sourceType !== "original";

export interface ResolvedImageUrls {
  folder: string | null;
  resultUrl: Blob | string | null;
  resultPreviewUrl: Blob | string | null;
  resultPlaceholderUrl: string | null;
  inpaintedUrl: string | null;
  inpaintedPreviewUrl: string | null;
  bubbleMaskUrl: string | null;
  originalUrl: Blob | File | string | null;
  originalPreviewUrl: Blob | File | string | null;
}

export const resolveImageUrls = (
  image: FinishedImage | null | undefined,
  apiUrlFn: (path: string) => string = apiUrl,
): ResolvedImageUrls => {
  if (!image) {
    return { folder: null, resultUrl: null, resultPreviewUrl: null, resultPlaceholderUrl: null, inpaintedUrl: null, inpaintedPreviewUrl: null, bubbleMaskUrl: null, originalUrl: null, originalPreviewUrl: null };
  }
  const isOriginal = image.sourceType === "original";
  const folder = image.folder ?? (typeof image.result === "string" ? resultFolderFromUrl(image.result) : null) ?? (image.fullUrl ? resultFolderFromUrl(image.fullUrl) : null);
  const resultUrl = image.fullUrl ? apiUrlFn(image.fullUrl) : (image.result instanceof Blob && image.result.size < 1000 && folder ? apiUrlFn(`/result/${folder}/final.jpg`) : image.result || (folder ? apiUrlFn(`/result/${folder}/final.jpg`) : null));
  const resultPreviewUrl = image.readerUrl ? apiUrlFn(image.readerUrl) : image.detailPreviewUrl ? apiUrlFn(image.detailPreviewUrl) : folder ? apiUrlFn(`/result/${folder}/reader.webp`) : resultUrl;
  const resultPlaceholderUrl = image.batchPreviewUrl ? apiUrlFn(image.batchPreviewUrl) : image.thumbnailUrl ? apiUrlFn(image.thumbnailUrl) : image.detailPreviewUrl ? apiUrlFn(image.detailPreviewUrl) : image.coverUrl ? apiUrlFn(image.coverUrl) : folder ? apiUrlFn(`/result/${folder}/preview.webp`) : typeof image.result === "string" && !image.result.startsWith("blob:") ? apiUrlFn(image.result) : null;
  const inpaintedUrl = image.inpaintedUrl ? apiUrlFn(image.inpaintedUrl) : folder ? apiUrlFn(`/result/${folder}/inpainted.jpg`) : null;
  const inpaintedPreviewUrl = folder ? apiUrlFn(`/result/${folder}/inpainted-reader.webp`) : inpaintedUrl;
  const bubbleMaskUrl = image.bubbleMaskUrl ? apiUrlFn(image.bubbleMaskUrl) : null;

  let originalUrl: Blob | File | string | null = null;
  if (image.inputUrl) originalUrl = typeof image.inputUrl === "string" ? apiUrlFn(image.inputUrl) : image.inputUrl;
  else if (isOriginal) originalUrl = image.fullUrl || image.result || (folder ? apiUrlFn(`/result/${folder}/input.png`) : null);
  else if (folder) originalUrl = apiUrlFn(`/result/${folder}/input.png`);
  const originalPreviewUrl = folder && !(image.inputUrl instanceof Blob) ? apiUrlFn(`/result/${folder}/input-reader.webp`) : originalUrl;

  return { folder, resultUrl, resultPreviewUrl, resultPlaceholderUrl, inpaintedUrl, inpaintedPreviewUrl, bubbleMaskUrl, originalUrl, originalPreviewUrl };
};
