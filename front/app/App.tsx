import React, { startTransition, useState, useEffect, useLayoutEffect, useMemo, useRef, useCallback } from "react";
import { Icon } from "@iconify/react";
import {
  processingStatuses,
  type TranslatorKey,
  type FileStatus,
  type QueuedImage,
  type TranslationSettings,
  type FinishedImage,
  type MangaGroupSummary,
  type MangaGroupSelection,
  type TranslationBatch,
  type SummaryJob,
  type StudioFile,
  type PendingStudioFile,
  type StoryPlan,
} from "@/types";
import { imageMimeTypes, ocrOptions, summaryModelOptions } from "@/config";
import { OptionsPanel } from "@/components/OptionsPanel";
import { ImageHandlingArea } from "@/components/ImageHandlingArea";
import { canChangeBatchTranslator } from "@/components/TranslatingSection";
import { JobsDrawer } from "@/components/JobsDrawer";
import { ResultGallery } from "@/components/ResultGallery";
import { Header } from "@/components/Header";
import { GroupSelectionModal, type ExistingGroupEntry, type ExistingGroupItem } from "@/components/GroupSelectionModal";
import { TranslationSubmitModal } from "@/components/TranslationSubmitModal";
import { PipelineLab } from "@/components/PipelineLab";
const SearchLab = React.lazy(() => import("@/components/SearchLab"));
import { PageDetailModal } from "@/components/PageDetailModal";
import PreviewImage from "@/components/PreviewImage";
import { useLocation, useNavigate } from "react-router";
import {
  parseAppPath,
  getLegacyRedirect,
  getNavigationOrigin,
  validatePriorRoute,
  buildPageViewUrl,
  buildPageEditUrl,
  buildReaderIdUrl,
  buildMangaDetailIdUrl,
  buildSeriesDetailUrl,
  buildGalleryPageUrl,
  DEFAULT_GALLERY_PAGE_SIZE,
  type GallerySort,
  type MangaStatusFilter,
} from "@/utils/routeState";

const useClientLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;
import { renameBatchQueuedOnly } from "@/utils/batchRename";
import {
  clearSettings,
  loadRememberSettings,
  loadSettings,
  saveRememberSettings,
  saveSettings,
  loadRecentGroups,
  saveRecentGroup,
} from "@/utils/localStorage";
import {
  saveStudioStateToIDB,
  loadStudioStateFromIDB,
  clearFilesFromIDB,
  loadTranslationBatchesFromIDB,
  loadTranslationBatchFromIDB,
  saveTranslationBatchToIDB,
  removeTranslationBatchFromIDB,
  loadQueueFromIDB,
  clearQueueFromIDB,
} from "@/utils/fileStorage";
import { detectIsImageColored } from "@/utils/colorDetector";
import { apiUrl } from "@/utils/api";
import {
  isArchiveFile,
  extractMangaTitleFromFilename,
  extractArchiveImages,
  naturalCompare,
} from "@/utils/zipUtils";
import {
  type ServerBatch,
  batchAction,
  fetchServerBatch,
  fetchServerBatches,
  getBatchKind,
  mergeServerBatches,
  subscribeServerBatches,
  removeBatchItem,
  removeServerBatch,
  rerenderPages,
  rerunPipeline,
  retryBatchItem,
  submitServerBatch,
  toTranslationBatch,
  updateBatchItem,
  updateBatchManualReview,
  updateBatchPriority,
  updateBatchTitle,
  updateBatchTranslator,
} from "@/utils/serverBatches";
import { PipelineRerunDialog } from "@/components/PipelineRerunDialog";

import {
  dismissSummaryJob,
  fetchSummaryJobs,
  pauseSummaryJob,
  resumeSummaryJob,
  retrySummaryJob,
  stopSummaryJob,
} from "@/utils/summaryJobs";
import {
  computeRangeSelection,
  clearBrowserTextSelection,
  type SelectionAnchor,
} from "@/utils/selectionUtils";
import { resultFolderFromUrl } from "@/utils/resultPaths";

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

const activeBatchStatuses = new Set<TranslationBatch["status"]>([
  "uploading",
  "waiting",
  "processing",
  "stopping",
  "paused",
]);

// Manga Translation Studio Application Main Component
export const App: React.FC = () => {
  // Theme & Navigation state
  const [theme, setTheme] = useState<"light" | "dark">("dark");
  const location = useLocation();
  const navigate = useNavigate();

  // Redirect legacy /?view=... or root /
  useEffect(() => {
    const redirectTarget = getLegacyRedirect(location.pathname, location.search);
    if (redirectTarget) {
      navigate(redirectTarget, { replace: true });
    }
  }, [location.pathname, location.search, navigate]);

  const parsedRoute = useMemo(
    () => parseAppPath(location.pathname, location.search),
    [location.pathname, location.search]
  );
  const activeView = parsedRoute.view;

  const handleSelectView = useCallback(
    (view: "studio" | "gallery") => {
      navigate(`/${view}`);
    },
    [navigate]
  );

  const handleBatchMangaTitleChange = useCallback(
    (batchId: string, title: string) => {
      setTranslationBatches((prev) =>
        prev.map((batch) => (batch.id === batchId ? renameBatchQueuedOnly(batch, title) : batch))
      );
      void updateBatchTitle(batchId, title).catch((error) =>
        console.warn("Failed to update batch title:", error)
      );
    },
    []
  );

  const handleOpenPageView = useCallback(
    (folder: string) => {
      navigate(buildPageViewUrl(folder), {
        state: { from: getNavigationOrigin(location.pathname, location.search, location.state?.from) },
      });
    },
    [location.pathname, location.search, location.state, navigate]
  );

  const handleOpenPageEdit = useCallback(
    (folder: string) => {
      navigate(buildPageEditUrl(folder), {
        state: { from: getNavigationOrigin(location.pathname, location.search, location.state?.from) },
      });
    },
    [location.pathname, location.search, location.state, navigate]
  );

  const handleOpenReader = useCallback(
    (mangaId: string, _initialPageIndex?: number, replace = false) => {
      navigate(buildReaderIdUrl(mangaId), {
        replace,
        state: {
          from: getNavigationOrigin(location.pathname, location.search, location.state?.from),
        },
      });
    },
    [location.pathname, location.search, location.state, navigate]
  );

  const handleOpenMangaDetail = useCallback(
    (mangaId: string, reviewOnly = false) => {
      navigate(buildMangaDetailIdUrl(mangaId, reviewOnly), {
        state: { from: getNavigationOrigin(location.pathname, location.search, location.state?.from) },
      });
    },
    [location.pathname, location.search, location.state, navigate]
  );

  const handleGalleryPageChange = useCallback(
    (page: number) => {
      navigate(buildGalleryPageUrl(
        page,
        parsedRoute.galleryPageSize,
        parsedRoute.gallerySearch,
        parsedRoute.gallerySection,
        parsedRoute.gallerySort,
        parsedRoute.reviewOnly,
        parsedRoute.galleryStatus,
      ));
    },
    [navigate, parsedRoute.galleryPageSize, parsedRoute.gallerySearch, parsedRoute.gallerySection, parsedRoute.gallerySort, parsedRoute.reviewOnly, parsedRoute.galleryStatus]
  );

  const handleGalleryPageSizeChange = useCallback(
    (pageSize: number) => {
      navigate(buildGalleryPageUrl(
        1,
        pageSize,
        parsedRoute.gallerySearch,
        parsedRoute.gallerySection,
        parsedRoute.gallerySort,
        parsedRoute.reviewOnly,
        parsedRoute.galleryStatus,
      ));
    },
    [navigate, parsedRoute.gallerySearch, parsedRoute.gallerySection, parsedRoute.gallerySort, parsedRoute.reviewOnly, parsedRoute.galleryStatus]
  );

  const handleGallerySearchChange = useCallback(
    (search: string) => {
      navigate(buildGalleryPageUrl(
        1,
        parsedRoute.galleryPageSize,
        search,
        parsedRoute.gallerySection,
        parsedRoute.gallerySort,
        parsedRoute.reviewOnly,
        parsedRoute.galleryStatus,
      ), { replace: true });
    },
    [navigate, parsedRoute.galleryPageSize, parsedRoute.gallerySection, parsedRoute.gallerySort, parsedRoute.reviewOnly, parsedRoute.galleryStatus]
  );

  const handleGallerySortChange = useCallback(
    (sort: GallerySort) => {
      navigate(buildGalleryPageUrl(
        1,
        parsedRoute.galleryPageSize,
        parsedRoute.gallerySearch,
        parsedRoute.gallerySection,
        sort,
        parsedRoute.reviewOnly,
        parsedRoute.galleryStatus,
      ));
    },
    [navigate, parsedRoute.galleryPageSize, parsedRoute.gallerySearch, parsedRoute.gallerySection, parsedRoute.reviewOnly, parsedRoute.galleryStatus]
  );

  const handleGalleryReviewChange = useCallback((pending: boolean) => {
    navigate(buildGalleryPageUrl(
      1,
      parsedRoute.galleryPageSize,
      parsedRoute.gallerySearch,
      parsedRoute.gallerySection,
      parsedRoute.gallerySort,
      pending,
      pending ? 'review' : 'all',
    ));
  }, [navigate, parsedRoute.galleryPageSize, parsedRoute.gallerySearch, parsedRoute.gallerySection, parsedRoute.gallerySort]);

  const handleGalleryStatusChange = useCallback((status: MangaStatusFilter) => {
    navigate(buildGalleryPageUrl(
      1,
      parsedRoute.galleryPageSize,
      parsedRoute.gallerySearch,
      parsedRoute.gallerySection,
      parsedRoute.gallerySort,
      status === 'review',
      status,
    ));
  }, [navigate, parsedRoute.galleryPageSize, parsedRoute.gallerySearch, parsedRoute.gallerySection, parsedRoute.gallerySort]);

  const handleCloseMangaDetail = useCallback(() => {
    navigate(validatePriorRoute(location.state?.from));
  }, [location.state, navigate]);

  const handleCloseOverlay = useCallback(() => {
    const priorRoute = validatePriorRoute(location.state?.from);
    navigate(priorRoute);
  }, [location.state, navigate]);

  const handleOpenSeriesDetail = useCallback((seriesId: string) => {
    navigate(buildSeriesDetailUrl(seriesId), {
      state: { from: getNavigationOrigin(location.pathname, location.search, location.state?.from) },
    });
  }, [location.pathname, location.search, location.state, navigate]);

  const handleCloseSeriesDetail = useCallback(() => {
    navigate(validatePriorRoute(location.state?.from, "/gallery?view=series"));
  }, [location.state, navigate]);

  const [selectedImageForModal, setSelectedImageForModal] = useState<FinishedImage | null>(null);
  const [selectedImageRetry, setSelectedImageRetry] = useState<(() => void | Promise<void>) | null>(null);

  // State Hooks
  const [fileStatuses, setFileStatuses] = useState<Map<string, FileStatus>>(
    new Map()
  );
  const [files, setFiles] = useState<StudioFile[]>([]);

  // New state for improved UI features
  const [translationBatches, setTranslationBatches] = useState<TranslationBatch[]>([]);
  const batchDetailRequestsRef = useRef(new Map<string, Promise<void>>());
  const batchDetailQueuedRef = useRef(new Set<string>());
  const studioUploadRequestsRef = useRef(new Map<string, Promise<void>>());
  const batchMutationVersionRef = useRef(0);
  const optimisticBatchTranslatorsRef = useRef(new Map<string, TranslatorKey>());
  const optimisticDismissedBatchIdsRef = useRef(new Set<string>());
  const optimisticDeletedBatchIdsRef = useRef(new Set<string>());
  const initialBatchLoadRef = useRef<Promise<void> | null>(null);

  const loadTranslationBatchDetails = React.useCallback((batchId: string) => {
    if (batchDetailRequestsRef.current.has(batchId)) {
      batchDetailQueuedRef.current.add(batchId);
      return batchDetailRequestsRef.current.get(batchId)!;
    }

    const request = fetchServerBatch(batchId)
      .then((serverBatch) => {
        const optimisticTranslator = optimisticBatchTranslatorsRef.current.get(batchId);
        if (optimisticTranslator === serverBatch.settings.translator) {
          optimisticBatchTranslatorsRef.current.delete(batchId);
        }
        const detailed = toTranslationBatch(serverBatch);
        const withOptimisticTranslator = optimisticTranslator && optimisticTranslator !== serverBatch.settings.translator
          ? { ...detailed, settings: { ...detailed.settings, translator: optimisticTranslator } }
          : detailed;
        setTranslationBatches((prev) => prev.map((batch) =>
          batch.id === batchId ? withOptimisticTranslator : batch
        ));
      })
      .finally(() => {
        batchDetailRequestsRef.current.delete(batchId);
        if (batchDetailQueuedRef.current.has(batchId)) {
          batchDetailQueuedRef.current.delete(batchId);
          void loadTranslationBatchDetails(batchId);
        }
      });
    batchDetailRequestsRef.current.set(batchId, request);
    return request;
  }, []);
  const [finishedImages, setFinishedImages] = useState<FinishedImage[]>([]);
  const [mangaSummaries, setMangaSummaries] = useState<MangaGroupSummary[]>([]);
  const [serverGroupTitles, setServerGroupTitles] = useState<string[]>([]);
  const [isLoadingServerGroupTitles, setIsLoadingServerGroupTitles] = useState(false);
  const [totalMangaCount, setTotalMangaCount] = useState<number>(0);
  const [totalGalleryCount, setTotalGalleryCount] = useState<number>(0);
  const [isGalleryLoading, setIsGalleryLoading] = useState<boolean>(true);
  const [galleryRevision, setGalleryRevision] = useState(0);
  const galleryLoadRequestRef = useRef(0);
  const galleryPageCacheRef = useRef(new Map<string, {
    groups: MangaGroupSummary[];
    totalGroups: number;
    totalImages: number;
  }>());
  const serverGroupTitlesRequestRef = useRef<Promise<string[]> | null>(null);
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
    let disposed = false;
    const refresh = async () => {
      try {
        const jobs = await fetchSummaryJobs();
        if (disposed) return;
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
      } catch {
        // Keep the last durable job list visible while the server is unavailable.
      }
    };
    void refresh();
    const interval = window.setInterval(refresh, 2000);
    return () => {
      disposed = true;
      window.clearInterval(interval);
    };
  }, []);

  const effectiveMangaFilter = parsedRoute.overlay === "none"
    ? (parsedRoute.mangaId || parsedRoute.mangaTitle)
    : undefined;

  const loadMangaSummaries = useCallback(async (signal?: AbortSignal) => {
    if (parsedRoute.overlay !== "none") return;
    const requestId = ++galleryLoadRequestRef.current;
    const page = parsedRoute.galleryPage ?? 1;
    const pageSize = parsedRoute.galleryPageSize ?? DEFAULT_GALLERY_PAGE_SIZE;
    const offset = (page - 1) * pageSize;
    const mangaFilter = effectiveMangaFilter;
    const mangaQuery = mangaFilter ? `&mangaId=${encodeURIComponent(mangaFilter)}` : "";
    const searchQuery = parsedRoute.gallerySearch
      ? `&search=${encodeURIComponent(parsedRoute.gallerySearch)}`
      : "";
    const sortQuery = parsedRoute.gallerySort && parsedRoute.gallerySort !== "alpha-asc"
      ? `&sort=${encodeURIComponent(parsedRoute.gallerySort)}`
      : "";
    const statusParam = (parsedRoute.galleryStatus && parsedRoute.galleryStatus !== 'all')
      ? `&status=${encodeURIComponent(parsedRoute.galleryStatus)}`
      : (parsedRoute.reviewOnly ? "&review=pending" : "");
    const cacheKey = `${page}:${pageSize}:${parsedRoute.gallerySearch || ""}:${parsedRoute.gallerySort || "date-desc"}:${parsedRoute.galleryStatus || (parsedRoute.reviewOnly ? "review" : "all")}`;
    const cached = mangaFilter ? undefined : galleryPageCacheRef.current.get(cacheKey);
    if (cached) {
      setMangaSummaries(cached.groups);
      setTotalMangaCount(cached.totalGroups);
      setTotalGalleryCount(cached.totalImages);
    } else if (!mangaFilter) {
      setIsGalleryLoading(true);
      setMangaSummaries([]);
      setTotalMangaCount(0);
      setTotalGalleryCount(0);
    } else {
      setIsGalleryLoading(true);
    }
    try {
      const response = await fetch(
        apiUrl(`/api/results/groups?limit=${pageSize}&offset=${offset}${mangaQuery}${searchQuery}${sortQuery}${statusParam}`),
        { signal },
      );
      if (response.ok) {
        const data = await response.json();
        if (requestId !== galleryLoadRequestRef.current) return;
        if (Array.isArray(data.groups)) {
          const totalGroups = typeof data.totalGroups === "number" ? data.totalGroups : data.groups.length;
          const totalImages = typeof data.totalImages === "number"
            ? data.totalImages
            : data.groups.reduce((acc: number, g: any) => acc + (g.count || 0), 0);
          setMangaSummaries(data.groups);
          setTotalMangaCount(totalGroups);
          setTotalGalleryCount(totalImages);
          if (!mangaFilter) {
            galleryPageCacheRef.current.set(cacheKey, { groups: data.groups, totalGroups, totalImages });
          }
        }
      }
    } catch (error) {
      if (!signal?.aborted && requestId === galleryLoadRequestRef.current) {
        console.warn("Failed to load manga groups from server:", error);
      }
    } finally {
      if (!signal?.aborted && requestId === galleryLoadRequestRef.current) {
        setIsGalleryLoading(false);
      }
    }
  }, [
    effectiveMangaFilter,
    parsedRoute.galleryPage,
    parsedRoute.galleryPageSize,
    parsedRoute.gallerySearch,
    parsedRoute.gallerySort,
    parsedRoute.galleryStatus,
    parsedRoute.overlay,
    parsedRoute.reviewOnly,
  ]);

  const loadAllServerGroupTitles = useCallback(async (): Promise<string[]> => {
    const pending = serverGroupTitlesRequestRef.current;
    if (pending) return pending;

    if (serverGroupTitles.length === 0) {
      setIsLoadingServerGroupTitles(true);
    }
    const request = (async () => {
      const titles = new Set<string>();
      let offset = 0;
      while (true) {
        const response = await fetch(apiUrl(`/api/results/groups?limit=500&offset=${offset}`));
        if (!response.ok) throw new Error(`Could not load manga groups (${response.status})`);
        const data = await response.json();
        if (!Array.isArray(data.groups)) break;
        data.groups.forEach((group: { title?: unknown }) => {
          if (typeof group.title === "string" && group.title.trim() && group.title.trim() !== "Ungrouped") {
            titles.add(group.title.trim());
          }
        });
        if (data.nextOffset === null || data.nextOffset === undefined) break;
        const nextOffset = Number(data.nextOffset);
        if (!Number.isFinite(nextOffset) || nextOffset <= offset) break;
        offset = nextOffset;
      }
      const result = Array.from(titles);
      setServerGroupTitles(result);
      return result;
    })().catch((error) => {
      console.warn("Failed to load all manga group titles:", error);
      return [];
    });
    serverGroupTitlesRequestRef.current = request;
    void request.then(() => {
      setIsLoadingServerGroupTitles(false);
      if (serverGroupTitlesRequestRef.current === request) {
        serverGroupTitlesRequestRef.current = null;
      }
    });
    return request;
  }, [serverGroupTitles.length]);

  // Preload manga group titles on initial load
  useEffect(() => {
    void loadAllServerGroupTitles();
  }, [loadAllServerGroupTitles]);

  // Group selection prompt modal state
  const [isGroupModalOpen, setIsGroupModalOpen] = useState(false);
  const [pendingTranslationTargets, setPendingTranslationTargets] = useState<StudioFile[]>([]);
  const [pendingFiles, setPendingFiles] = useState<PendingStudioFile[]>([]);
  const [pipelineLabInitialFile, setPipelineLabInitialFile] = useState<File | null>(null);
  const [recentGroups, setRecentGroups] = useState<string[]>([]);
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
  const [isExtractingArchive, setIsExtractingArchive] = useState<boolean>(false);
  const [archiveError, setArchiveError] = useState<string | null>(null);

  const toggleExcludeColorFile = (fileName: string) => {
    setExcludedColorFiles((prev) => {
      const next = new Set(prev);
      if (next.has(fileName)) {
        next.delete(fileName);
      } else {
        next.add(fileName);
      }
      return next;
    });
  };

  const setExcludeColorForSelected = (exclude: boolean) => {
    setExcludedColorFiles((prev) => {
      const next = new Set(prev);
      selectedFiles.forEach((fileName) => {
        if (exclude) {
          next.add(fileName);
        } else {
          next.delete(fileName);
        }
      });
      return next;
    });
  };

  const toggleQueueItemColor = (batchId: string, itemId: string) => {
    const item = translationBatches
      .find((batch) => batch.id === batchId)
      ?.items.find((entry) => entry.id === itemId);
    if (!item) return;
    const excludeColor = !item.excludeColor;
    setTranslationBatches((prev) =>
      prev.map((batch) =>
        batch.id !== batchId
          ? batch
          : {
              ...batch,
              items: batch.items.map((item) =>
                item.id === itemId ? { ...item, excludeColor } : item
              ),
          }
      )
    );
    void updateBatchItem(batchId, itemId, excludeColor).catch((error) =>
      console.warn("Failed to update batch item:", error)
    );
  };

  const checkColorForFiles = useCallback(async (newFiles: StudioFile[]) => {
    const coloredNames: string[] = [];
    const concurrency = 4;
    for (let i = 0; i < newFiles.length; i += concurrency) {
      const chunk = newFiles.slice(i, i + concurrency);
      const results = await Promise.all(
        chunk.map(async (entry) => {
          try {
            const isColored = await detectIsImageColored(entry.file);
            return isColored ? entry.id : null;
          } catch {
            return null;
          }
        })
      );
      results.forEach((name) => {
        if (name) coloredNames.push(name);
      });
    }
    if (coloredNames.length > 0) {
      setAutoDetectedColorFiles((prev) => {
        const next = new Set(prev);
        coloredNames.forEach((name) => next.add(name));
        return next;
      });
      setExcludedColorFiles((prev) => {
        const next = new Set(prev);
        coloredNames.forEach((name) => next.add(name));
        return next;
      });
    }
  }, []);

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


  // Translation Options State Hooks
  const [detectionResolution, setDetectionResolution] = useState("2048");
  const [textDetector, setTextDetector] = useState("default");
  const [ocr, setOcr] = useState("48px");
  const [renderFont, setRenderFont] = useState("wildwords");
  const [renderTextDirection, setRenderTextDirection] = useState("auto");
  const [letterCase, setLetterCase] = useState<"none" | "uppercase" | "lowercase">("none");
  const [translator, setTranslator] = useState<TranslatorKey>("deepseek");
  const [summaryModel, setSummaryModel] = useState("deepseek-flash");
  const [targetLanguage, setTargetLanguage] = useState("ENG");
  const [translationQuality, setTranslationQuality] = useState<"fast" | "professional">("fast");

  const [inpaintingSize, setInpaintingSize] = useState("2048");
  const [customUnclipRatio, setCustomUnclipRatio] = useState<number>(2.3);
  const [customBoxThreshold, setCustomBoxThreshold] = useState<number>(0.5);
  const [customOcrProb, setCustomOcrProb] = useState<number | undefined>(undefined);
  const [maskDilationOffset, setMaskDilationOffset] = useState<number>(20);
  const [bubbleDetection, setBubbleDetection] = useState(true);
  const [bubbleModel, setBubbleModel] = useState("yolov8m");
  const [inpainter, setInpainter] = useState("default");
  const [colorizer, setColorizer] = useState("none");
  const [colorizeOnly, setColorizeOnly] = useState(false);
  const [colorizationSize, setColorizationSize] = useState("576");
  const [denoiseSigma, setDenoiseSigma] = useState<number>(25);
  const [colorThreshold, setColorThreshold] = useState<number>(31);
  const [upscaler, setUpscaler] = useState("esrgan");
  const [upscaleRatio, setUpscaleRatio] = useState("");
  const [revertUpscaling, setRevertUpscaling] = useState(true);
  const [rememberSettings, setRememberSettings] = useState(true);
  const [translationBatchSize, setTranslationBatchSize] = useState(20);
  const [settingsHydrated, setSettingsHydrated] = useState(false);

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

  const getCurrentSettings = (): TranslationSettings => ({
    detectionResolution,
    textDetector,
    ocr,
    renderFont,
    renderTextDirection,
    letterCase,
    uppercase: letterCase === "uppercase",
    lowercase: letterCase === "lowercase",
    translator,
    targetLanguage,
    translationQuality,
    inpaintingSize,
    customUnclipRatio,
    customBoxThreshold,
    customOcrProb,
    ocrMinConfidence: customOcrProb,
    maskDilationOffset,
    bubbleDetection,
    bubbleModel,
    inpainter,
    colorizer,
    colorizeOnly,
    colorizationSize,
    denoiseSigma,
    colorThreshold,
    upscaler,
    upscaleRatio: upscaleRatio ? Number(upscaleRatio) : null,
    revertUpscaling: Boolean(upscaleRatio) && revertUpscaling,
    translationBatchSize,
  });

  // Effects
  /** Load saved settings, fetch finished images from server, and restore persisted files from IDB */
  useEffect(() => {
    setRecentGroups(loadRecentGroups());
    const savedSettings = loadSettings();
    const shouldRememberSettings = savedSettings.rememberSettings ?? loadRememberSettings();
    setRememberSettings(shouldRememberSettings);
    if (shouldRememberSettings && savedSettings.detectionResolution) setDetectionResolution(savedSettings.detectionResolution);
    if (shouldRememberSettings && savedSettings.textDetector) setTextDetector(savedSettings.textDetector);
    if (shouldRememberSettings && savedSettings.ocr && ocrOptions.some((option) => option.value === savedSettings.ocr)) setOcr(savedSettings.ocr);
    if (shouldRememberSettings && savedSettings.renderFont) setRenderFont(savedSettings.renderFont);
    if (shouldRememberSettings && savedSettings.renderTextDirection) setRenderTextDirection(savedSettings.renderTextDirection);
    if (shouldRememberSettings && savedSettings.letterCase) {
      setLetterCase(savedSettings.letterCase);
    } else if (shouldRememberSettings && savedSettings.uppercase) {
      setLetterCase("uppercase");
    } else if (shouldRememberSettings && savedSettings.lowercase) {
      setLetterCase("lowercase");
    }
    if (shouldRememberSettings && savedSettings.translator) {
      const savedTranslator = savedSettings.translator;
      if (
        (savedTranslator === "youdao" && !savedSettings.migratedDefaultTranslator) ||
        ((savedTranslator as string) === "offline" && !savedSettings.migratedDefaultSugoi) ||
        ((savedTranslator as string) === "qwen2" && !savedSettings.migratedDefaultSugoi) ||
        (savedTranslator === "sugoi" && !savedSettings.migratedDefaultGemini)
      ) {
        setTranslator("deepseek");
      } else {
        setTranslator(savedTranslator);
      }
    }
    if (shouldRememberSettings && savedSettings.summaryModel && summaryModelOptions.some((option) => option.value === savedSettings.summaryModel)) {
      setSummaryModel(savedSettings.summaryModel);
    }
    if (shouldRememberSettings && savedSettings.targetLanguage) {
      if (savedSettings.targetLanguage === "CHS" && !savedSettings.migratedDefaultTargetLang) {
        setTargetLanguage("ENG");
      } else {
        setTargetLanguage(savedSettings.targetLanguage);
      }
    }
    if (shouldRememberSettings && savedSettings.translationQuality) setTranslationQuality(savedSettings.translationQuality);
    if (shouldRememberSettings && savedSettings.inpaintingSize) setInpaintingSize(savedSettings.inpaintingSize);
    if (shouldRememberSettings && savedSettings.customUnclipRatio !== undefined) setCustomUnclipRatio(savedSettings.customUnclipRatio);
    if (shouldRememberSettings && savedSettings.customBoxThreshold !== undefined) setCustomBoxThreshold(savedSettings.customBoxThreshold);
    if (shouldRememberSettings && savedSettings.customOcrProb !== undefined) {
      setCustomOcrProb(savedSettings.customOcrProb);
    } else if (shouldRememberSettings && savedSettings.ocrMinConfidence !== undefined) {
      setCustomOcrProb(savedSettings.ocrMinConfidence);
    }
    if (shouldRememberSettings && savedSettings.maskDilationOffset !== undefined) setMaskDilationOffset(savedSettings.maskDilationOffset);
    if (shouldRememberSettings && savedSettings.bubbleDetection !== undefined) {
      if (!savedSettings.migratedDefaultBubbleDetection) {
        setBubbleDetection(true);
      } else {
        setBubbleDetection(savedSettings.bubbleDetection);
      }
    }
    if (shouldRememberSettings && savedSettings.inpainter) setInpainter(savedSettings.inpainter);
    if (shouldRememberSettings && savedSettings.colorizer) setColorizer(savedSettings.colorizer);
    if (shouldRememberSettings && savedSettings.colorizeOnly !== undefined) setColorizeOnly(savedSettings.colorizeOnly);
    if (shouldRememberSettings && savedSettings.colorizationSize) setColorizationSize(savedSettings.colorizationSize);
    if (shouldRememberSettings && savedSettings.denoiseSigma !== undefined) setDenoiseSigma(savedSettings.denoiseSigma);
    if (shouldRememberSettings && savedSettings.colorThreshold !== undefined) setColorThreshold(savedSettings.colorThreshold);
    if (shouldRememberSettings && savedSettings.upscaler) setUpscaler(savedSettings.upscaler);
    if (shouldRememberSettings && savedSettings.upscaleRatio !== undefined) {
      setUpscaleRatio(savedSettings.upscaleRatio == null ? "" : String(savedSettings.upscaleRatio));
    }
    if (shouldRememberSettings && savedSettings.revertUpscaling !== undefined) {
      setRevertUpscaling(savedSettings.revertUpscaling);
    }
    if (shouldRememberSettings && savedSettings.translationBatchSize !== undefined) {
      setTranslationBatchSize(Math.min(100, Math.max(1, savedSettings.translationBatchSize)));
    }
    setSettingsHydrated(true);

    // Restore studio state persisted across refreshes
    loadStudioStateFromIDB()
      .then((restored) => {
        const restoredFiles = restored.studioFiles || restored.files.map((file, index) => ({
          id: file.name,
          file,
          sourcePath: file.name,
          addedAt: index,
          dropOrder: index,
        }));
        if (restoredFiles.length > 0) {
          studioDropOrderRef.current = Math.max(
            studioDropOrderRef.current,
            ...restoredFiles.map((entry) => entry.dropOrder),
          );
          setFiles((current) => {
            const merged = new Map(current.map((entry) => [entry.id, entry]));
            restoredFiles.forEach((entry) => merged.set(entry.id, entry));
            return Array.from(merged.values()).sort((a, b) => a.dropOrder - b.dropOrder);
          });
          setFileStatuses((current) => new Map([...restored.fileStatuses, ...current]));
          setSelectedFiles((current) => new Set([...restored.selectedFiles, ...current]));
          setExcludedColorFiles((current) => new Set([...restored.excludedColorFiles, ...current]));
          setAutoDetectedColorFiles((current) => new Set([...restored.autoDetectedColorFiles, ...current]));
          restored.folderMap.forEach((v, k) => folderMapRef.current.set(k, v));
          restored.resultUrls.forEach((k) => resultUrlsRef.current.add(k));
        }
        setIsStudioHydrated(true);
      })
      .catch((err) => {
        console.warn("Failed to restore studio state from IDB:", err);
        setIsStudioHydrated(true);
      });

    // Server is authoritative for translation batches; only keep active uploads in IDB.
    const restoreServerBatches = async () => {
      try {
        const storedBatchesPromise = loadTranslationBatchesFromIDB();
        const remote = await fetchServerBatches();
        translationBatchSnapshotRef.current = JSON.stringify(remote);
        setTranslationBatches(remote.map(toTranslationBatch));

        const storedBatches = await storedBatchesPromise;
        const uploadingBatches = storedBatches.filter((batch) => batch.status === "uploading");

        // Clean up legacy non-uploading records from IDB so they do not resurrect old/dismissed batches
        await clearQueueFromIDB().catch(() => {});
        for (const upload of uploadingBatches) {
          await saveTranslationBatchToIDB(upload).catch(() => {});
        }

        const activeRemote = remote.filter((batch) => !optimisticDeletedBatchIdsRef.current.has(batch.id));
        translationBatchSnapshotRef.current = JSON.stringify(activeRemote);
        setTranslationBatches([
          ...uploadingBatches,
          ...activeRemote.map(toTranslationBatch),
        ]);
        for (const batch of uploadingBatches) {
          const detailed = batch.items.some((item) => item.file.size > 0)
            ? batch
            : await loadTranslationBatchFromIDB(batch.id);
          if (detailed) {
            setTranslationBatches((current) => current.map((candidate) =>
              candidate.id === detailed.id ? detailed : candidate
            ));
            if (detailed.kind === "manga-upload") {
              await resumeStudioMangaUpload(detailed);
            } else {
              await resumeStudioTranslationUpload(detailed);
            }
          }
        }
      } catch (err) {
        console.warn("Failed to restore server batches:", err);
      } finally {
      }
    };
    initialBatchLoadRef.current = restoreServerBatches();
    void initialBatchLoadRef.current;

  }, []);

  useClientLayoutEffect(() => {
    if (activeView !== "gallery") return;
    if (parsedRoute.gallerySection === "series") return;
    if (parsedRoute.overlay !== "none") return;
    const controller = new AbortController();
    void loadMangaSummaries(controller.signal);
    return () => controller.abort();
  }, [activeView, loadMangaSummaries, parsedRoute.gallerySection, parsedRoute.overlay]);

  // Server-owned batches are shared by every device.
  useEffect(() => {
    let disposed = false;
    let source: EventSource | undefined;
    const startSubscription = async () => {
      if (initialBatchLoadRef.current) await initialBatchLoadRef.current;
      if (!disposed) {
        source = subscribeServerBatches((remote) => {
          const snapshot = JSON.stringify(remote);
          if (snapshot !== translationBatchSnapshotRef.current) {
            translationBatchSnapshotRef.current = snapshot;
            const activeRemote = remote.filter((batch) => !optimisticDeletedBatchIdsRef.current.has(batch.id));

            for (const [batchId, translator] of optimisticBatchTranslatorsRef.current) {
              const serverBatch = activeRemote.find((batch) => batch.id === batchId);
              if (!serverBatch || serverBatch.settings.translator === translator) {
                optimisticBatchTranslatorsRef.current.delete(batchId);
              }
            }

            for (const batchId of optimisticDismissedBatchIdsRef.current) {
              const serverBatch = activeRemote.find((batch) => batch.id === batchId);
              if (!serverBatch || serverBatch.dismissed) {
                optimisticDismissedBatchIdsRef.current.delete(batchId);
              }
            }
            for (const batchId of optimisticDeletedBatchIdsRef.current) {
              if (!remote.some((batch) => batch.id === batchId)) {
                optimisticDeletedBatchIdsRef.current.delete(batchId);
              }
            }
            const locallyDismissed = new Set(optimisticDismissedBatchIdsRef.current);
            const optimisticTranslators = new Map(optimisticBatchTranslatorsRef.current);

            let hasNewCompletions = false;
            let hasRerenderTerminal = false;
            startTransition(() => setTranslationBatches((current) => {
              for (const remoteSummary of activeRemote) {
                const existing = current.find((b) => b.id === remoteSummary.id);
                if (
                  getBatchKind(remoteSummary) === "rerender" &&
                  (!existing || existing.status !== remoteSummary.status) &&
                  (remoteSummary.status === "completed" || remoteSummary.status === "error")
                ) {
                  hasRerenderTerminal = true;
                }
                if (existing) {
                  const updatedAtMs = remoteSummary.updatedAt
                    ? (typeof remoteSummary.updatedAt === "number" ? remoteSummary.updatedAt : new Date(remoteSummary.updatedAt).getTime())
                    : 0;
                  const isUpdated =
                    existing.updatedAt?.getTime() !== updatedAtMs ||
                    existing.completedCount !== remoteSummary.completedCount ||
                    existing.status !== remoteSummary.status ||
                    existing.failedCount !== remoteSummary.failedCount ||
                    existing.processingCount !== remoteSummary.processingCount;

                  if (existing.status !== "completed" && remoteSummary.status === "completed") {
                    hasNewCompletions = true;
                  }

                  if (isUpdated && (existing.detailsLoaded || existing.items.length > 0 || remoteSummary.status === "completed")) {
                    void loadTranslationBatchDetails(remoteSummary.id);
                  }
                }
              }
              return mergeServerBatches(current, activeRemote, locallyDismissed, optimisticTranslators);
            }));

            if (hasNewCompletions || hasRerenderTerminal) {
              if (hasRerenderTerminal) {
                galleryPageCacheRef.current.clear();
                setGalleryRevision((revision) => revision + 1);
              }
              void loadMangaSummaries();
            }
          }
        }, () => console.warn("Batch event stream disconnected; retrying..."));
      }
    };
    void startSubscription();
    return () => {
      disposed = true;
      source?.close();
    };
  }, [loadTranslationBatchDetails, loadMangaSummaries]);

  // Save navigation and options to localStorage
  useEffect(() => {
    if (typeof window !== "undefined" && window.localStorage) {
      window.localStorage.setItem("manga-studio-active-view", activeView);
    }
  }, [activeView]);

  useEffect(() => {
    if (typeof window !== "undefined" && window.localStorage) {
      window.localStorage.setItem("manga-studio-current-title", currentMangaTitle);
    }
  }, [currentMangaTitle]);

  /** Persist Studio state to IndexedDB whenever files, statuses, or selections change */
  useEffect(() => {
    if (!isStudioHydrated) return;
    const timeoutId = setTimeout(() => {
      saveStudioStateToIDB(
        files,
        fileStatuses,
        selectedFiles,
        excludedColorFiles,
        autoDetectedColorFiles,
        folderMapRef.current
      ).catch((err) => console.warn("Failed to save studio state to IDB:", err));
    }, 200);
    return () => clearTimeout(timeoutId);
  }, [isStudioHydrated, files, fileStatuses, selectedFiles, excludedColorFiles, autoDetectedColorFiles]);

  /** Save settings to localStorage whenever they change */
  useEffect(() => {
    if (!settingsHydrated) return;
    saveRememberSettings(rememberSettings);
    if (!rememberSettings) {
      clearSettings();
      return;
    }
    const settings: TranslationSettings = {
      rememberSettings: true,
      detectionResolution,
      textDetector,
      ocr,
      renderTextDirection,
      letterCase,
      uppercase: letterCase === "uppercase",
      lowercase: letterCase === "lowercase",
      translator,
      summaryModel,
      targetLanguage,
      translationQuality,
      inpaintingSize,
      customUnclipRatio,
      customBoxThreshold,
      customOcrProb,
      ocrMinConfidence: customOcrProb,
      maskDilationOffset,
      bubbleDetection,
      bubbleModel,
      inpainter,
      colorizer,
      colorizeOnly,
      colorizationSize,
      denoiseSigma,
      colorThreshold,
      upscaler,
      upscaleRatio: upscaleRatio ? Number(upscaleRatio) : null,
      revertUpscaling: Boolean(upscaleRatio) && revertUpscaling,
      translationBatchSize,
      migratedDefaultTranslator: true,
      migratedDefaultQwen2: true,
      migratedDefaultSugoi: true,
      migratedDefaultGemini: true,
      migratedDefaultBubbleDetection: true,
      migratedDefaultTargetLang: true,
    };
    saveSettings(settings);
  }, [
    detectionResolution,
    textDetector,
    ocr,
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
    bubbleModel,
    inpainter,
    colorizer,
    colorizeOnly,
    colorizationSize,
    denoiseSigma,
    colorThreshold,
    upscaler,
    upscaleRatio,
    revertUpscaling,
    translationBatchSize,
    rememberSettings,
    settingsHydrated,
  ]);

  // Existing and recent manga groups for suggestion in modal
  const existingGroups = useMemo<ExistingGroupEntry[]>(() => {
    const byTitle = new Map<string, ExistingGroupItem>();
    serverGroupTitles.forEach((title) => {
      const clean = title.trim();
      if (clean && clean.toLocaleLowerCase() !== "ungrouped") {
        byTitle.set(clean.toLocaleLowerCase(), { title: clean });
      }
    });
    mangaSummaries.forEach((grp) => {
      const title = (grp.title || "").trim();
      if (title && title.toLocaleLowerCase() !== "ungrouped") {
        const key = title.toLocaleLowerCase();
        const existing = byTitle.get(key);
        byTitle.set(key, {
          id: grp.id || existing?.id,
          title,
          count: grp.count !== undefined ? grp.count : existing?.count,
        });
      }
    });
    finishedImages.forEach((img) => {
      const title = (img.mangaTitle || "").trim();
      if (title && title.toLocaleLowerCase() !== "ungrouped") {
        const key = title.toLocaleLowerCase();
        if (!byTitle.has(key)) {
          byTitle.set(key, { id: img.groupId || undefined, title });
        }
      }
    });
    recentGroups.forEach((title) => {
      const clean = title.trim();
      if (clean && clean.toLocaleLowerCase() !== "ungrouped") {
        const key = clean.toLocaleLowerCase();
        if (!byTitle.has(key)) {
          byTitle.set(key, { title: clean });
        }
      }
    });
    translationBatches.forEach((batch) => {
      if (!activeBatchStatuses.has(batch.status)) return;
      const title = batch.mangaTitle.trim();
      if (title && title.toLocaleLowerCase() !== "ungrouped") {
        const key = title.toLocaleLowerCase();
        if (!byTitle.has(key)) {
          byTitle.set(key, { id: batch.mangaGroupId || undefined, title });
        }
      }
    });
    return Array.from(byTitle.values()).sort((a, b) =>
      a.title.localeCompare(b.title, undefined, { numeric: true, sensitivity: "base" })
    );
  }, [serverGroupTitles, mangaSummaries, finishedImages, recentGroups, translationBatches]);

  const isMangaTitleTaken = useCallback((title: string) => {
    const normalizedTitle = title.trim().toLocaleLowerCase();
    if (!normalizedTitle || normalizedTitle === "ungrouped") return false;

    return mangaSummaries.some(
      (group) => group.title.trim().toLocaleLowerCase() === normalizedTitle,
    ) || finishedImages.some(
      (image) => image.mangaTitle?.trim().toLocaleLowerCase() === normalizedTitle,
    ) || translationBatches.some(
      (batch) => activeBatchStatuses.has(batch.status)
        && batch.mangaTitle.trim().toLocaleLowerCase() === normalizedTitle,
    ) || serverGroupTitles.some((groupTitle) => groupTitle.toLocaleLowerCase() === normalizedTitle);
  }, [mangaSummaries, finishedImages, translationBatches, serverGroupTitles]);

  // Event Handlers
  /** フォーム再セット */
  const clearForm = () => {
    setFiles([]);
    setPendingFiles([]);
    setFileStatuses(() => new Map());
    setSelectedFiles(new Set());
    lastSelectedFileRef.current = null;
    setExcludedColorFiles(new Set());
    setAutoDetectedColorFiles(new Set());
    setIsExtractingArchive(false);
    setArchiveError(null);
    setCurrentMangaTitle("");
    resultUrlsRef.current.clear();
    folderMapRef.current.clear();
    clearFilesFromIDB().catch(() => {});
  };

  /** Process dropped, picked, or pasted files, expanding .cbz and .zip archives if present */
  const processIncomingFiles = useCallback(async (incoming: File[]) => {
    const dropOrder = ++studioDropOrderRef.current;
    const addedAt = Date.now();
    const pendingSources: PendingStudioFile[] = [];
    let archiveTitle = "";

    const hasArchives = incoming.some(isArchiveFile);
    if (hasArchives) {
      setIsExtractingArchive(true);
      setArchiveError(null);
    }

    try {
      for (const file of incoming) {
        if (isArchiveFile(file)) {
          try {
            const extracted = await extractArchiveImages(file);
            const sourceId = `source-${Date.now()}-${dropOrder}-${pendingSources.length}-${Math.random().toString(36).slice(2)}`;
            pendingSources.push({
              id: sourceId,
              file,
              pages: extracted.map((page, index) => ({
                id: `studio-${Date.now()}-${dropOrder}-${pendingSources.length}-${index}-${Math.random().toString(36).slice(2)}`,
                file: page,
                sourcePath: page.sourcePath,
                addedAt,
                dropOrder,
                archiveId: sourceId,
                archiveName: file.name,
                archivePageIndex: index,
              })),
            });
            if (!archiveTitle) {
              archiveTitle = extractMangaTitleFromFilename(file.name);
            }
          } catch (err) {
            console.warn(`Failed to extract images from ${file.name}:`, err);
            setArchiveError(
              err instanceof Error
                ? `${file.name}: ${err.message}`
                : `Failed to extract images from ${file.name}`
            );
          }
        } else if (
          imageMimeTypes.includes(file.type) ||
          /\.(png|jpe?g|bmp|webp)$/i.test(file.name)
        ) {
          const sourceId = `source-${Date.now()}-${dropOrder}-loose-${Math.random().toString(36).slice(2)}`;
          pendingSources.push({
            id: sourceId,
            file,
            pages: [{
              id: `studio-${Date.now()}-${dropOrder}-${pendingSources.length}-0-${Math.random().toString(36).slice(2)}`,
              file,
              sourcePath: file.webkitRelativePath || file.name,
              addedAt,
              dropOrder,
              archiveId: `loose-${dropOrder}`,
              archiveName: "Loose images",
              archivePageIndex: pendingSources.length,
            }],
          });
        }
      }
    } finally {
      if (hasArchives) {
        setIsExtractingArchive(false);
      }
    }

    if (pendingSources.length === 0) return;

    if (files.length === 0 && pendingFiles.length === 0) {
      const folderName = incoming[0]?.webkitRelativePath?.split(/[/\\]/)[0]?.trim();
      const fileName = incoming[0]?.name.replace(/\.[^.]+$/, "").trim();
      setCurrentMangaTitle((archiveTitle || folderName || fileName || "").trim());
    }

    pendingSources.sort((a, b) => naturalCompare(a.file.name, b.file.name));
    if (pendingFiles.length > 0 || (hasArchives && pendingSources.length > 1)) {
      setPendingFiles((prev) => [...prev, ...pendingSources]);
      return;
    }

    const startOrder = studioDropOrderRef.current + 1;
    const loadedPages = pendingSources.flatMap((source) => source.pages).map((page, index) => ({
      ...page,
      dropOrder: startOrder + index,
    }));
    studioDropOrderRef.current = startOrder + loadedPages.length - 1;
    setFiles((prev) => [...prev, ...loadedPages].sort((a, b) => a.dropOrder - b.dropOrder));
    setSelectedFiles((prev) => new Set([...prev, ...loadedPages.map((entry) => entry.id)]));
    void checkColorForFiles(loadedPages);
  }, [checkColorForFiles, files.length, pendingFiles.length]);

  const reorderPendingFiles = (sourceId: string, targetId: string) => {
    setPendingFiles((prev) => {
      const sourceIndex = prev.findIndex((entry) => entry.id === sourceId);
      const targetIndex = prev.findIndex((entry) => entry.id === targetId);
      if (sourceIndex < 0 || targetIndex < 0 || sourceIndex === targetIndex) return prev;
      const next = [...prev];
      const [moved] = next.splice(sourceIndex, 1);
      next.splice(targetIndex, 0, moved);
      return next;
    });
  };

  const commitPendingFiles = () => {
    if (pendingFiles.length === 0) return;
    const startOrder = studioDropOrderRef.current + 1;
    const orderedFiles = pendingFiles.flatMap((source) => source.pages).map((entry, index) => ({
      ...entry,
      dropOrder: startOrder + index,
    }));
    studioDropOrderRef.current = startOrder + orderedFiles.length - 1;
    setFiles((prev) => [...prev, ...orderedFiles].sort((a, b) => a.dropOrder - b.dropOrder));
    setSelectedFiles((prev) => new Set([...prev, ...orderedFiles.map((entry) => entry.id)]));
    setPendingFiles([]);
    void checkColorForFiles(orderedFiles);
  };

  const removePendingFile = (fileId: string) => {
    setPendingFiles((prev) => prev.filter((entry) => entry.id !== fileId));
  };

  const clearPendingFiles = () => {
    setPendingFiles([]);
    if (files.length === 0) setCurrentMangaTitle("");
  };

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

  /** ドラッグ＆ドロップ対応 */
  const handleDrop = (e: React.DragEvent<HTMLLabelElement>) => {
    e.preventDefault();
    const droppedFiles = Array.from(e.dataTransfer?.files || []);
    void processIncomingFiles(droppedFiles);
  };

  /** ファイル選択時 */
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const pickedFiles = Array.from(e.target.files || []);
    e.target.value = "";
    void processIncomingFiles(pickedFiles);
  };

  // Remove file handler
  const removeFile = (fileId: string) => {
    setFiles((prev) => prev.filter((entry) => entry.id !== fileId));
    setSelectedFiles((prev) => {
      const next = new Set(prev);
      next.delete(fileId);
      return next;
    });
    if (lastSelectedFileRef.current?.id === fileId) {
      lastSelectedFileRef.current = null;
    }
    setExcludedColorFiles((prev) => {
      const next = new Set(prev);
      next.delete(fileId);
      return next;
    });
    setAutoDetectedColorFiles((prev) => {
      const next = new Set(prev);
      next.delete(fileId);
      return next;
    });
    folderMapRef.current.delete(fileId);
    resultUrlsRef.current.delete(fileId);
    setFileStatuses((prev) => {
      const newStatuses = new Map(prev);
      newStatuses.delete(fileId);
      return newStatuses;
    });
  };

  const removeSelectedFiles = () => {
    if (selectedFiles.size === 0) return;
    const toRemove = new Set(selectedFiles);
    setFiles((prev) => prev.filter((entry) => !toRemove.has(entry.id)));
    setSelectedFiles(new Set());
    lastSelectedFileRef.current = null;
    setExcludedColorFiles((prev) => {
      const next = new Set(prev);
      for (const id of toRemove) next.delete(id);
      return next;
    });
    setAutoDetectedColorFiles((prev) => {
      const next = new Set(prev);
      for (const id of toRemove) next.delete(id);
      return next;
    });
    for (const id of toRemove) {
      folderMapRef.current.delete(id);
      resultUrlsRef.current.delete(id);
    }
    setFileStatuses((prev) => {
      const newStatuses = new Map(prev);
      for (const id of toRemove) newStatuses.delete(id);
      return newStatuses;
    });
  };

  const createStudioTranslationBatch = (
    filesToUpload: StudioFile[],
    targetMangaTitle: string,
    settings: TranslationSettings,
    targetGroupId?: string | null,
    isNewGroup?: boolean,
  ): TranslationBatch => {
    const effectiveMangaTitle = targetMangaTitle.trim() || "Ungrouped";
    const batchId = `batch-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const addedAt = new Date();
    const items: QueuedImage[] = filesToUpload.map((entry, index) => ({
      id: `item-${addedAt.getTime()}-${index}-${Math.random().toString(36).slice(2)}`,
      mangaGroupId: targetGroupId || null,
      file: entry.file,
      addedAt,
      status: "queued" as const,
      mangaTitle: effectiveMangaTitle,
      sourcePath: entry.sourcePath,
      excludeColor: excludedColorFiles.has(entry.id),
      isAutoColorDetected: autoDetectedColorFiles.has(entry.id),
    }));
    return {
      id: batchId,
      kind: "translation",
      addedAt,
      mangaTitle: effectiveMangaTitle,
      mangaGroupId: targetGroupId || null,
      isNewGroup,
      settings,
      items,
      totalItems: items.length,
      completedCount: 0,
      uploadProgress: 0,
      status: "uploading",
    };
  };

  const failStudioTranslationUpload = async (uploadBatch: TranslationBatch, error: unknown) => {
    const errorMessage = error instanceof Error ? error.message : "Failed to submit translation batch. Please try again.";
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
      console.warn(`Failed to remove failed translation upload ${uploadBatch.id}:`, removeError)
    );
  };

  const resumeStudioTranslationUpload = (uploadBatch: TranslationBatch): Promise<void> => {
    const pending = studioUploadRequestsRef.current.get(uploadBatch.id);
    if (pending) return pending;

    const performUpload = async (batch: TranslationBatch) => {
      try {
        const serverBatch = await submitServerBatch(
          batch,
          (uploadProgress) => setTranslationBatches((prev) => prev.map((batchItem) =>
            batchItem.id === uploadBatch.id ? { ...batchItem, uploadProgress } : batchItem
          )),
        );
        await removeTranslationBatchFromIDB(uploadBatch.id);
        setTranslationBatches((prev) => [
          toTranslationBatch(serverBatch),
          ...prev.filter((batchItem) => batchItem.id !== uploadBatch.id && batchItem.id !== serverBatch.id),
        ]);
        if (batch.mangaTitle && batch.mangaTitle !== "Ungrouped") {
          saveRecentGroup(batch.mangaTitle);
          setRecentGroups(loadRecentGroups());
        }
      } catch (error) {
        await failStudioTranslationUpload(uploadBatch, error);
        console.warn(`Failed to submit translation batch ${uploadBatch.id}:`, error);
      }
    };

    const request = (async () => {
      const runUpload = async () => {
        const persisted = await loadTranslationBatchFromIDB(uploadBatch.id);
        if (!persisted) {
          if (uploadBatch.items.some((item) => item.file.size > 0)) {
            await performUpload(uploadBatch);
          } else {
            setTranslationBatches((prev) => prev.filter((batch) => batch.id !== uploadBatch.id));
          }
          return;
        }
        await performUpload(persisted);
      };

      if (typeof navigator !== "undefined" && navigator.locks?.request) {
        await navigator.locks.request(`translation-batch-upload:${uploadBatch.id}`, runUpload);
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

  const handleUpdateMangaTitle = async (
    pageIds: string[],
    newMangaTitle: string,
    oldMangaTitle?: string,
    groupId?: string,
    folders: string[] = pageIds,
  ) => {
    const cleanTitle = newMangaTitle.trim() || "Ungrouped";
    setFinishedImages((prev) =>
      prev.map((img) =>
        pageIds.includes(img.id) ||
        (img.folder && folders.includes(img.folder)) ||
        (oldMangaTitle && img.mangaTitle === oldMangaTitle)
          ? { ...img, mangaTitle: cleanTitle }
          : img
      )
    );

    try {
      const validPageIds = pageIds.filter((id) => id.length > 0);
      const validFolders = folders.filter((folder) => !folder.includes("/") && folder.length > 0);
      await fetch(apiUrl("/api/results/update-meta"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          folders: validFolders.length > 0 ? validFolders : undefined,
          pageIds: validPageIds.length > 0 ? validPageIds : undefined,
          oldMangaTitle: oldMangaTitle || undefined,
          mangaTitle: cleanTitle,
          groupId: groupId || undefined,
        }),
      });
      await loadMangaSummaries();
    } catch (err) {
      console.warn("Failed to update manga title on server:", err);
    }
  };

  const importOriginalManga = useCallback(async (
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
  }, []);

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

  const addImportedMangaBatch = async (
    items: ImportedMangaItem[],
    mangaTitle: string,
    groupId?: string | null,
    isNewGroup?: boolean,
  ): Promise<ServerBatch> => {
    const cleanTitle = mangaTitle.trim() || "Ungrouped";
    const importedItems = items.filter(
      (item): item is ImportedMangaItem & { folder: string } => Boolean(item.folder),
    );
    if (importedItems.length === 0) {
      throw new Error("Import completed without any manga pages");
    }

    const addedAt = new Date();
    const batch: TranslationBatch = {
      id: `original-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      kind: "manga-upload",
      addedAt,
      mangaTitle: cleanTitle,
      mangaGroupId: groupId || importedItems[0]?.groupId || null,
      isNewGroup,
      settings: {
        ...getCurrentSettings(),
        translator: "none",
        inpainter: "original",
        colorizer: "none",
        colorizeOnly: false,
      },
      items: importedItems.map((item) => ({
        id: item.folder,
        pageId: item.id || null,
        pageOrder: item.pageOrder,
        sourcePath: item.sourcePath,
        mangaGroupId: item.groupId || groupId || null,
        file: new File([], item.originalName || `${item.folder}.jpg`),
        addedAt,
        status: "finished" as const,
        mangaTitle: cleanTitle,
        folder: item.folder,
      })),
      totalItems: importedItems.length,
      completedCount: importedItems.length,
      status: "completed",
    };

    const serverBatch = await submitServerBatch(batch);
    return serverBatch;
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
    // Do not restore a failed import as an active upload on the next page load.
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
          data = await importOriginalManga(
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
        const serverBatch = await addImportedMangaBatch(
          data.items || [],
          batch.mangaTitle,
          batch.mangaGroupId || batch.items[0]?.mangaGroupId || null,
          batch.isNewGroup,
        );
        await removeTranslationBatchFromIDB(uploadBatch.id);
        setTranslationBatches((prev) => [
          toTranslationBatch(serverBatch),
          ...prev.filter((batchItem) => batchItem.id !== uploadBatch.id && batchItem.id !== serverBatch.id),
        ]);
        await loadMangaSummaries();
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

  const clearGallery = async () => {
    try {
      await fetch(apiUrl("/api/results/clear"), { method: "DELETE" });
    } catch (err) {
      console.warn("Failed to clear results on server:", err);
    }
    setFinishedImages([]);
    setMangaSummaries([]);
    setTotalMangaCount(0);
    setTotalGalleryCount(0);
    setIsGalleryLoading(false);
    if (typeof window !== "undefined" && typeof window.localStorage !== "undefined") {
      window.localStorage.removeItem("manga-translator-finished-images");
    }
  };

  const updateFinishedImage = (updated: FinishedImage) => {
    setFinishedImages((prev) =>
      prev.map((img) =>
        img.id === updated.id || (img.folder && img.folder === updated.folder) ? updated : img
      )
    );
  };

  const deleteFinishedImage = async (image: FinishedImage) => {
    if (image.folder) {
      try {
        await fetch(apiUrl(`/api/results/${image.folder}`), { method: "DELETE" });
      } catch (err) {
        console.warn(`Failed to delete result ${image.folder} on server:`, err);
      }
    }
    setFinishedImages((prev) => prev.filter((img) => img.id !== image.id));
    setTotalGalleryCount((prev) => Math.max(0, prev - 1));
    setMangaSummaries((prev) => {
      const mangaTitle = (image.mangaTitle || "Ungrouped").trim() || "Ungrouped";
      return prev
        .map((g) => (g.title === mangaTitle ? { ...g, count: Math.max(0, g.count - 1) } : g))
        .filter((g) => g.count > 0);
    });
  };

  const deleteFinishedImages = async (images: FinishedImage[]) => {
    if (images.length === 0) return;
    galleryPageCacheRef.current.clear();
    const folders = images.map((img) => img.folder).filter(Boolean) as string[];
    await Promise.allSettled(
      folders.map((folder) =>
        fetch(apiUrl(`/api/results/${folder}`), { method: "DELETE" }).catch((err) =>
          console.warn(`Failed to delete result ${folder} on server:`, err)
        )
      )
    );
    const deletedIds = new Set(images.map((img) => img.id));
    const titleCounts = new Map<string, number>();
    for (const img of images) {
      const t = (img.mangaTitle || "Ungrouped").trim() || "Ungrouped";
      titleCounts.set(t, (titleCounts.get(t) || 0) + 1);
    }

    setFinishedImages((prev) => prev.filter((img) => !deletedIds.has(img.id)));
    setTotalGalleryCount((prev) => Math.max(0, prev - images.length));
    setMangaSummaries((prev) => {
      return prev
        .map((g) => {
          const removed = titleCounts.get(g.title) || 0;
          return removed > 0 ? { ...g, count: Math.max(0, g.count - removed) } : g;
        })
        .filter((g) => g.count > 0);
    });
  };

  const reorderMangaPages = async (groupId: string, pageIds: string[]) => {
    const response = await fetch(apiUrl(`/api/manga/${encodeURIComponent(groupId)}/pages/order`), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pageIds }),
    });
    if (!response.ok) {
      throw new Error(`Could not save page order (${response.status})`);
    }
    const payload = await response.json() as { pages?: Array<{ id: string; pageOrder: number }> };
    const orders = new Map((payload.pages || []).map((page) => [page.id, page.pageOrder]));
    setFinishedImages((previous) => previous.map((image) => (
      orders.has(image.id) ? { ...image, pageOrder: orders.get(image.id) } : image
    )));
    await loadMangaSummaries();
  };

  const deleteMangaGroup = async (images: FinishedImage[], mangaTitle?: string) => {
    const title = mangaTitle || images[0]?.mangaTitle || "Ungrouped";
    galleryPageCacheRef.current.clear();
    try {
      await fetch(apiUrl(`/api/results/group?title=${encodeURIComponent(title)}`), { method: "DELETE" });
    } catch (err) {
      await Promise.allSettled(
        images
          .filter((img) => img.folder)
          .map((img) => fetch(apiUrl(`/api/results/${img.folder}`), { method: "DELETE" }))
      );
    }
    const ids = new Set(images.map((img) => img.id));
    setFinishedImages((prev) => prev.filter((img) => !ids.has(img.id) && img.mangaTitle !== title));
    setMangaSummaries((prev) => {
      const match = prev.find((g) => g.title === title);
      if (match) {
        setTotalGalleryCount((count) => Math.max(0, count - match.count));
        setTotalMangaCount((count) => Math.max(0, count - 1));
      }
      return prev.filter((g) => g.title !== title);
    });
  };

  const deleteMangaGroups = async (mangaList: Array<{ title: string; images?: FinishedImage[] }>) => {
    if (mangaList.length === 0) return;
    galleryPageCacheRef.current.clear();
    await Promise.allSettled(
      mangaList.map(async ({ title, images = [] }) => {
        try {
          await fetch(apiUrl(`/api/results/group?title=${encodeURIComponent(title)}`), { method: "DELETE" });
        } catch {
          await Promise.allSettled(
            images
              .filter((img) => img.folder)
              .map((img) => fetch(apiUrl(`/api/results/${img.folder}`), { method: "DELETE" }))
          );
        }
      })
    );
    const titlesToDelete = new Set(mangaList.map((m) => m.title));
    const allImagesToDelete = mangaList.flatMap((m) => m.images || []);
    const idsToDelete = new Set(allImagesToDelete.map((img) => img.id));
    setFinishedImages((prev) =>
      prev.filter(
        (img) => !idsToDelete.has(img.id) && !titlesToDelete.has((img.mangaTitle || "Ungrouped").trim() || "Ungrouped")
      )
    );
    setMangaSummaries((prev) => {
      let removedTotal = 0;
      let removedCount = 0;
      for (const g of prev) {
        if (titlesToDelete.has(g.title)) {
          removedTotal += g.count;
          removedCount += 1;
        }
      }
      setTotalGalleryCount((count) => Math.max(0, count - removedTotal));
      setTotalMangaCount((count) => Math.max(0, count - removedCount));
      return prev.filter((g) => !titlesToDelete.has(g.title));
    });
  };

  // Selection helpers
  const toggleFileSelection = (fileId: string, shiftKey = false) => {
    const allIds = files.map((entry) => entry.id);
    const isItemSelectable = (id: string) => {
      const status = fileStatuses.get(id);
      const isCurrentFileActive = Boolean(
        status?.status && processingStatuses.includes(status.status)
      );
      return !isCurrentFileActive;
    };

    setSelectedFiles((prev) => {
      const res = computeRangeSelection({
        selectedIds: prev,
        allIds,
        targetId: fileId,
        shiftKey,
        anchor: lastSelectedFileRef.current,
        isItemSelectable,
      });
      lastSelectedFileRef.current = res.nextAnchor;
      return res.nextSelectedIds;
    });

    if (shiftKey) {
      clearBrowserTextSelection();
    }
  };

  const selectAllFiles = () => {
    setSelectedFiles(new Set(files.map((entry) => entry.id)));
    lastSelectedFileRef.current = null;
  };

  const deselectAllFiles = () => {
    setSelectedFiles(new Set());
    lastSelectedFileRef.current = null;
  };

  /** Open group assignment before creating the next translation batch. */
  const handleSubmit = () => {
    const filesToTranslate = files.filter((entry) => selectedFiles.has(entry.id));
    if (filesToTranslate.length === 0) return;

    setTranslationBatchError(null);
    setPendingTranslationTargets(filesToTranslate);
    setIsGroupModalOpen(true);
  };

  const handleConfirmGroup = async (selectedGroup: MangaGroupSelection | string, storyPlan?: StoryPlan) => {
    if (isConfirmingTranslationRef.current) return;
    const rawTitle = typeof selectedGroup === "string" ? selectedGroup : selectedGroup.title;
    const cleanGroup = rawTitle.trim() || "Ungrouped";
    const groupId = typeof selectedGroup === "object" ? selectedGroup.groupId : undefined;
    const isNewGroup = typeof selectedGroup === "object" ? selectedGroup.isNewGroup : undefined;
    const targets = pendingTranslationTargets;
    if (targets.length === 0) return;

    isConfirmingTranslationRef.current = true;
    setTranslationBatchError(null);
    const batchSettings = {
      ...getCurrentSettings(),
      ...(storyPlan ? { storyPlan } : {}),
    };
    setCurrentMangaTitle(cleanGroup);
    if (cleanGroup !== "Ungrouped") {
      saveRecentGroup(cleanGroup);
      setRecentGroups(loadRecentGroups());
    }
    const targetIds = new Set(targets.map((entry) => entry.id));
    setIsGroupModalOpen(false);
    setPendingTranslationTargets([]);

    // Hand off submitted pages immediately from Studio to the translation batch queue
    setFiles((prev) => prev.filter((entry) => !targetIds.has(entry.id)));
    setSelectedFiles((prev) => new Set([...prev].filter((id) => !targetIds.has(id))));
    setExcludedColorFiles((prev) => new Set([...prev].filter((id) => !targetIds.has(id))));
    setAutoDetectedColorFiles((prev) => new Set([...prev].filter((id) => !targetIds.has(id))));
    setFileStatuses((prev) => new Map([...prev].filter(([id]) => !targetIds.has(id))));

    const uploadBatch = createStudioTranslationBatch(targets, cleanGroup, batchSettings, groupId, isNewGroup);
    setTranslationBatches((prev) => [uploadBatch, ...prev]);
    setIsJobsOpen(true);
    isConfirmingTranslationRef.current = false;

    try {
      await saveTranslationBatchToIDB(uploadBatch);
      await resumeStudioTranslationUpload(uploadBatch);
    } catch (error) {
      await failStudioTranslationUpload(uploadBatch, error);
    }
  };

  const handleCloseGroupModal = () => {
    setIsGroupModalOpen(false);
    setPendingTranslationTargets([]);
    setTranslationBatchError(null);
  };

  // Helper to open lightbox for any file/result
  const handleOpenLightbox = (
    file: File | StudioFile | string,
    result: Blob | File | string | null,
    onRetry?: () => void | Promise<void>,
    sourceType?: FinishedImage["sourceType"],
    options?: {
      folder?: string;
      fileName?: string;
      settings?: Partial<TranslationSettings>;
      mangaTitle?: string;
      offlineModel?: string;
      geminiModel?: string;
    },
  ) => {
    if (!result) return;
    const studioFile = typeof file === "object" && "file" in file ? file : null;
    const studioId = studioFile?.id || null;
    const rawFile: File | string = studioFile ? studioFile.file : file as File | string;
    const fileName = options?.fileName
      ?? (typeof rawFile === "string" ? (rawFile.includes("/") ? rawFile.split("/").pop() || rawFile : rawFile) : rawFile.name);
    const folder = options?.folder
      ?? (studioId ? folderMapRef.current.get(studioId) : null)
      ?? folderMapRef.current.get(fileName)
      ?? (typeof result === "string" ? resultFolderFromUrl(result) : null)
      ?? (typeof file === "string" ? resultFolderFromUrl(file) : null)
      ?? undefined;
    const inputUrl = rawFile instanceof File && folder
      ? apiUrl(`/result/${folder}/input.png`)
      : (rawFile instanceof File ? rawFile : (typeof rawFile === "string" ? rawFile : null));

    const mergedSettings: Partial<TranslationSettings> = {
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
      ...(options?.settings || {}),
    };
    if (options?.offlineModel) mergedSettings.offlineModel = options.offlineModel;
    if (options?.geminiModel) mergedSettings.geminiModel = options.geminiModel;

    const finishedItem: FinishedImage = {
      id: `${fileName}-${Date.now()}`,
      originalName: fileName,
      mangaTitle: options?.mangaTitle,
      sourcePath: studioFile?.sourcePath,
      result: result as Blob | string,
      sourceType,
      inputUrl,
      inpaintedUrl: folder ? `/result/${folder}/inpainted.jpg` : undefined,
      textRegionsUrl: folder ? `/result/${folder}/text_regions.json` : undefined,
      hasTextRegions: Boolean(folder),
      folder,
      finishedAt: new Date(),
      settings: mergedSettings,
    };
    setSelectedImageForModal(finishedItem);
    setSelectedImageRetry(() => onRetry ?? null);
  };

  const closeStudioViewer = useCallback(() => {
    setSelectedImageForModal(null);
    setSelectedImageRetry(null);
  }, []);


  const updateTranslationBatchTranslator = React.useCallback(
    (batchId: string, translator: TranslatorKey) => {
      const batch = translationBatches.find((candidate) => candidate.id === batchId);
      if (!batch || !canChangeBatchTranslator(batch)) return;
      optimisticBatchTranslatorsRef.current.set(batchId, translator);
      setTranslationBatches((prev) =>
        prev.map((batch) =>
          batch.id === batchId
            ? { ...batch, settings: { ...batch.settings, translator } }
            : batch
        )
      );
      void updateBatchTranslator(batchId, translator).catch((error) => {
        if (optimisticBatchTranslatorsRef.current.get(batchId) === translator) {
          optimisticBatchTranslatorsRef.current.delete(batchId);
        }
        console.warn("Failed to update batch translator:", error);
      });
    },
    [translationBatches]
  );

  const updateTranslationBatchManualReview = React.useCallback(
    (batchId: string, enabled: boolean) => {
      setTranslationBatches((prev) =>
        prev.map((batch) =>
          batch.id === batchId
            ? { ...batch, settings: { ...batch.settings, keepFailedPagesForEditing: enabled } }
            : batch
        )
      );
      void updateBatchManualReview(batchId, enabled).catch((error) =>
        console.warn("Failed to update manual review setting:", error)
      );
    },
    []
  );

  const updateTranslationBatchPriority = React.useCallback(
    async (batchId: string, priority: boolean) => {
      setTranslationBatches((prev) =>
        prev.map((batch) =>
          batch.id === batchId && batch.status === "waiting"
            ? { ...batch, priority }
            : batch
        )
      );
      const serverBatch = await updateBatchPriority(batchId, priority);
      setTranslationBatches((prev) => [
        toTranslationBatch(serverBatch),
        ...prev.filter((batch) => batch.id !== serverBatch.id),
      ]);
    },
    []
  );

  const pauseTranslation = React.useCallback(async (batchId: string) => {
    const activeBatch = translationBatches.find((batch) => batch.id === batchId && batch.status === "processing");
    if (!activeBatch) return;
    batchMutationVersionRef.current += 1;
    try {
      const serverBatch = await batchAction(activeBatch.id, "pause");
      setTranslationBatches((prev) => [
        toTranslationBatch(serverBatch),
        ...prev.filter((batch) => batch.id !== serverBatch.id),
      ]);
    } catch (error) {
      console.warn("Failed to pause batch:", error);
      throw error;
    }
  }, [translationBatches]);

  const resumeTranslation = React.useCallback(async (batchId: string) => {
    const activeBatch = translationBatches.find((batch) => batch.id === batchId && batch.status === "paused");
    if (!activeBatch) return;
    batchMutationVersionRef.current += 1;
    try {
      const serverBatch = await batchAction(activeBatch.id, "resume");
      setTranslationBatches((prev) => [
        toTranslationBatch(serverBatch),
        ...prev.filter((batch) => batch.id !== serverBatch.id),
      ]);
    } catch (error) {
      console.warn("Failed to resume batch:", error);
      throw error;
    }
  }, [translationBatches]);



  const dismissTranslationBatch = React.useCallback((batchId: string) => {
    optimisticDismissedBatchIdsRef.current.add(batchId);
    setTranslationBatches((prev) => prev.map((batch) =>
      batch.id === batchId ? { ...batch, dismissed: true } : batch
    ));
    void removeTranslationBatchFromIDB(batchId).catch(() => {});
    if (batchId.startsWith("upload-") || batchId.startsWith("legacy-") || batchId.startsWith("original-")) {
      // If it's a client or imported batch, try dismiss on server if exists, but don't revert on 404
      void batchAction(batchId, "dismiss").catch(() => {});
      return;
    }
    void batchAction(batchId, "dismiss").catch((error) => {
      const status = (error as { status?: number })?.status;
      if (status !== 404 && !String(error).includes("404")) {
        optimisticDismissedBatchIdsRef.current.delete(batchId);
        setTranslationBatches((prev) => prev.map((batch) =>
          batch.id === batchId ? { ...batch, dismissed: false } : batch
        ));
        console.warn("Failed to dismiss batch:", error);
      }
    });
  }, []);

  const removeTranslationBatch = React.useCallback((batchId: string) => {
    batchMutationVersionRef.current += 1;
    optimisticDeletedBatchIdsRef.current.add(batchId);
    setTranslationBatches((prev) => prev.filter((batch) => batch.id !== batchId));
    void removeTranslationBatchFromIDB(batchId).catch(() => {});

    const pendingUpload = studioUploadRequestsRef.current.get(batchId);
    if (pendingUpload) {
      studioUploadRequestsRef.current.delete(batchId);
    }

    if (batchId.startsWith("upload-") || batchId.startsWith("legacy-")) {
      return Promise.resolve();
    }
    return removeServerBatch(batchId).catch((error) => {
      const status = (error as { status?: number })?.status;
      if (status !== 404 && !String(error).includes("404")) {
        optimisticDeletedBatchIdsRef.current.delete(batchId);
        console.warn("Failed to remove batch:", error);
        throw error;
      }
    });
  }, []);

  const removeTranslationItem = (batchId: string, itemId: string) => {
    void removeBatchItem(batchId, itemId).catch((error) =>
      console.warn("Failed to remove batch item:", error)
    );
  };

  const retryTranslationItem = (
    batchId: string,
    itemId: string,
    keepFailedPagesForEditing = false,
  ) => {
    const targetBatch = translationBatches.find((b) => b.id === batchId);
    if (targetBatch?.kind === "manga-upload") {
      return resumeStudioMangaUpload(targetBatch);
    }
    batchMutationVersionRef.current += 1;
    return retryBatchItem(batchId, itemId, keepFailedPagesForEditing).catch((error) => {
      console.warn("Failed to retry batch item:", error);
      throw error;
    }).then((serverBatch) => {
      setTranslationBatches((prev) => [
        toTranslationBatch(serverBatch),
        ...prev.filter((batch) => batch.id !== serverBatch.id),
      ]);
    });
  };

  const retryFinishedImage = (image: FinishedImage) => {
    const match = image.folder
      ? translationBatches
          .flatMap((batch) => batch.items.map((item) => ({ batch, item })))
          .find(({ item }) => item.folder === image.folder)
      : undefined;
    if (!match) {
      throw new Error("This image is not linked to a retryable translation batch.");
    }
    return retryTranslationItem(match.batch.id, match.item.id);
  };

  const [pipelineRerunState, setPipelineRerunState] = useState<{
    images: FinishedImage[];
    groupId?: string;
    mangaTitle?: string;
  } | null>(null);

  const handleOpenPipelineRerun = useCallback((images: FinishedImage[], groupId?: string, mangaTitle?: string) => {
    const eligible = images.filter((img) => img.id);
    if (!eligible.length) {
      window.alert("No pages were selected for pipeline rerun.");
      return;
    }
    setPipelineRerunState({ images: eligible, groupId, mangaTitle });
  }, []);

  const handleExecutePipelineRerun = useCallback(async ({
    mode,
    settingsOverrides,
  }: {
    mode: any;
    settingsOverrides?: any;
  }) => {
    if (!pipelineRerunState) return;
    const pageIds = pipelineRerunState.images.map((img) => img.id);
    const serverBatch = await rerunPipeline({
      pageIds: pipelineRerunState.groupId ? undefined : pageIds,
      groupId: pipelineRerunState.groupId,
      mode,
      settingsOverrides,
    });
    setTranslationBatches((prev) => [
      toTranslationBatch(serverBatch),
      ...prev.filter((batch) => batch.id !== serverBatch.id),
    ]);
  }, [pipelineRerunState]);

  const rerenderImages = useCallback(async (images: FinishedImage[]) => {
    handleOpenPipelineRerun(images);
  }, [handleOpenPipelineRerun]);


  const handleCloseJobs = useCallback(() => setIsJobsOpen(false), []);

  const handleDismissSummaryJob = useCallback(async (job: SummaryJob) => {
    await dismissSummaryJob(job);
    setSummaryJobs((current) => current.filter((candidate) => candidate.id !== job.id));
  }, []);

  const handleRetrySummaryJob = useCallback(async (job: SummaryJob, summaryModel: string, refreshText = false) => {
    await retrySummaryJob(job, summaryModel, refreshText);
    setSummaryJobs((current) => current.map((candidate) => candidate.id === job.id
      ? {
          ...candidate,
          status: "generating",
          jobStage: refreshText ? "detecting" : "summarizing",
          jobProgress: 0,
          jobError: null,
          jobExtractionRequired: refreshText || candidate.jobExtractionRequired,
        }
      : candidate));
  }, []);

  const handlePauseSummaryJob = useCallback(async (job: SummaryJob) => {
    await pauseSummaryJob(job);
    setSummaryJobs((current) => current.map((candidate) => candidate.id === job.id
      ? { ...candidate, status: "paused" }
      : candidate));
  }, []);

  const handleResumeSummaryJob = useCallback(async (job: SummaryJob) => {
    await resumeSummaryJob(job);
    setSummaryJobs((current) => current.map((candidate) => candidate.id === job.id
      ? { ...candidate, status: "generating" }
      : candidate));
  }, []);

  const handleStopSummaryJob = useCallback(async (job: SummaryJob) => {
    await stopSummaryJob(job);
    setSummaryJobs((current) => current.filter((candidate) => candidate.id !== job.id));
  }, []);

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
        onSelectView={handleSelectView}
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
          <div>
            <div className="min-w-0 space-y-6">
              <OptionsPanel
                detectionResolution={detectionResolution}
                textDetector={textDetector}
                ocr={ocr}
                renderFont={renderFont}
                renderTextDirection={renderTextDirection}
                letterCase={letterCase}
                translator={translator}
                summaryModel={summaryModel}
                targetLanguage={targetLanguage}
                translationQuality={translationQuality}
                inpaintingSize={inpaintingSize}
                customUnclipRatio={customUnclipRatio}
                customBoxThreshold={customBoxThreshold}
                customOcrProb={customOcrProb}
                maskDilationOffset={maskDilationOffset}
                bubbleDetection={bubbleDetection}
                inpainter={inpainter}
                colorizer={colorizer}
                colorizeOnly={colorizeOnly}
                colorizationSize={colorizationSize}
                denoiseSigma={denoiseSigma}
                colorThreshold={colorThreshold}
                upscaler={upscaler}
                upscaleRatio={upscaleRatio}
                revertUpscaling={revertUpscaling}
                rememberSettings={rememberSettings}
                translationBatchSize={translationBatchSize}
                setDetectionResolution={setDetectionResolution}
                setTextDetector={setTextDetector}
                setOcr={setOcr}
                setRenderFont={setRenderFont}
                setRenderTextDirection={setRenderTextDirection}
                setLetterCase={setLetterCase}
                setTranslator={setTranslator}
                setSummaryModel={setSummaryModel}
                setTargetLanguage={setTargetLanguage}
                setTranslationQuality={setTranslationQuality}
                setInpaintingSize={setInpaintingSize}
                setCustomUnclipRatio={setCustomUnclipRatio}
                setCustomBoxThreshold={setCustomBoxThreshold}
                setCustomOcrProb={setCustomOcrProb}
                setMaskDilationOffset={setMaskDilationOffset}
                setBubbleDetection={setBubbleDetection}
                setInpainter={setInpainter}
                setColorizer={setColorizer}
                setColorizeOnly={setColorizeOnly}
                setColorizationSize={setColorizationSize}
                setDenoiseSigma={setDenoiseSigma}
                setColorThreshold={setColorThreshold}
                setUpscaler={setUpscaler}
                setUpscaleRatio={setUpscaleRatio}
                setRevertUpscaling={setRevertUpscaling}
                setRememberSettings={setRememberSettings}
                setTranslationBatchSize={setTranslationBatchSize}
              />

              <ImageHandlingArea
                files={files}
                pendingFiles={pendingFiles}
                fileStatuses={fileStatuses}
                isProcessing={isProcessing}
                isProcessingAllFinished={isProcessingAllFinished}
                selectedFiles={selectedFiles}
                mangaTitle={currentMangaTitle}
                onMangaTitleChange={setCurrentMangaTitle}
                handleFileChange={handleFileChange}
                handleDrop={handleDrop}
                onConfirmPendingFiles={commitPendingFiles}
                onRemovePendingFile={removePendingFile}
                onReorderPendingFiles={reorderPendingFiles}
                onClearPendingFiles={clearPendingFiles}
                onUploadManga={handleStudioMangaUpload}
                uploadMangaError={studioMangaUploadError}
                handleSubmit={handleSubmit}
                clearForm={clearForm}
                removeFile={removeFile}
                onRemoveSelectedFiles={removeSelectedFiles}
                onToggleFile={toggleFileSelection}
                onSelectAll={selectAllFiles}
                onDeselectAll={deselectAllFiles}
                onOpenLightbox={handleOpenLightbox}
                onOpenInPipelineLab={(targetFile) => {
                  setPipelineLabInitialFile(targetFile);
                  navigate("/pipeline");
                }}
                excludedColorFiles={excludedColorFiles}
                autoDetectedColorFiles={autoDetectedColorFiles}
                onToggleExcludeColor={toggleExcludeColorFile}
                onSetExcludeColorForSelected={setExcludeColorForSelected}
                isColorizerActive={colorizer !== "none" || colorizeOnly}
                isExtractingArchive={isExtractingArchive}
                archiveError={archiveError}
                onDismissArchiveError={() => setArchiveError(null)}
              />
              {translationBatchError && !isGroupModalOpen && (
                <p role="alert" className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-300">
                  {translationBatchError} Select the files again to retry.
                </p>
              )}
              {studioMangaUploadWarning && (
                <p role="status" className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
                  {studioMangaUploadWarning}
                </p>
              )}

              {/* Link banner to gallery if finished items exist */}
              {totalGalleryCount > 0 && (
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-indigo-200 dark:border-indigo-900/60 bg-indigo-50/50 dark:bg-indigo-950/20 p-4 shadow-2xs">
                  <div className="flex items-center space-x-3">
                    <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-600 text-white shadow-xs">
                      <Icon icon="carbon:image" className="h-5 w-5" />
                    </div>
                    <div>
                      <div className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                        {totalGalleryCount} {totalGalleryCount === 1 ? "page" : "pages"} in gallery
                      </div>
                      <div className="text-xs text-zinc-500 dark:text-zinc-400">
                        Open full-screen comparison lightbox with zoom and batch download
                      </div>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => navigate("/gallery")}
                    className="flex items-center space-x-1.5 rounded-lg bg-white dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 px-3.5 py-1.5 text-xs font-semibold text-zinc-800 dark:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-700 transition-colors shadow-2xs"
                  >
                    <span>Open Gallery</span>
                    <Icon icon="carbon:arrow-right" className="h-3.5 w-3.5" />
                  </button>
                </div>
              )}
            </div>

          </div>
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
              onClearGallery={clearGallery}
              onDeleteImage={deleteFinishedImage}
              onDeleteImages={deleteFinishedImages}
              onDeleteManga={deleteMangaGroup}
              onDeleteMangas={deleteMangaGroups}
              onReorderMangaPages={reorderMangaPages}
              onUpdateImage={updateFinishedImage}
              onUpdateMangaTitle={handleUpdateMangaTitle}
              onOpenPageView={handleOpenPageView}
              onOpenPageEdit={handleOpenPageEdit}
              onRetryImage={retryFinishedImage}
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
        {activeView === "pipeline" && (
          <div className="space-y-6">
            <PipelineLab showHeader={false} initialFile={pipelineLabInitialFile} />
          </div>
        )}
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
          onRetry={selectedImageRetry ?? (selectedImageForModal.folder ? retryFinishedImage : undefined)}
          onRerender={selectedImageForModal.folder ? (image) => handleOpenPipelineRerun([image], selectedImageForModal.groupId ?? undefined, selectedImageForModal.mangaTitle) : undefined}
          titlePrefix="Studio preview"
        />
      )}

      {pipelineRerunState && (
        <PipelineRerunDialog
          images={pipelineRerunState.images}
          groupId={pipelineRerunState.groupId}
          mangaTitle={pipelineRerunState.mangaTitle}
          onClose={() => setPipelineRerunState(null)}
          onSubmit={handleExecutePipelineRerun}
        />
      )}
    </div>
  );
};


export default App;
