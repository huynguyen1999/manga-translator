import { useCallback } from "react";
import type { Dispatch, SetStateAction } from "react";
import type {
  MangaGroupSelection,
  StudioFile,
  TranslationBatch,
  TranslationSettings,
} from "@/types";
import { imageMimeTypes } from "@/config";
import { apiUrl } from "@/utils/api";
import {
  loadTranslationBatchFromIDB,
  removeTranslationBatchFromIDB,
  saveTranslationBatchToIDB,
} from "@/utils/fileStorage";
import { isArchiveFile } from "@/utils/zipUtils";

type ImportedMangaItem = {
  id?: string;
  groupId?: string | null;
  folder?: string;
  originalName?: string;
  pageOrder?: number | null;
  sourcePath?: string | null;
};

type ImportedMangaResponse = {
  group?: unknown;
  items?: ImportedMangaItem[];
};

type StudioMangaUploadOptions = {
  pendingStudioMangaFiles: StudioFile[];
  setPendingStudioMangaFiles: Dispatch<SetStateAction<StudioFile[]>>;
  setStudioMangaUploadError: Dispatch<SetStateAction<string | null>>;
  setStudioMangaUploadWarning: Dispatch<SetStateAction<string | null>>;
  setIsStudioMangaUploadModalOpen: Dispatch<SetStateAction<boolean>>;
  setTranslationBatches: Dispatch<SetStateAction<TranslationBatch[]>>;
  isConfirmingStudioUploadRef: { current: boolean };
  studioUploadRequestsRef: { current: Map<string, Promise<void>> };
  getCurrentSettings: () => TranslationSettings;
  loadMangaSummaries: (signal?: AbortSignal) => Promise<void>;
  clearForm: () => void;
};

export const importOriginalManga = async (
  files: StudioFile[],
  mangaTitle: string,
  onProgress?: (progress: number) => void,
  groupId?: string | null,
  isNewGroup?: boolean,
): Promise<ImportedMangaResponse> => {
  const cleanTitle = mangaTitle.trim();
  const form = new FormData();
  form.append("mangaTitle", cleanTitle);
  if (groupId) {
    form.append("groupId", groupId);
    form.append("mangaGroupId", groupId);
  }
  if (isNewGroup !== undefined) {
    form.append("isNewGroup", isNewGroup ? "true" : "false");
  }
  form.append(
    "pageMetadata",
    JSON.stringify(files.map((entry) => ({
      originalName: entry.file.name,
      sourcePath: entry.sourcePath,
    }))),
  );
  files.forEach((entry) => form.append("files", entry.file, entry.file.name));

  const data = await new Promise<ImportedMangaResponse>((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", apiUrl("/api/results/import"));
    request.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) {
        onProgress?.(Math.min(100, Math.round((event.loaded / event.total) * 100)));
      }
    };
    request.onerror = () => reject(new Error("Could not reach the import server"));
    request.onabort = () => reject(new Error("Manga import was cancelled"));
    request.onload = () => {
      let payload: ImportedMangaResponse & { detail?: string };
      try {
        payload = JSON.parse(request.responseText) as ImportedMangaResponse & { detail?: string };
      } catch {
        payload = {} as ImportedMangaResponse;
      }
      if (request.status < 200 || request.status >= 300) {
        const error = new Error(payload.detail || `Import failed (${request.status})`) as Error & { status?: number };
        error.status = request.status;
        reject(error);
        return;
      }
      resolve(payload);
    };
    request.send(form);
  });
  if (!data.group) throw new Error("Import completed without a manga group");
  return data;
};

export const useStudioMangaUpload = ({
  pendingStudioMangaFiles,
  setPendingStudioMangaFiles,
  setStudioMangaUploadError,
  setStudioMangaUploadWarning,
  setIsStudioMangaUploadModalOpen,
  setTranslationBatches,
  isConfirmingStudioUploadRef,
  studioUploadRequestsRef,
  getCurrentSettings,
  loadMangaSummaries,
  clearForm,
}: StudioMangaUploadOptions) => {
  const importOriginalMangaRequest = useCallback(importOriginalManga, []);

  const createStudioUploadBatch = (
    filesToUpload: StudioFile[],
    mangaTitle: string,
    groupId?: string | null,
    isNewGroup?: boolean,
  ): TranslationBatch => {
    const cleanTitle = mangaTitle.trim() || "Ungrouped";
    const addedAt = new Date();
    return {
      id: `upload-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      kind: "manga-upload",
      addedAt,
      mangaTitle: cleanTitle,
      mangaGroupId: groupId || null,
      isNewGroup,
      settings: {
        ...getCurrentSettings(),
        translator: "none",
        inpainter: "original",
        colorizer: "none",
        colorizeOnly: false,
      },
      items: filesToUpload.map((file, index) => ({
        id: `upload-${addedAt.getTime()}-${index}`,
        mangaGroupId: groupId || null,
        file: file.file,
        sourcePath: file.sourcePath,
        addedAt,
        status: "queued" as const,
        mangaTitle: cleanTitle,
      })),
      totalItems: filesToUpload.length,
      completedCount: 0,
      uploadProgress: 0,
      status: "uploading",
    };
  };

  const failStudioMangaUpload = async (uploadBatch: TranslationBatch, error: unknown) => {
    const errorMessage = error instanceof Error ? error.message : "Could not upload manga.";
    const failedBatch = {
      ...uploadBatch,
      status: "error" as const,
      error: errorMessage,
      items: uploadBatch.items.map((item) => ({
        ...item,
        status: "error" as const,
        error: errorMessage,
      })),
    };
    setTranslationBatches((prev) => prev.map((batch) =>
      batch.id === uploadBatch.id ? failedBatch : batch
    ));
    await removeTranslationBatchFromIDB(uploadBatch.id).catch((removeError) =>
      console.warn(`Failed to remove failed manga upload ${uploadBatch.id}:`, removeError)
    );
  };

  const resumeStudioMangaUpload = (uploadBatch: TranslationBatch): Promise<void> => {
    const pending = studioUploadRequestsRef.current.get(uploadBatch.id);
    if (pending) return pending;

    const performUpload = async (batch: TranslationBatch) => {
      try {
        let data: ImportedMangaResponse;
        try {
          data = await importOriginalMangaRequest(
            batch.items.map((item, index) => ({
              id: item.id,
              file: item.file,
              sourcePath: item.sourcePath || item.file.name,
              addedAt: item.addedAt.getTime(),
              dropOrder: index,
            })),
            batch.mangaTitle,
            (uploadProgress) => setTranslationBatches((prev) => prev.map((batchItem) =>
              batchItem.id === uploadBatch.id ? { ...batchItem, uploadProgress } : batchItem
            )),
            batch.mangaGroupId || batch.items[0]?.mangaGroupId || null,
            batch.isNewGroup,
          );
        } catch (error) {
          if ((error as Error & { status?: number }).status !== 409) throw error;
          setStudioMangaUploadWarning(
            "A manga with this title already exists. The existing manga was kept; no new upload was created.",
          );
          const response = await fetch(
            apiUrl(`/api/results/list?groupId=${encodeURIComponent(batch.items[0]?.mangaGroupId || batch.mangaTitle)}&limit=500`),
          );
          if (!response.ok) throw error;
          data = (await response.json()) as ImportedMangaResponse;
          if (!Array.isArray(data.items) || data.items.length === 0) throw error;
        }
        if (!data.items?.some((item) => item.folder)) {
          throw new Error("Import completed without any manga pages");
        }
        await removeTranslationBatchFromIDB(uploadBatch.id);
        setTranslationBatches((prev) => prev.filter((batchItem) => batchItem.id !== uploadBatch.id));
        void loadMangaSummaries().catch((error) =>
          console.warn("Manga imported, but the gallery could not be refreshed:", error)
        );
      } catch (error) {
        await failStudioMangaUpload(uploadBatch, error);
        console.warn(`Failed to resume manga upload ${uploadBatch.id}:`, error);
      }
    };

    const request = (async () => {
      const runUpload = async () => {
        const persisted = await loadTranslationBatchFromIDB(uploadBatch.id);
        if (!persisted) {
          setTranslationBatches((prev) => prev.filter((batch) => batch.id !== uploadBatch.id));
          await loadMangaSummaries();
          return;
        }
        await performUpload(persisted);
      };

      if (typeof navigator !== "undefined" && navigator.locks?.request) {
        await navigator.locks.request(`manga-original-upload:${uploadBatch.id}`, runUpload);
      } else {
        await runUpload();
      }
    })();
    studioUploadRequestsRef.current.set(uploadBatch.id, request);
    void request.then(
      () => studioUploadRequestsRef.current.delete(uploadBatch.id),
      () => studioUploadRequestsRef.current.delete(uploadBatch.id),
    );
    return request;
  };

  const handleStudioMangaUpload = (files: StudioFile[]) => {
    const supported = (file: File) =>
      isArchiveFile(file) ||
      imageMimeTypes.includes(file.type) ||
      /\.(png|jpe?g|bmp|webp|tiff?|gif|avif|jfif|tga)$/i.test(file.name);
    if (files.some((entry) => !supported(entry.file))) {
      setStudioMangaUploadError("Use PNG, JPEG, BMP, WEBP, TIFF, GIF, or AVIF images, or CBZ / ZIP archives.");
      return;
    }

    setStudioMangaUploadError(null);
    setStudioMangaUploadWarning(null);
    setPendingStudioMangaFiles(files.slice());
    setIsStudioMangaUploadModalOpen(true);
  };

  const handleStudioMangaUploadConfirm = async (selection: MangaGroupSelection | string) => {
    if (isConfirmingStudioUploadRef.current) return;
    const rawTitle = typeof selection === "string" ? selection : selection.title;
    const cleanTitle = rawTitle.trim();
    if (!cleanTitle || cleanTitle.toLocaleLowerCase() === "ungrouped") {
      setStudioMangaUploadError("Enter a manga title.");
      return;
    }
    const groupId = typeof selection === "object" ? selection.groupId : undefined;
    const isNewGroup = typeof selection === "object" ? selection.isNewGroup : undefined;
    if (pendingStudioMangaFiles.length === 0) return;

    isConfirmingStudioUploadRef.current = true;
    const filesToUpload = [...pendingStudioMangaFiles];
    setIsStudioMangaUploadModalOpen(false);
    setPendingStudioMangaFiles([]);
    setStudioMangaUploadError(null);
    clearForm();

    const uploadBatch = createStudioUploadBatch(filesToUpload, cleanTitle, groupId, isNewGroup);
    setTranslationBatches((prev) => [uploadBatch, ...prev]);
    isConfirmingStudioUploadRef.current = false;

    try {
      await saveTranslationBatchToIDB(uploadBatch);
      await resumeStudioMangaUpload(uploadBatch);
    } catch (error) {
      await failStudioMangaUpload(uploadBatch, error);
    }
  };

  const closeStudioMangaUploadModal = () => {
    setIsStudioMangaUploadModalOpen(false);
    setPendingStudioMangaFiles([]);
    setStudioMangaUploadError(null);
  };

  return {
    handleStudioMangaUpload,
    handleStudioMangaUploadConfirm,
    closeStudioMangaUploadModal,
    resumeStudioMangaUpload,
  };
};
