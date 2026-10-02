import { useCallback, useEffect, useRef } from "react";
import type { Dispatch, SetStateAction } from "react";
import type {
  MangaGroupSelection,
  StudioFile,
  TranslationBatch,
  TranslationSettings,
} from "@/types";
import { imageMimeTypes } from "@/config";
import type { MangaImportJob, MangaImportJobStatus } from "./mangaImportJobs";
import {
  dismissMangaImportJobRequest,
  fetchMangaImportJobs,
  importOriginalManga,
  type MangaImportJobWatcher,
  retryMangaImportJobRequest,
  upsertMangaImportJob,
  watchMangaImportJobs,
} from "./mangaImportJobs";
import {
  loadTranslationBatchFromIDB,
  removeTranslationBatchFromIDB,
  saveTranslationBatchToIDB,
} from "@/utils/fileStorage";
import { isArchiveFile } from "@/utils/zipUtils";

export {
  dismissMangaImportJobRequest,
  fetchMangaImportJobs,
  importOriginalManga,
  retryMangaImportJobRequest,
  watchMangaImportJobs,
} from "./mangaImportJobs";

type StudioMangaUploadOptions = {
  pendingStudioMangaFiles: StudioFile[];
  setPendingStudioMangaFiles: Dispatch<SetStateAction<StudioFile[]>>;
  setStudioMangaUploadError: Dispatch<SetStateAction<string | null>>;
  setStudioMangaUploadWarning: Dispatch<SetStateAction<string | null>>;
  setIsStudioMangaUploadModalOpen: Dispatch<SetStateAction<boolean>>;
  setTranslationBatches: Dispatch<SetStateAction<TranslationBatch[]>>;
  setMangaImportJobs: Dispatch<SetStateAction<MangaImportJob[]>>;
  isConfirmingStudioUploadRef: { current: boolean };
  studioUploadRequestsRef: { current: Map<string, Promise<void>> };
  getCurrentSettings: () => TranslationSettings;
  loadMangaSummaries: (signal?: AbortSignal) => Promise<void>;
  clearForm: () => void;
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null;

const errorStatus = (error: unknown): number | undefined =>
  isRecord(error) && typeof error.status === "number" ? error.status : undefined;

export const useStudioMangaUpload = ({
  pendingStudioMangaFiles,
  setPendingStudioMangaFiles,
  setStudioMangaUploadError,
  setStudioMangaUploadWarning,
  setIsStudioMangaUploadModalOpen,
  setTranslationBatches,
  setMangaImportJobs,
  isConfirmingStudioUploadRef,
  studioUploadRequestsRef,
  getCurrentSettings,
  loadMangaSummaries,
  clearForm,
}: StudioMangaUploadOptions) => {
  const jobsRef = useRef(new Map<string, MangaImportJobStatus>());
  const refreshRequestRef = useRef<Promise<MangaImportJob[]> | null>(null);
  const jobWatcherRef = useRef<MangaImportJobWatcher | null>(null);

  const refreshMangaImportJobs = useCallback((): Promise<MangaImportJob[]> => {
    if (refreshRequestRef.current) return refreshRequestRef.current;
    const request = fetchMangaImportJobs().then((jobs) => {
      const previous = jobsRef.current;
      jobsRef.current = new Map(jobs.map((job) => [job.id, job.status]));
      setMangaImportJobs(jobs);
      if (jobs.some((job) => job.status === "completed" && previous.get(job.id) !== "completed")) {
        void loadMangaSummaries().catch((error) =>
          console.warn("Manga imported, but the gallery could not be refreshed:", error)
        );
      }
      if (jobs.some((job) => job.status === "failed" && /duplicate|already exists/i.test(job.error || ""))) {
        setStudioMangaUploadWarning(
          "A manga with this title already exists. The existing manga was kept; no new upload was created.",
        );
      }
      return jobs;
    }).finally(() => {
      if (refreshRequestRef.current === request) refreshRequestRef.current = null;
    });
    refreshRequestRef.current = request;
    return request;
  }, [loadMangaSummaries, setMangaImportJobs, setStudioMangaUploadWarning]);

  useEffect(() => {
    const refresh = () => refreshMangaImportJobs().catch((error) => {
      console.warn("Failed to load manga imports:", error);
      throw error;
    });
    const watcher = watchMangaImportJobs(refresh, window, document);
    jobWatcherRef.current = watcher;
    return () => {
      watcher.stop();
      if (jobWatcherRef.current === watcher) jobWatcherRef.current = null;
    };
  }, [refreshMangaImportJobs]);

  const retryMangaImportJob = useCallback(async (id: string) => {
    await retryMangaImportJobRequest(id);
    await (jobWatcherRef.current?.refresh() ?? refreshMangaImportJobs());
  }, [refreshMangaImportJobs]);

  const dismissMangaImportJob = useCallback(async (id: string) => {
    await dismissMangaImportJobRequest(id);
    setMangaImportJobs((jobs) => jobs.filter((job) => job.id !== id));
  }, [setMangaImportJobs]);

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
      items: uploadBatch.items.map((item) => ({ ...item, status: "error" as const, error: errorMessage })),
    };
    setTranslationBatches((prev) => prev.map((batch) => batch.id === uploadBatch.id ? failedBatch : batch));
  };

  const resumeStudioMangaUpload = (uploadBatch: TranslationBatch): Promise<void> => {
    const pending = studioUploadRequestsRef.current.get(uploadBatch.id);
    if (pending) return pending;

    const performUpload = async (batch: TranslationBatch) => {
      try {
        const job = await importOriginalManga(
          batch.items.map((item, index) => ({
            id: item.id,
            file: item.file,
            sourcePath: item.sourcePath || item.file.name,
            addedAt: item.addedAt.getTime(),
            dropOrder: index,
          })),
          batch.mangaTitle,
          (uploadProgress) => setTranslationBatches((prev) => prev.map((candidate) =>
            candidate.id === uploadBatch.id ? { ...candidate, uploadProgress } : candidate
          )),
          batch.mangaGroupId || batch.items[0]?.mangaGroupId || null,
          batch.isNewGroup,
          batch.id,
        );
        await removeTranslationBatchFromIDB(uploadBatch.id).catch((error) =>
          console.warn(`Accepted manga upload ${uploadBatch.id}, but could not remove its local draft:`, error)
        );
        upsertMangaImportJob(setMangaImportJobs, job);
        setTranslationBatches((prev) => prev.filter((candidate) => candidate.id !== uploadBatch.id));
        void (jobWatcherRef.current?.refresh() ?? refreshMangaImportJobs()).catch((error) =>
          console.warn("Failed to refresh manga imports:", error)
        );
      } catch (error) {
        if (errorStatus(error) === 409) {
          setStudioMangaUploadWarning(
            "A manga with this title already exists. The existing manga was kept; no new upload was created.",
          );
          await removeTranslationBatchFromIDB(uploadBatch.id).catch(() => {});
          setTranslationBatches((prev) => prev.filter((candidate) => candidate.id !== uploadBatch.id));
          void loadMangaSummaries().catch((refreshError) => console.warn("Failed to refresh manga gallery:", refreshError));
          return;
        }
        await failStudioMangaUpload(uploadBatch, error);
        console.warn(`Failed to upload manga ${uploadBatch.id}:`, error);
      }
    };

    const request = (async () => {
      const runUpload = async () => {
        const persisted = await loadTranslationBatchFromIDB(uploadBatch.id).catch(() => null);
        await performUpload(persisted?.items.some((item) => item.file.size > 0) ? persisted : uploadBatch);
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
      isArchiveFile(file) || imageMimeTypes.includes(file.type) || /\.(png|jpe?g|bmp|webp|tiff?|gif|avif|jfif|tga)$/i.test(file.name);
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
    const uploadBatch = createStudioUploadBatch([...pendingStudioMangaFiles], cleanTitle, groupId, isNewGroup);
    setIsStudioMangaUploadModalOpen(false);
    setPendingStudioMangaFiles([]);
    setStudioMangaUploadError(null);
    clearForm();
    setTranslationBatches((prev) => [uploadBatch, ...prev]);
    isConfirmingStudioUploadRef.current = false;
    await saveTranslationBatchToIDB(uploadBatch).catch((error) =>
      console.warn(`Could not retain manga upload draft ${uploadBatch.id}:`, error)
    );
    await resumeStudioMangaUpload(uploadBatch);
  };

  const closeStudioMangaUploadModal = () => {
    setIsStudioMangaUploadModalOpen(false);
    setPendingStudioMangaFiles([]);
    setStudioMangaUploadError(null);
    setStudioMangaUploadWarning(null);
  };

  return {
    handleStudioMangaUpload,
    handleStudioMangaUploadConfirm,
    closeStudioMangaUploadModal,
    resumeStudioMangaUpload,
    retryMangaImportJob,
    dismissMangaImportJob,
    refreshMangaImportJobs,
  };
};
