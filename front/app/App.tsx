import React, { useState, useEffect, useMemo, useRef, useCallback } from "react";
import { Icon } from "@iconify/react";
import {
  processingStatuses,
  type TranslatorKey,
  type FileStatus,
  type FinishedImage,
  type TranslationBatch,
  type SummaryJob,
  type StudioFile,
} from "@/types";
import { JobsDrawer } from "@/components/JobsDrawer";
import { ResultGallery } from "@/components/ResultGallery";
import { Header } from "@/components/Header";
import { GroupSelectionModal, type ExistingGroupEntry } from "@/components/GroupSelectionModal";
import { TranslationSubmitModal } from "@/components/TranslationSubmitModal";
const SearchLab = React.lazy(() => import("@/components/SearchLab"));
import { PageDetailModal } from "@/components/PageDetailModal";
import PreviewImage from "@/components/PreviewImage";

import {
  clearFilesFromIDB,
  saveTranslationBatchToIDB,
} from "@/utils/fileStorage";
import { apiUrl } from "@/utils/api";
import { PipelineRerunDialog } from "@/components/PipelineRerunDialog";

import {
  subscribeSummaryJobs,
} from "@/utils/summaryJobs";
import {
  type SelectionAnchor,
} from "@/utils/selectionUtils";
import { useBatchActions } from "@/features/batches/useBatchActions";
import { loadBatchDetails } from "@/features/batches/loadBatchDetails";
import { useServerBatchEvents } from "@/features/batches/useServerBatchEvents";
import { useSummaryJobActions } from "@/features/summaries/useSummaryJobActions";
import { useAppNavigation } from "@/features/navigation/useAppNavigation";
import { useTranslationSettings } from "@/features/translation/useTranslationSettings";
import { createTranslationSubmissionActions } from "@/features/translation/translationSubmission";
import { useStudioFileIntake } from "@/features/upload/useStudioFileIntake";
import { useStudioMangaUpload } from "@/features/upload/useStudioMangaUpload";
import { createStudioFileSelectionActions } from "@/features/upload/studioFileSelection";
import { useStudioColorActions } from "@/features/upload/useStudioColorActions";
import { createTranslationBatchUploadActions } from "@/features/upload/translationBatchUpload";
import { createGalleryMutationActions } from "@/features/gallery/galleryMutations";
import { useMangaSummaries } from "@/features/gallery/useMangaSummaries";
import { useGalleryData, useGalleryLoadEffect } from "@/features/gallery/useGalleryData";
import { buildExistingGroupEntries } from "@/utils/groupTitles";
import { StudioWorkspace } from "@/features/studio/StudioWorkspace";
import { useStudioInitialization } from "@/features/studio/useStudioInitialization";
import { useAppPersistence } from "@/features/studio/useAppPersistence";
import { useStudioLightbox } from "@/features/studio/useStudioLightbox";
import { usePipelineRerunActions } from "@/features/batches/usePipelineRerunActions";

// Manga Translation Studio Application Main Component
export const App: React.FC = () => {
  // Theme & Navigation state
  const [theme, setTheme] = useState<"light" | "dark">("dark");
  const {
    parsedRoute,
    activeView,
    handleOpenPageView,
    handleOpenPageEdit,
    handleOpenReader,
    handleOpenMangaDetail,
    handleGalleryPageChange,
    handleGalleryPageSizeChange,
    handleGallerySearchChange,
    handleGallerySortChange,
    handleGalleryReviewChange,
    handleGalleryStatusChange,
    handleCloseMangaDetail,
    handleCloseOverlay,
    handleOpenSeriesDetail,
    handleCloseSeriesDetail,
  } = useAppNavigation();

  // State Hooks
  const [fileStatuses, setFileStatuses] = useState<Map<string, FileStatus>>(
    new Map()
  );
  const [files, setFiles] = useState<StudioFile[]>([]);

  // New state for improved UI features
  const [translationBatches, setTranslationBatches] = useState<TranslationBatch[]>([]);
  const batchDetailRequestsRef = useRef(new Map<string, Promise<void>>());
  const studioUploadRequestsRef = useRef(new Map<string, Promise<void>>());
  const batchMutationVersionRef = useRef(0);
  const optimisticBatchTranslatorsRef = useRef(new Map<string, TranslatorKey>());
  const optimisticDismissedBatchIdsRef = useRef(new Set<string>());
  const optimisticDeletedBatchIdsRef = useRef(new Set<string>());
  const initialBatchLoadRef = useRef<Promise<void> | null>(null);

  const loadTranslationBatchDetails = React.useCallback((batchId: string) => (
    loadBatchDetails(
      batchId,
      batchDetailRequestsRef,
      optimisticBatchTranslatorsRef,
      setTranslationBatches,
    )
  ), []);
  const [finishedImages, setFinishedImages] = useState<FinishedImage[]>([]);
  const {
    mangaSummaries,
    setMangaSummaries,
    serverGroupTitles,
    isLoadingServerGroupTitles,
    loadAllServerGroupTitles,
    totalMangaCount,
    setTotalMangaCount,
    totalGalleryCount,
    setTotalGalleryCount,
    isGalleryLoading,
    setIsGalleryLoading,
    galleryRevision,
    setGalleryRevision,
    galleryLoadRequestRef,
    galleryPageCacheRef,
  } = useGalleryData();
  const [summaryJobs, setSummaryJobs] = useState<SummaryJob[]>([]);
  const [isJobsOpen, setIsJobsOpen] = useState(false);
  const [jobToast, setJobToast] = useState<{ status: "ready" | "error"; title: string; message: string } | null>(null);
  const previousSummaryJobsRef = useRef<SummaryJob[] | null>(null);
  const folderMapRef = useRef<Map<string, string>>(new Map());
  const translationBatchSnapshotRef = useRef<string | null>(null);
  // Track files that received a final URL via final_ready so the placeholder blob (status 0) doesn't overwrite them
  const resultUrlsRef = useRef<Set<string>>(new Set());
  const [currentMangaTitle, setCurrentMangaTitle] = useState<string>(() => {
    if (typeof window !== "undefined" && window.localStorage) {
      return window.localStorage.getItem("manga-studio-current-title") || "";
    }
    return "";
  });
  const [isStudioHydrated, setIsStudioHydrated] = useState(false);
  const studioDropOrderRef = useRef(0);

  useEffect(() => {
    return subscribeSummaryJobs((jobs) => {
      const previous = previousSummaryJobsRef.current;
      const completed = jobs.find((job) => {
        const old = previous?.find((candidate) => candidate.id === job.id);
        return old?.status === "generating" && (job.status === "ready" || job.status === "error");
      });
      if (completed) {
        setJobToast({
          status: completed.status === "ready" ? "ready" : "error",
          title: completed.title,
          message: completed.status === "ready" ? "Summary ready" : completed.jobError || "Summary generation failed",
        });
        window.setTimeout(() => setJobToast(null), 6000);
      }
      previousSummaryJobsRef.current = jobs;
      setSummaryJobs(jobs);
    });
  }, []);

  const loadMangaSummaries = useMangaSummaries({
    parsedRoute,
    setMangaSummaries,
    setTotalMangaCount,
    setTotalGalleryCount,
    setIsGalleryLoading,
    galleryLoadRequestRef,
    galleryPageCacheRef,
  });

  // Preload manga group titles on initial load
  useEffect(() => {
    void loadAllServerGroupTitles();
  }, [loadAllServerGroupTitles]);

  // Group selection prompt modal state
  const [isGroupModalOpen, setIsGroupModalOpen] = useState(false);
  const [pendingTranslationTargets, setPendingTranslationTargets] = useState<StudioFile[]>([]);
  const [translationBatchError, setTranslationBatchError] = useState<string | null>(null);
  const [isStudioMangaUploadModalOpen, setIsStudioMangaUploadModalOpen] = useState(false);
  const [pendingStudioMangaFiles, setPendingStudioMangaFiles] = useState<StudioFile[]>([]);
  const [studioMangaUploadError, setStudioMangaUploadError] = useState<string | null>(null);
  const [studioMangaUploadWarning, setStudioMangaUploadWarning] = useState<string | null>(null);
  const [isCheckingGroupTitle, setIsCheckingGroupTitle] = useState(false);
  const isConfirmingStudioUploadRef = useRef(false);
  const isConfirmingTranslationRef = useRef(false);

  useEffect(() => {
    if (isGroupModalOpen || isStudioMangaUploadModalOpen) {
      void loadAllServerGroupTitles();
    }
  }, [isGroupModalOpen, isStudioMangaUploadModalOpen, loadAllServerGroupTitles]);

  // Per-file selection for targeted translation
  const [selectedFiles, setSelectedFiles] = useState<Set<string>>(new Set());
  const lastSelectedFileRef = useRef<SelectionAnchor | null>(null);

  // Per-file coloring exclusion and auto-detection
  const [excludedColorFiles, setExcludedColorFiles] = useState<Set<string>>(new Set());
  const [autoDetectedColorFiles, setAutoDetectedColorFiles] = useState<Set<string>>(new Set());

  // Archive extraction state

  const {
    toggleExcludeColorFile,
    setExcludeColorForSelected,
    toggleQueueItemColor,
    checkColorForFiles,
  } = useStudioColorActions({
    selectedFiles,
    translationBatches,
    setExcludedColorFiles,
    setAutoDetectedColorFiles,
    setTranslationBatches,
  });

  const {
    pendingFiles,
    isExtractingArchive,
    archiveError,
    processIncomingFiles,
    reorderPendingFiles,
    commitPendingFiles,
    removePendingFile,
    clearPendingFiles,
    resetFileIntake,
    clearArchiveError,
    handleDrop,
    handleFileChange,
  } = useStudioFileIntake({
    files,
    setFiles,
    setSelectedFiles,
    setCurrentMangaTitle,
    checkColorForFiles,
    studioDropOrderRef,
  });

  // Theme synchronization effect
  useEffect(() => {
    const saved = localStorage.getItem("manga-studio-theme") as "light" | "dark" | null;
    const initialTheme =
      saved ||
      (typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light");
    setTheme(initialTheme);
    if (initialTheme === "dark") {
      document.documentElement.classList.add("dark");
    } else {
      document.documentElement.classList.remove("dark");
    }
  }, []);

  const toggleTheme = () => {
    setTheme((prev) => {
      const next = prev === "dark" ? "light" : "dark";
      localStorage.setItem("manga-studio-theme", next);
      if (next === "dark") {
        document.documentElement.classList.add("dark");
      } else {
        document.documentElement.classList.remove("dark");
      }
      return next;
    });
  };


  const {
    detectionResolution,
    setDetectionResolution,
    textDetector,
    setTextDetector,
    ocr,
    setOcr,
    renderFont,
    setRenderFont,
    renderTextDirection,
    setRenderTextDirection,
    letterCase,
    setLetterCase,
    translator,
    setTranslator,
    summaryModel,
    setSummaryModel,
    targetLanguage,
    setTargetLanguage,
    translationQuality,
    setTranslationQuality,
    inpaintingSize,
    setInpaintingSize,
    customUnclipRatio,
    setCustomUnclipRatio,
    customBoxThreshold,
    setCustomBoxThreshold,
    customOcrProb,
    setCustomOcrProb,
    maskDilationOffset,
    setMaskDilationOffset,
    bubbleDetection,
    setBubbleDetection,
    bubbleModel,
    setBubbleModel,
    inpainter,
    setInpainter,
    colorizer,
    setColorizer,
    colorizeOnly,
    setColorizeOnly,
    colorizationSize,
    setColorizationSize,
    denoiseSigma,
    setDenoiseSigma,
    colorThreshold,
    setColorThreshold,
    upscaler,
    setUpscaler,
    upscaleRatio,
    setUpscaleRatio,
    revertUpscaling,
    setRevertUpscaling,
    rememberSettings,
    setRememberSettings,
    translationBatchSize,
    setTranslationBatchSize,
    getCurrentSettings,
    hydrateSettings,
    persistSettings,
  } = useTranslationSettings();

  const {
    selectedImageForModal,
    setSelectedImageForModal,
    selectedImageRetry,
    setSelectedImageRetry,
    selectedModalImages,
    selectedModalIndex,
    setSelectedModalIndex,
    handleOpenLightbox,
    closeStudioViewer,
  } = useStudioLightbox({
    folderMapRef,
    settings: {
      detectionResolution,
      textDetector,
      ocr,
      renderTextDirection,
      letterCase,
      uppercase: letterCase === "uppercase",
      lowercase: letterCase === "lowercase",
      translator,
      targetLanguage,
      inpaintingSize,
      customUnclipRatio,
      customBoxThreshold,
      customOcrProb,
      ocrMinConfidence: customOcrProb,
      maskDilationOffset,
      bubbleDetection,
      inpainter,
      colorizer,
      colorizeOnly,
      colorizationSize,
      denoiseSigma,
      colorThreshold,
      upscaler,
      upscaleRatio: upscaleRatio ? Number(upscaleRatio) : null,
      revertUpscaling: Boolean(upscaleRatio) && revertUpscaling,
    },
  });

  const clearForm = () => {
    setFiles([]);
    resetFileIntake();
    setFileStatuses(() => new Map());
    setSelectedFiles(new Set());
    lastSelectedFileRef.current = null;
    setExcludedColorFiles(new Set());
    setAutoDetectedColorFiles(new Set());
    setCurrentMangaTitle("");
    resultUrlsRef.current.clear();
    folderMapRef.current.clear();
    clearFilesFromIDB().catch(() => {});
  };

  const {
    handleStudioMangaUpload,
    handleStudioMangaUploadConfirm,
    closeStudioMangaUploadModal,
    resumeStudioMangaUpload,
  } = useStudioMangaUpload({
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
  });

  const { failStudioTranslationUpload, resumeStudioTranslationUpload } =
    createTranslationBatchUploadActions({ setTranslationBatches, studioUploadRequestsRef });

  // Computed State (useMemo)
  const isProcessing = useMemo(() => {
    // If there are no files or no statuses, we're not processing
    if (files.length === 0 || fileStatuses.size === 0) return false;

    // Check if any file has a processing status
    return Array.from(fileStatuses.values()).some((fileStatus) => {
      if (!fileStatus || fileStatus.status === null) return false;
      return processingStatuses.includes(fileStatus.status);
    });
  }, [files, fileStatuses]);

  const isProcessingAllFinished = useMemo(() => {
    // If there are no files or no statuses, we're not finished
    if (files.length === 0 || fileStatuses.size === 0) return false;

    // Check if all files are finished
    return Array.from(fileStatuses.values()).every((status) => {
      if (!status || status.status === null) return false;
      return status.status === "finished";
    });
  }, [files, fileStatuses]);

  // Effects
  /** Load saved settings, fetch finished images from server, and restore persisted files from IDB */
  useStudioInitialization({
    hydrateSettings,
    studioState: {
      setFiles,
      setFileStatuses,
      setSelectedFiles,
      setExcludedColorFiles,
      setAutoDetectedColorFiles,
      setIsStudioHydrated,
      studioDropOrderRef,
      folderMapRef,
      resultUrlsRef,
    },
    batches: {
      initialBatchLoadRef,
      translationBatchSnapshotRef,
      optimisticDeletedBatchIdsRef,
      setTranslationBatches,
      resumeStudioMangaUpload,
      resumeStudioTranslationUpload,
    },
  });

  useGalleryLoadEffect(activeView, parsedRoute, loadMangaSummaries, setIsGalleryLoading);

  // Server-owned batches are shared by every device.
  useServerBatchEvents({
    initialBatchLoadRef,
    translationBatchSnapshotRef,
    optimisticBatchTranslatorsRef,
    optimisticDismissedBatchIdsRef,
    optimisticDeletedBatchIdsRef,
    setTranslationBatches,
    galleryPageCacheRef,
    setGalleryRevision,
    loadMangaSummaries,
  });

  useAppPersistence({
    activeView,
    currentMangaTitle,
    isStudioHydrated,
    files,
    fileStatuses,
    selectedFiles,
    excludedColorFiles,
    autoDetectedColorFiles,
    folderMapRef,
    persistSettings,
  });

  // Only library manga count as existing assignment targets.
  const existingGroups = useMemo<ExistingGroupEntry[]>(
    () => buildExistingGroupEntries(serverGroupTitles, mangaSummaries, finishedImages),
    [serverGroupTitles, mangaSummaries, finishedImages],
  );

  // Event Handlers
  /** クリップボード ペースト対応 */
  useEffect(() => {
    const handlePaste = (e: ClipboardEvent) => {
      if (activeView !== "studio") return;
      const items = e.clipboardData?.items || [];
      const pastedFiles: File[] = [];
      for (const item of items) {
        if (item.kind === "file") {
          const pastedFile = item.getAsFile();
          if (pastedFile) {
            pastedFiles.push(pastedFile);
          }
        }
      }
      if (pastedFiles.length > 0) {
        void processIncomingFiles(pastedFiles);
      }
    };

    window.addEventListener("paste", handlePaste as EventListener);
    return () =>
      window.removeEventListener("paste", handlePaste as EventListener);
  }, [activeView, processIncomingFiles]);

  const {
    handleUpdateMangaTitle,
    restoreBatchPages,
    updateFinishedImage,
    deleteFinishedImage,
    deleteFinishedImages,
    reorderMangaPages,
    deleteMangaGroup,
    deleteMangaGroups,
  } = createGalleryMutationActions({
    setFinishedImages,
    setMangaSummaries,
    setTotalMangaCount,
    setTotalGalleryCount,
    setIsGalleryLoading,
    setGalleryRevision,
    galleryPageCacheRef,
    loadMangaSummaries,
  });

  const {
    removeFile,
    removeSelectedFiles,
    toggleFileSelection,
    selectAllFiles,
    deselectAllFiles,
  } = createStudioFileSelectionActions({
    files,
    selectedFiles,
    fileStatuses,
    setFiles,
    setSelectedFiles,
    setExcludedColorFiles,
    setAutoDetectedColorFiles,
    setFileStatuses,
    lastSelectedFileRef,
    folderMapRef,
    resultUrlsRef,
  });

  const { handleSubmit, handleConfirmGroup, handleCloseGroupModal } = createTranslationSubmissionActions({
    files,
    selectedFiles,
    pendingTranslationTargets,
    excludedColorFiles,
    autoDetectedColorFiles,
    isConfirmingTranslationRef,
    setFiles,
    setSelectedFiles,
    setPendingTranslationTargets,
    setIsGroupModalOpen,
    setTranslationBatchError,
    setExcludedColorFiles,
    setAutoDetectedColorFiles,
    setFileStatuses,
    setCurrentMangaTitle,
    setTranslationBatches,
    setIsJobsOpen,
    getCurrentSettings,
    persistBatch: saveTranslationBatchToIDB,
    resumeUpload: resumeStudioTranslationUpload,
    failUpload: failStudioTranslationUpload,
  });

  const {
    handleBatchMangaTitleChange,
    updateTranslationBatchTranslator,
    updateTranslationBatchManualReview,
    updateTranslationBatchPriority,
    pauseTranslation,
    resumeTranslation,
    dismissTranslationBatch,
    removeTranslationBatch,
    removeTranslationItem,
    retryTranslationItem,
  } = useBatchActions({
    translationBatches,
    setTranslationBatches,
    batchMutationVersionRef,
    optimisticBatchTranslatorsRef,
    optimisticDismissedBatchIdsRef,
    optimisticDeletedBatchIdsRef,
    studioUploadRequestsRef,
    resumeStudioMangaUpload,
  });

  const retryFinishedImage = (image: FinishedImage, fromStage?: string) => {
    const match = image.folder
      ? translationBatches
          .flatMap((batch) => batch.items.map((item) => ({ batch, item })))
          .find(({ item }) => item.folder === image.folder)
      : undefined;
    if (!match) {
      throw new Error("This image is not linked to a retryable translation batch.");
    }
    return retryTranslationItem(match.batch.id, match.item.id, false, fromStage);
  };

  const {
    pipelineRerunState,
    setPipelineRerunState,
    handleOpenPipelineRerun,
    handleExecutePipelineRerun,
    rerenderImages,
  } = usePipelineRerunActions({ setTranslationBatches });


  const handleCloseJobs = useCallback(() => setIsJobsOpen(false), []);

  const {
    handleDismissSummaryJob,
    handleRetrySummaryJob,
    handlePauseSummaryJob,
    handleResumeSummaryJob,
    handleStopSummaryJob,
  } = useSummaryJobActions(setSummaryJobs);

  const actionableJobCount = useMemo(() => (
    translationBatches.filter((batch) => !batch.dismissed && (batch.status !== "completed" || Boolean(batch.failedCount))).length
    + summaryJobs.filter((job) => job.status !== "ready").length
  ), [summaryJobs, translationBatches]);

  const jobAttentionCount = useMemo(() => (
    translationBatches.filter((batch) => !batch.dismissed && (batch.status === "error" || Boolean(batch.failedCount))).length
    + summaryJobs.filter((job) => job.status === "error").length
  ), [summaryJobs, translationBatches]);

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-zinc-950 text-zinc-900 dark:text-zinc-100 flex flex-col transition-colors">
      <Header
        theme={theme}
        onToggleTheme={toggleTheme}
        activeView={activeView}
        gallerySection={parsedRoute.gallerySection}
        galleryCount={totalGalleryCount}
        jobCount={actionableJobCount}
        jobAttentionCount={jobAttentionCount}
        onOpenJobs={() => setIsJobsOpen(true)}
      />

      {jobToast && (
        <div className="pointer-events-none fixed inset-x-3 top-20 z-40 flex justify-center" role="status" aria-live="polite">
          <div className={`pointer-events-auto flex max-w-lg items-center gap-3 rounded-xl border px-4 py-3 text-sm shadow-lg ${jobToast.status === "ready" ? "border-indigo-200 bg-white text-zinc-800 dark:border-indigo-800 dark:bg-zinc-900 dark:text-zinc-100" : "border-rose-200 bg-white text-rose-800 dark:border-rose-900/60 dark:bg-zinc-900 dark:text-rose-200"}`}>
            <Icon icon={jobToast.status === "ready" ? "carbon:checkmark-filled" : "carbon:warning-alt"} className="h-5 w-5 shrink-0" />
            <span className="min-w-0"><strong className="font-semibold">{jobToast.title}</strong> · {jobToast.message}</span>
            <button type="button" onClick={() => setJobToast(null)} className="rounded-md p-1 text-zinc-400 hover:bg-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 dark:hover:bg-zinc-800" aria-label="Dismiss job notification"><Icon icon="carbon:close" className="h-4 w-4" /></button>
          </div>
        </div>
      )}

      <JobsDrawer
        open={isJobsOpen}
        onClose={handleCloseJobs}
        batches={translationBatches}
        summaryJobs={summaryJobs}
        onLoadBatchDetails={loadTranslationBatchDetails}
        onPause={pauseTranslation}
        onResume={resumeTranslation}
        onDismissBatch={dismissTranslationBatch}
        onRemoveBatch={removeTranslationBatch}
        onRetryItem={retryTranslationItem}
        onRemoveItem={removeTranslationItem}
        onTranslatorChange={updateTranslationBatchTranslator}
        onManualReviewChange={updateTranslationBatchManualReview}
        onPriorityChange={updateTranslationBatchPriority}
        onMangaTitleChange={handleBatchMangaTitleChange}
        onOpenLightbox={handleOpenLightbox}
        onOpenPageEdit={handleOpenPageEdit}
        isColorizerActive={colorizer !== "none" || colorizeOnly}
        onToggleExcludeColor={toggleQueueItemColor}
        onDismissSummary={handleDismissSummaryJob}
        onRetrySummary={handleRetrySummaryJob}
        onPauseSummary={handlePauseSummaryJob}
        onResumeSummary={handleResumeSummaryJob}
        onStopSummary={handleStopSummaryJob}
      />

      <main className="min-w-0 flex-1 mx-auto w-full max-w-7xl space-y-6 px-3 py-4 sm:space-y-8 sm:px-6 sm:py-8 lg:px-8">
        {/* Studio View (Default) */}
        {activeView === "studio" && (
          <StudioWorkspace
            options={{
              detectionResolution,
              textDetector,
              ocr,
              renderFont,
              renderTextDirection,
              letterCase,
              translator,
              summaryModel,
              targetLanguage,
              translationQuality,
              inpaintingSize,
              customUnclipRatio,
              customBoxThreshold,
              customOcrProb,
              maskDilationOffset,
              bubbleDetection,
              inpainter,
              colorizer,
              colorizeOnly,
              colorizationSize,
              denoiseSigma,
              colorThreshold,
              upscaler,
              upscaleRatio,
              revertUpscaling,
              rememberSettings,
              translationBatchSize,
              setDetectionResolution,
              setTextDetector,
              setOcr,
              setRenderFont,
              setRenderTextDirection,
              setLetterCase,
              setTranslator,
              setSummaryModel,
              setTargetLanguage,
              setTranslationQuality,
              setInpaintingSize,
              setCustomUnclipRatio,
              setCustomBoxThreshold,
              setCustomOcrProb,
              setMaskDilationOffset,
              setBubbleDetection,
              setInpainter,
              setColorizer,
              setColorizeOnly,
              setColorizationSize,
              setDenoiseSigma,
              setColorThreshold,
              setUpscaler,
              setUpscaleRatio,
              setRevertUpscaling,
              setRememberSettings,
              setTranslationBatchSize,
            }}
            imageHandling={{
              files,
              pendingFiles,
              fileStatuses,
              isProcessing,
              isProcessingAllFinished,
              selectedFiles,
              mangaTitle: currentMangaTitle,
              onMangaTitleChange: setCurrentMangaTitle,
              handleFileChange,
              handleDrop,
              onConfirmPendingFiles: commitPendingFiles,
              onRemovePendingFile: removePendingFile,
              onReorderPendingFiles: reorderPendingFiles,
              onClearPendingFiles: clearPendingFiles,
              onUploadManga: handleStudioMangaUpload,
              uploadMangaError: studioMangaUploadError,
              handleSubmit,
              clearForm,
              removeFile,
              onRemoveSelectedFiles: removeSelectedFiles,
              onToggleFile: toggleFileSelection,
              onSelectAll: selectAllFiles,
              onDeselectAll: deselectAllFiles,
              onOpenLightbox: handleOpenLightbox,
              excludedColorFiles,
              autoDetectedColorFiles,
              onToggleExcludeColor: toggleExcludeColorFile,
              onSetExcludeColorForSelected: setExcludeColorForSelected,
              isColorizerActive: colorizer !== "none" || colorizeOnly,
              isExtractingArchive,
              archiveError,
              onDismissArchiveError: clearArchiveError,
            }}
            translationBatchError={translationBatchError}
            isGroupModalOpen={isGroupModalOpen}
            studioMangaUploadWarning={studioMangaUploadWarning}
            totalGalleryCount={totalGalleryCount}
          />
        )}

        {/* Gallery View */}
        {activeView === "gallery" && (
          <div className="space-y-6">
            <ResultGallery
              finishedImages={finishedImages}
              mangaSummaries={mangaSummaries}
              summaryModel={summaryModel}
              totalGalleryCount={totalGalleryCount}
              totalMangaCount={totalMangaCount}
              isLoading={isGalleryLoading}
              onDeleteImage={deleteFinishedImage}
              onDeleteImages={deleteFinishedImages}
              onDeleteManga={deleteMangaGroup}
              onDeleteMangas={deleteMangaGroups}
              onReorderMangaPages={reorderMangaPages}
              onRestoreBatchPages={restoreBatchPages}
              onUpdateImage={updateFinishedImage}
              onUpdateMangaTitle={handleUpdateMangaTitle}
              onOpenPageView={handleOpenPageView}
              onOpenPageEdit={handleOpenPageEdit}
              onRetryImage={retryFinishedImage}
              onRetryFromStage={retryFinishedImage}
              onRerenderImage={(image) => rerenderImages([image])}
              onRerenderImages={rerenderImages}
              galleryRevision={galleryRevision}
              onOpenReader={handleOpenReader}
              onCloseOverlay={handleCloseOverlay}
              initialPageViewFolder={parsedRoute.overlay === "viewer" ? parsedRoute.folder : null}
              initialPageEditFolder={parsedRoute.overlay === "editor" ? parsedRoute.folder : null}
              initialReaderMangaId={parsedRoute.overlay === "reader" ? (parsedRoute.mangaId ?? null) : null}
              initialReaderManga={parsedRoute.overlay === "reader" ? parsedRoute.mangaTitle : null}
              galleryPage={parsedRoute.overlay === "none" ? parsedRoute.galleryPage : undefined}
              galleryPageSize={parsedRoute.overlay === "none" ? parsedRoute.galleryPageSize : undefined}
              gallerySearch={parsedRoute.overlay === "none" ? parsedRoute.gallerySearch : undefined}
              gallerySort={parsedRoute.overlay === "none" ? parsedRoute.gallerySort : undefined}
              galleryStatus={parsedRoute.overlay === "none" ? parsedRoute.galleryStatus : undefined}
              reviewOnly={parsedRoute.overlay === "none" ? parsedRoute.reviewOnly : false}
              onGalleryPageChange={handleGalleryPageChange}
              onGalleryPageSizeChange={handleGalleryPageSizeChange}
              onGallerySearchChange={handleGallerySearchChange}
              onGallerySortChange={handleGallerySortChange}
              onGalleryStatusChange={handleGalleryStatusChange}
              onGalleryReviewChange={handleGalleryReviewChange}
              selectedMangaId={parsedRoute.overlay === "none" ? (parsedRoute.mangaId ?? null) : undefined}
              selectedMangaTitle={parsedRoute.overlay === "none" ? (parsedRoute.mangaTitle ?? null) : undefined}
              onOpenMangaDetail={handleOpenMangaDetail}
              onCloseMangaDetail={handleCloseMangaDetail}
              gallerySection={parsedRoute.gallerySection}
              initialSeriesId={parsedRoute.seriesId ?? null}
              onOpenSeriesDetail={handleOpenSeriesDetail}
              onCloseSeriesDetail={handleCloseSeriesDetail}
              onSeriesChanged={() => loadMangaSummaries()}
            />
          </div>
        )}

        {/* Diagnostic workspace */}
        {activeView === "search" && <React.Suspense fallback={<p role="status">Loading Search Lab…</p>}><SearchLab /></React.Suspense>}
      </main>

      {/* Fast submissions keep the original one-step group dialog. */}
      <GroupSelectionModal
        isOpen={isGroupModalOpen && translationQuality !== "professional"}
        onClose={handleCloseGroupModal}
        onConfirm={handleConfirmGroup}
        initialGroupName={currentMangaTitle}
        existingGroups={existingGroups}
        pageCount={pendingTranslationTargets.length}
        title="Assign to Manga Group"
        subtitle="This batch will be added to Translating in order"
        icon="carbon:translate"
        errorMessage={translationBatchError}
        isLoadingGroups={isLoadingServerGroupTitles}
        isSubmitting={isCheckingGroupTitle}
      />

      <TranslationSubmitModal
        isOpen={isGroupModalOpen && translationQuality === "professional"}
        onClose={handleCloseGroupModal}
        onConfirm={handleConfirmGroup}
        files={pendingTranslationTargets}
        initialGroupName={currentMangaTitle}
        existingGroups={existingGroups}
        errorMessage={translationBatchError}
        isLoadingGroups={isLoadingServerGroupTitles}
        isSubmitting={isCheckingGroupTitle}
      />

      <GroupSelectionModal
        isOpen={isStudioMangaUploadModalOpen}
        onClose={closeStudioMangaUploadModal}
        onConfirm={handleStudioMangaUploadConfirm}
        initialGroupName={currentMangaTitle.trim()}
        existingGroups={existingGroups}
        pageCount={pendingStudioMangaFiles.length}
        title="Upload Manga"
        subtitle="Original pages are added without translation and can upload while other workers are running"
        actionLabel="Upload"
        icon="carbon:book"
        allowUngrouped={false}
        errorMessage={studioMangaUploadError}
        isLoadingGroups={isLoadingServerGroupTitles}
        isSubmitting={isCheckingGroupTitle}
      />

      {selectedImageForModal && (
        <PageDetailModal
          image={selectedImageForModal}
          onClose={closeStudioViewer}
          images={selectedModalImages}
          currentIndex={selectedModalIndex >= 0 ? selectedModalIndex : undefined}
          onNavigate={(index) => {
            const nextImage = selectedModalImages[index];
            if (!nextImage) return;
            setSelectedImageForModal(nextImage);
            setSelectedImageRetry(null);
            setSelectedModalIndex(index);
          }}
          onRetry={selectedImageRetry ?? (selectedImageForModal.folder ? retryFinishedImage : undefined)}
          onRetryFromStage={selectedImageForModal.folder ? retryFinishedImage : undefined}
          onRerender={selectedImageForModal.folder ? (image) => handleOpenPipelineRerun([image], selectedImageForModal.mangaTitle) : undefined}
          titlePrefix="Studio preview"
        />
      )}

      {pipelineRerunState && (
        <PipelineRerunDialog
          images={pipelineRerunState.images}
          mangaTitle={pipelineRerunState.mangaTitle}
          onClose={() => setPipelineRerunState(null)}
          onSubmit={handleExecutePipelineRerun}
        />
      )}
    </div>
  );
};


export default App;
