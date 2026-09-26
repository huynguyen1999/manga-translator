import type { FinishedImage, StudioFile, TranslationSettings } from "@/types";
import { apiUrl } from "@/utils/api";
import { resultFolderFromUrl } from "@/utils/resultPaths";

export type StudioPreviewOptions = {
  folder?: string;
  fileName?: string;
  settings?: Partial<TranslationSettings>;
  mangaTitle?: string;
  offlineModel?: string;
  geminiModel?: string;
  images?: FinishedImage[];
  currentIndex?: number;
};

type BuildPreviewImageOptions = {
  file: File | StudioFile | string;
  result: Blob | File | string | null;
  sourceType?: FinishedImage["sourceType"];
  options?: StudioPreviewOptions;
  folderMap: Map<string, string>;
  settings: Partial<TranslationSettings>;
};

export const buildStudioPreviewImage = ({
  file, result, sourceType, options, folderMap, settings,
}: BuildPreviewImageOptions): FinishedImage | null => {
  if (!result) return null;
  const studioFile = typeof file === "object" && "file" in file ? file : null;
  const studioId = studioFile?.id || null;
  const rawFile: File | string = studioFile ? studioFile.file : file as File | string;
  const fileName = options?.fileName ?? (typeof rawFile === "string" ? (rawFile.includes("/") ? rawFile.split("/").pop() || rawFile : rawFile) : rawFile.name);
  const folder = options?.folder ?? (studioId ? folderMap.get(studioId) : null) ?? folderMap.get(fileName)
    ?? (typeof result === "string" ? resultFolderFromUrl(result) : null) ?? (typeof file === "string" ? resultFolderFromUrl(file) : null) ?? undefined;
  const inputUrl = rawFile instanceof File && folder ? apiUrl(`/result/${folder}/input.png`) : (rawFile instanceof File ? rawFile : (typeof rawFile === "string" ? rawFile : null));
  const mergedSettings: Partial<TranslationSettings> = { ...settings, ...(options?.settings || {}) };
  if (options?.offlineModel) mergedSettings.offlineModel = options.offlineModel;
  if (options?.geminiModel) mergedSettings.geminiModel = options.geminiModel;

  return {
    id: `${fileName}-${Date.now()}`,
    originalName: fileName,
    mangaTitle: options?.mangaTitle,
    sourcePath: studioFile?.sourcePath,
    result: result as Blob | string,
    thumbnailUrl: folder ? `/result/${folder}/thumbnail.webp` : undefined,
    batchPreviewUrl: folder ? `/result/${folder}/batch.webp` : undefined,
    detailPreviewUrl: folder ? `/result/${folder}/preview.webp` : undefined,
    readerUrl: folder ? `/result/${folder}/reader.webp` : undefined,
    sourceType,
    inputUrl,
    inpaintedUrl: folder ? `/result/${folder}/inpainted.jpg` : undefined,
    textRegionsUrl: folder ? `/result/${folder}/text_regions.json` : undefined,
    hasTextRegions: Boolean(folder),
    folder,
    finishedAt: new Date(),
    settings: mergedSettings,
  };
};
