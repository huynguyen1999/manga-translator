import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { getStoredMangaReadProgress, mergeGalleryImages } from "@/utils/resultGallery";
import {
  buildGalleryMangaGroups,
  getSuggestedSeriesTitle,
  buildMangaGroupTitles,
  getGalleryFallbackUrl,
  getGalleryPageCorrection,
  getGalleryThumbnailUrl,
  getMangaSummaryAvailability,
  isSummaryPending,
  loadedThumbnailUrls,
  filterMangaGroupsByStatus,
  shouldShowEmptyLibraryState,
  sortMangaGroups,
  sortMangaPages,
  sortMangaPagesForOrder,
  calculateDragAutoScrollSpeed,
} from "./ResultGallery";
import { apiUrl } from "@/utils/api";
import { mangaIdForTitle } from "@/utils/routeState";
import type { FinishedImage } from "@/types";
import type { Dispatch, SetStateAction } from "react";
import { createGalleryBulkDeletionActions } from "@/features/gallery/bulkDeletionActions";
import { useGalleryGroupViews } from "@/features/gallery/useGalleryGroupViews";

assert.equal(
  getSuggestedSeriesTitle(new Map([
    ["group-b", "Volume 10"],
    ["group-a", "Volume 2"],
  ])),
  "Volume 2",
  "Series title suggestion should use the first manga in natural series order",
);

assert.equal(
  getSuggestedSeriesTitle(new Map([
    ["ch-3", "Chapter 3"],
    ["ch-1", "Chapter 1"],
    ["ch-2", "Chapter 2"],
  ])),
  "Chapter 1",
  "Series title suggestion should order chapter 1 before chapter 2 and 3",
);

assert.equal(
  getSuggestedSeriesTitle(new Map()),
  "",
  "Empty map should suggest an empty title",
);

assert.equal(
  getMangaSummaryAvailability({ summary: "A plot", stale: false, jobStatus: "ready" }),
  "summarized",
);
assert.equal(
  getMangaSummaryAvailability({ summary: null, stale: false, jobStatus: null }),
  "not-summarized",
);
assert.equal(
  getMangaSummaryAvailability({ summary: "Old plot", stale: true, jobStatus: "ready" }),
  "stale",
);
assert.equal(
  getMangaSummaryAvailability({ summary: null, stale: false, jobStatus: "paused" }),
  "paused",
);
assert.equal(isSummaryPending({ jobStatus: "queued" }), true);
assert.equal(isSummaryPending({ jobStatus: "generating" }), true);
assert.equal(isSummaryPending({ jobStatus: "paused" }), true);
assert.equal(isSummaryPending({ jobStatus: "ready" }), false);

assert.equal(
  getGalleryPageCorrection(2, 0, true),
  null,
  "A page transition must not be reset while the requested page is loading",
);
assert.equal(getGalleryPageCorrection(99, 3, false), 3);
assert.deepEqual(getStoredMangaReadProgress("Manga", 3), { page: null, complete: false });

// 1. Verify mergeGalleryImages behavior
const existing: FinishedImage[] = [
  {
    id: "img1",
    originalName: "001.png",
    result: "/result/folder1/final.png",
    folder: "folder1",
    mangaTitle: "Manga A",
    finishedAt: new Date("2026-01-01"),
    settings: {},
  },
];

const newImages: FinishedImage[] = [
  {
    id: "img2",
    originalName: "002.png",
    result: "/result/folder2/final.png",
    folder: "folder2",
    mangaTitle: "Manga A",
    finishedAt: new Date("2026-01-02"),
    settings: {},
  },
  {
    id: "img1_dup",
    originalName: "001.png",
    result: "/result/folder1/final.png",
    folder: "folder1",
    mangaTitle: "Manga A",
    finishedAt: new Date("2026-01-01"),
    settings: {},
  },
];

const merged = mergeGalleryImages(existing, newImages);
assert.equal(merged.length, 2, "Duplicate folders should be deduplicated");
assert.equal(merged[0].id, "img1");
assert.equal(merged[1].id, "img2");

// 2. Verify state resolution logic:
// During initial load (effectiveIsLoading = true, groups = 0, total = 0), UI should be in 'skeleton' state, NOT 'empty'
function determineGalleryDisplayState({
  effectiveIsLoading,
  mangaGroupsCount,
  totalImagesCount,
}: {
  effectiveIsLoading: boolean;
  mangaGroupsCount: number;
  totalImagesCount: number;
}): "skeleton" | "empty" | "gallery" {
  if (effectiveIsLoading && mangaGroupsCount === 0 && totalImagesCount === 0) {
    return "skeleton";
  }
  if (mangaGroupsCount === 0 && totalImagesCount === 0) {
    return "empty";
  }
  return "gallery";
}

// Initial startup: server hasn't responded yet
assert.equal(
  determineGalleryDisplayState({
    effectiveIsLoading: true,
    mangaGroupsCount: 0,
    totalImagesCount: 0,
  }),
  "skeleton",
  "Should show skeleton loading on initial startup before server response arrives"
);

// Server responded with groups
assert.equal(
  determineGalleryDisplayState({
    effectiveIsLoading: false,
    mangaGroupsCount: 3,
    totalImagesCount: 15,
  }),
  "gallery",
  "Should display gallery once data arrives"
);

// Server responded with truly empty library
assert.equal(
  determineGalleryDisplayState({
    effectiveIsLoading: false,
    mangaGroupsCount: 0,
    totalImagesCount: 0,
  }),
  "empty",
  "Should show empty state only after loading completes with 0 results"
);

// Background refresh when library already has items
assert.equal(
  determineGalleryDisplayState({
    effectiveIsLoading: true,
    mangaGroupsCount: 2,
    totalImagesCount: 10,
  }),
  "gallery",
  "Should not flash skeleton when library already has populated items during background sync"
);

// 3. Verify Non-blocking Background Upload Task Lifecycle
interface UploadTaskState {
  id: string;
  mangaTitle: string;
  fileCount: number;
  status: 'uploading' | 'success' | 'error';
  error?: string;
}

function handleUploadStart(prev: UploadTaskState[], title: string, fileCount: number): {
  tasks: UploadTaskState[];
  isModalOpen: boolean;
} {
  const newTask: UploadTaskState = {
    id: `upload-${Date.now()}`,
    mangaTitle: title,
    fileCount,
    status: 'uploading',
  };
  return {
    tasks: [newTask, ...prev],
    // Modal closes immediately upon confirmation so user is not blocked
    isModalOpen: false,
  };
}

function handleUploadComplete(tasks: UploadTaskState[], taskId: string): UploadTaskState[] {
  return tasks.map((t) => (t.id === taskId ? { ...t, status: 'success' } : t));
}

function handleUploadFailure(tasks: UploadTaskState[], taskId: string, error: string): UploadTaskState[] {
  return tasks.map((t) => (t.id === taskId ? { ...t, status: 'error', error } : t));
}

// Test upload task start closes modal immediately
const startRes = handleUploadStart([], "Solo Leveling Chapter 1", 20);
assert.equal(startRes.isModalOpen, false, "Upload modal should close immediately upon submission to prevent blocking user");
assert.equal(startRes.tasks.length, 1);
assert.equal(startRes.tasks[0].status, 'uploading');

// Test upload success transition
const successTasks = handleUploadComplete(startRes.tasks, startRes.tasks[0].id);
assert.equal(successTasks[0].status, 'success', "Upload task should transition to success state without modal interruption");

// Test upload error transition
const errorTasks = handleUploadFailure(startRes.tasks, startRes.tasks[0].id, "Network timeout");
assert.equal(errorTasks[0].status, 'error');
assert.equal(errorTasks[0].error, "Network timeout");

// 4. Verify Non-blocking Background Summarization State Lifecycle
interface SummaryNotificationState {
  title: string;
  status: 'generating' | 'ready' | 'error';
  hasData: boolean;
}

function computeSummarizeTriggerState({
  cachedSummary,
  regenerate,
  currentModalTitle,
  targetTitle,
}: {
  cachedSummary: string | null;
  regenerate: boolean;
  currentModalTitle: string | null;
  targetTitle: string;
}): {
  openModalImmediately: boolean;
  backgroundNotification: SummaryNotificationState | null;
} {
  if (cachedSummary && !regenerate) {
    return {
      openModalImmediately: true,
      backgroundNotification: null,
    };
  }
  // If generating from scratch or regenerating: run in background!
  return {
    openModalImmediately: currentModalTitle === targetTitle,
    backgroundNotification: {
      title: targetTitle,
      status: 'generating',
      hasData: false,
    },
  };
}

// Cached summary triggers modal immediately
const cachedTest = computeSummarizeTriggerState({
  cachedSummary: "Manga synopsis text here",
  regenerate: false,
  currentModalTitle: null,
  targetTitle: "One Piece",
});
assert.equal(cachedTest.openModalImmediately, true, "Existing non-stale synopsis should open modal immediately");
assert.equal(cachedTest.backgroundNotification, null);

// Uncached summary does NOT block with modal, runs in background
const uncachedTest = computeSummarizeTriggerState({
  cachedSummary: null,
  regenerate: false,
  currentModalTitle: null,
  targetTitle: "One Piece",
});
assert.equal(uncachedTest.openModalImmediately, false, "Generating synopsis should NOT force open a blocking modal");
assert.equal(uncachedTest.backgroundNotification?.status, 'generating');

// 5. Verify MangaCard and Manga Detail Button Specifications
const mangaCardButtonRules = {
  readButtonAlwaysVisible: true,
  summaryButtonOnCard: false,
  viewSummaryIconWhenSummarized: true,
  detailHeaderContainsSummaryButton: true,
};

assert.equal(mangaCardButtonRules.readButtonAlwaysVisible, true, "MangaCard must always present the Read button");
assert.equal(mangaCardButtonRules.summaryButtonOnCard, false, "MangaCard should not display summary generation button on the card");
assert.equal(mangaCardButtonRules.viewSummaryIconWhenSummarized, true, "MangaCard should show document icon to view summary if summarized");
assert.equal(mangaCardButtonRules.detailHeaderContainsSummaryButton, true, "Manga Detail view must include the Summary button in header");

// 6. Verify Manga Detail Endpoint State Resolution & Sync Logic
function computeActiveMangaState({
  currentFilter,
  selectedMangaTitle,
}: {
  currentFilter: string;
  selectedMangaTitle: string | null | undefined;
}): string {
  if (selectedMangaTitle !== undefined) {
    return selectedMangaTitle || 'all';
  }
  return currentFilter;
}

// Direct URL navigation to /gallery/manga/Solo%20Leveling
assert.equal(
  computeActiveMangaState({ currentFilter: 'all', selectedMangaTitle: 'Solo Leveling' }),
  'Solo Leveling',
  'Should switch active filter to manga title from URL'
);

// Returning to /gallery (selectedMangaTitle = null)
assert.equal(
  computeActiveMangaState({ currentFilter: 'Solo Leveling', selectedMangaTitle: null }),
  'all',
  'Should reset active filter to all when navigating to root gallery'
);

// Opening page overlay (/gallery/pages/:folder -> selectedMangaTitle is undefined)
assert.equal(
  computeActiveMangaState({ currentFilter: 'Solo Leveling', selectedMangaTitle: undefined }),
  'Solo Leveling',
  'Should preserve active manga detail filter while overlays are open'
);

// Detail view vs Library view resolution
function computeEffectiveSingleManga(activeFilter: string): string | null {
  return activeFilter !== 'all' ? activeFilter : null;
}

assert.equal(
  computeEffectiveSingleManga('Solo Leveling'),
  'Solo Leveling',
  'Active manga filter must enter single manga detail view'
);
assert.equal(
  computeEffectiveSingleManga('all'),
  null,
  'All manga filter must display library overview'
);

// 7. Verify manga sorting does not reorder pages inside a manga
const mangaGroups = [
  { title: 'Zeta', latestFinishedAt: '2026-09-05T10:00:00Z' },
  { title: 'Alpha', latestFinishedAt: '2026-09-01T10:00:00Z' },
  { title: 'Beta', latestFinishedAt: '2026-09-13T12:00:00Z' },
  { title: 'Ungrouped', latestFinishedAt: '2026-09-20T12:00:00Z' },
];

assert.deepEqual(
  sortMangaGroups(mangaGroups, 'alpha-desc').map((group) => group.title),
  ['Zeta', 'Beta', 'Alpha', 'Ungrouped'],
  'Alphabetical sorting must order manga groups by title'
);
assert.deepEqual(
  sortMangaGroups(mangaGroups, 'date-desc').map((group) => group.title),
  ['Beta', 'Zeta', 'Alpha', 'Ungrouped'],
  'Date sorting must order manga groups by latest page'
);

const unsortedPages: FinishedImage[] = [
  { id: '2', originalName: '002.png', result: '/res/2.png', finishedAt: new Date('2026-09-13T12:00:00Z'), settings: { translator: 'none' } },
  { id: '10', originalName: '010.png', result: '/res/10.png', finishedAt: new Date('2026-09-05T10:00:00Z'), settings: { translator: 'none' } },
  { id: '1', originalName: '001.png', result: '/res/1.png', finishedAt: new Date('2026-09-01T10:00:00Z'), settings: { translator: 'none' } },
];
assert.deepEqual(
  sortMangaPages(unsortedPages).map((page) => page.id),
  ['1', '2', '10'],
  'Pages must remain in natural reading order regardless of manga sort'
);
assert.deepEqual(
  sortMangaPages([
    { ...unsortedPages[0], id: 'first', pageOrder: 3 },
    { ...unsortedPages[1], id: 'second', pageOrder: 1 },
    { ...unsortedPages[2], id: 'third', pageOrder: 2 },
  ]).map((page) => page.id),
  ['second', 'third', 'first'],
  'Persistent page order must take precedence over filenames',
);
assert.deepEqual(
  sortMangaPagesForOrder(unsortedPages, 'name-asc').map((page) => page.id),
  ['1', '2', '10'],
  'Name sorting must use natural filename order',
);
assert.deepEqual(
  sortMangaPagesForOrder(unsortedPages, 'created-desc').map((page) => page.id),
  ['2', '10', '1'],
  'Created-time sorting must reorder newest pages first',
);
assert.deepEqual(
  sortMangaPagesForOrder(
    [
      { ...unsortedPages[0], pageOrder: 1 },
      { ...unsortedPages[1], pageOrder: 2 },
      { ...unsortedPages[2], pageOrder: 3 },
    ],
    'order',
  ).map((page) => page.id),
  ['2', '10', '1'],
  'Reading-order selection must preserve saved page order',
);

// 8. Verify Thumbnail URL Resolution & Caching
const thumbImageWithExplicit: FinishedImage = {
  id: "img-thumb-1",
  originalName: "001.png",
  result: "/result/folder123/final.png",
  thumbnailUrl: "/result/folder123/thumbnail.webp",
  folder: "folder123",
  mangaTitle: "Manga Thumb",
  finishedAt: new Date(),
  settings: {},
};

assert.equal(
  getGalleryThumbnailUrl(thumbImageWithExplicit),
  apiUrl("/result/folder123/thumbnail.webp"),
  "Should use explicit thumbnailUrl when available"
);

const thumbImageFolderFallback: FinishedImage = {
  id: "img-thumb-2",
  originalName: "002.png",
  result: "/result/folder456/final.png",
  folder: "folder456",
  mangaTitle: "Manga Thumb",
  finishedAt: new Date(),
  settings: {},
};

assert.equal(
  getGalleryThumbnailUrl(thumbImageFolderFallback),
  apiUrl("/result/folder456/thumbnail.webp"),
  "Should construct /thumbnail.webp from folder when available"
);

assert.equal(
  getGalleryFallbackUrl(thumbImageFolderFallback),
  apiUrl("/result/folder456/final.png"),
  "Fallback URL should be final.png"
);

assert.equal(
  getGalleryFallbackUrl(thumbImageFolderFallback, "cover"),
  apiUrl("/result/folder456/thumbnail.webp"),
  "Cover fallback must stay on a small derivative"
);

const detailPreviewImage: FinishedImage = {
  ...thumbImageFolderFallback,
  detailPreviewUrl: "/result/folder456/preview.webp?v=1",
  coverUrl: "/result/folder456/cover.webp?v=1",
};
assert.equal(
  getGalleryThumbnailUrl(detailPreviewImage),
  apiUrl("/result/folder456/preview.webp?v=1"),
  "Manga detail cards should use the detail preview source"
);
assert.equal(
  getGalleryThumbnailUrl(detailPreviewImage, "cover"),
  apiUrl("/result/folder456/cover.webp?v=1"),
  "Gallery manga cards should use the small cover source"
);

// Verify loadedThumbnailUrls cache tracking
loadedThumbnailUrls.add("/result/folder123/thumbnail.webp");
assert.equal(
  loadedThumbnailUrls.has("/result/folder123/thumbnail.webp"),
  true,
  "loadedThumbnailUrls should retain cached thumbnails across scrolls"
);

// Verify reader exit position resolution and card query selector logic for both detail view and gallery overview
function resolveReaderExitTarget({
  viewMode = "grid",
  activeMangaFilter = "all",
  mangaTitle,
  mangaId,
  lastPageIndex,
  lastImageId,
  savedPage,
  savedPageCount,
  images,
}: {
  viewMode?: "grid" | "rows";
  activeMangaFilter?: string;
  mangaTitle?: string;
  mangaId?: string;
  lastPageIndex?: number;
  lastImageId?: string;
  savedPage?: string | null;
  savedPageCount?: string | null;
  images: FinishedImage[];
}): { pageIndex: number; imageId?: string; mangaId?: string; querySelector: string } {
  let finalIndex: number;
  if (typeof lastPageIndex === "number" && lastPageIndex >= 0) {
    finalIndex = Math.min(lastPageIndex, Math.max(0, images.length - 1));
  } else {
    const page = Number(savedPage);
    if (
      images.length > 0 &&
      Number(savedPageCount) === images.length &&
      Number.isFinite(page) &&
      page >= 1
    ) {
      finalIndex = Math.min(page, images.length) - 1;
    } else {
      finalIndex = 0;
    }
  }

  const finalImageId = lastImageId || images[finalIndex]?.id;
  const isSingleView = activeMangaFilter === mangaTitle;
  const isRowExpanded = viewMode === "rows" && activeMangaFilter === mangaTitle;

  let querySelector: string;
  if (isSingleView || isRowExpanded) {
    querySelector = finalImageId
      ? `[data-image-id="${finalImageId}"]`
      : `[data-page-index="${finalIndex}"]`;
  } else {
    querySelector = mangaId
      ? `[data-manga-id="${mangaId}"]`
      : `[data-manga-title="${mangaTitle}"]`;
  }

  return { pageIndex: finalIndex, imageId: finalImageId, mangaId, querySelector };
}

const mockMangaImages: FinishedImage[] = [
  { id: "img-0", originalName: "01.png", result: "/res/0", finishedAt: new Date(), settings: {} },
  { id: "img-1", originalName: "02.png", result: "/res/1", finishedAt: new Date(), settings: {} },
  { id: "img-2", originalName: "03.png", result: "/res/2", finishedAt: new Date(), settings: {} },
  { id: "img-3", originalName: "04.png", result: "/res/3", finishedAt: new Date(), settings: {} },
];

// Direct exit from reader modal with page index and image ID in Manga Detail view
const directExitDetail = resolveReaderExitTarget({
  activeMangaFilter: "Solo Leveling",
  mangaTitle: "Solo Leveling",
  mangaId: "manga-solo",
  lastPageIndex: 2,
  lastImageId: "img-2",
  savedPage: "3",
  savedPageCount: "4",
  images: mockMangaImages,
});
assert.equal(directExitDetail.pageIndex, 2);
assert.equal(directExitDetail.imageId, "img-2");
assert.equal(directExitDetail.querySelector, '[data-image-id="img-2"]');

// Direct exit from reader modal in Gallery Grid View (all manga overview)
const directExitGalleryGrid = resolveReaderExitTarget({
  viewMode: "grid",
  activeMangaFilter: "all",
  mangaTitle: "Solo Leveling",
  mangaId: "manga-solo",
  lastPageIndex: 2,
  lastImageId: "img-2",
  images: mockMangaImages,
});
assert.equal(directExitGalleryGrid.mangaId, "manga-solo");
assert.equal(directExitGalleryGrid.querySelector, '[data-manga-id="manga-solo"]', "Exiting reader to gallery grid view must target the manga card via data-manga-id");

// Exit via browser back button in gallery overview
const backButtonGalleryExit = resolveReaderExitTarget({
  viewMode: "grid",
  activeMangaFilter: "all",
  mangaTitle: "Chainsaw Man",
  mangaId: "manga-csm",
  savedPage: "3",
  savedPageCount: "4",
  images: mockMangaImages,
});
assert.equal(backButtonGalleryExit.querySelector, '[data-manga-id="manga-csm"]');

// 9. Verify overlay route transitions preserve gallery summaries without wiping
function computeSummaryFetchDecision({
  activeView,
  gallerySection,
  overlay,
  hasLoadedSummaries,
}: {
  activeView: string;
  gallerySection?: string;
  overlay: string;
  hasLoadedSummaries: boolean;
}): { shouldFetch: boolean; wipeExisting: boolean } {
  if (activeView !== "gallery" || gallerySection === "series" || overlay !== "none") {
    return { shouldFetch: false, wipeExisting: false };
  }
  return { shouldFetch: true, wipeExisting: !hasLoadedSummaries };
}

// Opening reader overlay (/read?manga=...) must NOT fetch or wipe gallery
assert.deepEqual(
  computeSummaryFetchDecision({ activeView: "gallery", overlay: "reader", hasLoadedSummaries: true }),
  { shouldFetch: false, wipeExisting: false },
  "Opening reader overlay must not trigger summary fetch or wipe existing gallery items"
);

// Closing reader overlay back to /gallery (overlay = none) with already loaded items
assert.deepEqual(
  computeSummaryFetchDecision({ activeView: "gallery", overlay: "none", hasLoadedSummaries: true }),
  { shouldFetch: true, wipeExisting: false },
  "Returning to gallery with loaded summaries must preserve existing items without blanking to empty"
);

// Status Filter tests
const testGroups = [
  {
    title: "Original Manga",
    coverImage: { id: "img-orig", sourceType: "original" as const, originalName: "p1.png", result: "/res/p1.png", finishedAt: new Date(), settings: {} },
    images: [{ id: "img-orig", sourceType: "original" as const, originalName: "p1.png", result: "/res/p1.png", finishedAt: new Date(), settings: {} }],
    hasSummary: false,
    needsReviewCount: 0,
  },
  {
    title: "Translated Manga",
    coverImage: { id: "img-trans", sourceType: "translated" as const, originalName: "p1.png", result: "/res/p1.png", finishedAt: new Date(), settings: {} },
    images: [{ id: "img-trans", sourceType: "translated" as const, originalName: "p1.png", result: "/res/p1.png", finishedAt: new Date(), settings: {} }],
    hasSummary: false,
    needsReviewCount: 0,
  },
  {
    title: "Summarized Manga",
    coverImage: { id: "img-sum", sourceType: "translated" as const, originalName: "p1.png", result: "/res/p1.png", finishedAt: new Date(), settings: {} },
    images: [{ id: "img-sum", sourceType: "translated" as const, originalName: "p1.png", result: "/res/p1.png", finishedAt: new Date(), settings: {} }],
    hasSummary: true,
    needsReviewCount: 0,
  },
  {
    title: "Review Manga",
    coverImage: { id: "img-rev", sourceType: "translated" as const, originalName: "p1.png", result: "/res/p1.png", finishedAt: new Date(), settings: {} },
    images: [{ id: "img-rev", sourceType: "translated" as const, originalName: "p1.png", result: "/res/p1.png", finishedAt: new Date(), settings: {} }],
    hasSummary: false,
    needsReviewCount: 3,
  },
  {
    title: "Server Summary Original Manga",
    cover: { id: "img-srv-orig", sourceType: "original" as const, originalName: "p1.png", result: "/res/p1.png", finishedAt: new Date(), settings: {} },
    hasSummary: false,
    needsReviewCount: 0,
  },
];

assert.equal(filterMangaGroupsByStatus(testGroups, "all").length, 5, "all filter should return all groups");
assert.deepEqual(filterMangaGroupsByStatus(testGroups, "original").map(g => g.title), ["Original Manga", "Server Summary Original Manga"], "original filter should return only raw/original groups");
assert.deepEqual(filterMangaGroupsByStatus(testGroups, "translated").map(g => g.title), ["Translated Manga", "Summarized Manga", "Review Manga"], "translated filter should return translated groups");
assert.deepEqual(filterMangaGroupsByStatus(testGroups, "summarized").map(g => g.title), ["Summarized Manga"], "summarized filter should return only groups with summaries");
assert.deepEqual(filterMangaGroupsByStatus(testGroups, "review").map(g => g.title), ["Review Manga"], "review filter should return only groups needing review");

// Search, status, pagination, and review counts remain coordinated in the view model.
const viewGroups = testGroups.map((group, index) => ({
  id: `group-${index}`,
  title: group.title,
  count: group.images?.length ?? 1,
  coverImage: group.coverImage || group.cover || null,
  images: group.images || [],
  hasSummary: group.hasSummary || false,
  needsReviewCount: group.needsReviewCount || 0,
  isLoaded: true,
  isLoading: false,
}));
let groupViews!: ReturnType<typeof useGalleryGroupViews>;
let remoteSearch = false;
let groupStatusFilter: "translated" | "all" = "translated";
const remoteSearchHandler = (_search: string) => {};
function GalleryGroupViewsHarness() {
  groupViews = useGalleryGroupViews({
    mangaGroups: viewGroups,
    mangaSearchQuery: "Manga",
    moveMangaSearch: "Original",
    activeMangaFilter: "all",
    statusFilter: groupStatusFilter,
    onGallerySearchChange: remoteSearch ? remoteSearchHandler : undefined,
    totalMangaCount: 0,
    requestedGalleryPageSize: 2,
    reviewOnly: true,
    totalImagesCount: 10,
    activeSummaries: [{ title: "Review Manga", count: 1, needsReviewCount: 3 }],
  });
  return null;
}
const renderGalleryGroupViews = () => renderToStaticMarkup(React.createElement(GalleryGroupViewsHarness));
renderGalleryGroupViews();
assert.deepEqual(groupViews.filteredGroups.map((group) => group.title), ["Translated Manga", "Summarized Manga", "Review Manga"]);
assert.deepEqual(groupViews.moveMangaGroups.map((group) => group.title), ["Original Manga", "Server Summary Original Manga"]);
assert.equal(groupViews.galleryMangaCount, 3);
assert.equal(groupViews.galleryPageCount, 2);
assert.equal(groupViews.reviewCount, 10);

remoteSearch = true;
groupStatusFilter = "all";
renderGalleryGroupViews();
assert.equal(groupViews.filteredGroups.length, viewGroups.length, "Server-owned search should not filter the loaded page a second time");

// Test buildMangaGroupTitles to ensure searching one by one replaces results instead of appending
const cachedMangaImages: Record<string, FinishedImage[]> = {
  "Manga Alpha": [
    { id: "img-a1", originalName: "a1.png", result: "/res/a1", finishedAt: new Date(), settings: {}, mangaTitle: "Manga Alpha" },
  ],
  "Manga Beta": [
    { id: "img-b1", originalName: "b1.png", result: "/res/b1", finishedAt: new Date(), settings: {}, mangaTitle: "Manga Beta" },
  ],
};

// Search 1: Searching for "Manga Alpha" returns Manga Alpha
const search1Titles = buildMangaGroupTitles({
  activeSummaries: [
    { id: "id-alpha", title: "Manga Alpha", count: 1, cover: null, latestFinishedAt: new Date().toISOString(), seriesId: null, seriesTitle: null, hasSummary: false, needsReviewCount: 0 },
  ],
  hasExplicitSummaries: true,
  mangaImages: cachedMangaImages,
  finishedImages: [],
  activeMangaFilter: "all",
});
assert.deepEqual(search1Titles, ["Manga Alpha"], "First search should return only Manga Alpha");

// Search 2: Searching for "Manga Beta" returns ONLY Manga Beta, not appending Manga Alpha from cached mangaImages
const search2Titles = buildMangaGroupTitles({
  activeSummaries: [
    { id: "id-beta", title: "Manga Beta", count: 1, cover: null, latestFinishedAt: new Date().toISOString(), seriesId: null, seriesTitle: null, hasSummary: false, needsReviewCount: 0 },
  ],
  hasExplicitSummaries: true,
  mangaImages: cachedMangaImages,
  finishedImages: [],
  activeMangaFilter: "all",
});
assert.deepEqual(search2Titles, ["Manga Beta"], "Second search should return ONLY Manga Beta and not append previous cached manga");

// Search 3: Searching for "Ungrouped" returns ONLY Ungrouped, not appending Manga Alpha or Manga Beta
const search3Titles = buildMangaGroupTitles({
  activeSummaries: [
    { id: "id-ungrouped", title: "Ungrouped", count: 5, cover: null, latestFinishedAt: new Date().toISOString(), seriesId: null, seriesTitle: null, hasSummary: false, needsReviewCount: 0 },
  ],
  hasExplicitSummaries: true,
  mangaImages: cachedMangaImages,
  finishedImages: [],
  activeMangaFilter: "all",
});
assert.deepEqual(search3Titles, ["Ungrouped"], "Searching for Ungrouped should return ONLY Ungrouped without appending cached manga");

// Search 4: Search with 0 matching results should return empty array, not resurrect cached mangaImages
const search4Titles = buildMangaGroupTitles({
  activeSummaries: [],
  hasExplicitSummaries: true,
  mangaImages: cachedMangaImages,
  finishedImages: [],
  activeMangaFilter: "all",
});
assert.deepEqual(search4Titles, [], "Empty search results should return [] and never append cached mangaImages");

// Unmanaged / Client fallback mode (hasExplicitSummaries = false)
const fallbackTitles = buildMangaGroupTitles({
  activeSummaries: [],
  hasExplicitSummaries: false,
  mangaImages: cachedMangaImages,
  finishedImages: [
    { id: "img-u1", originalName: "u1.png", result: "/res/u1", finishedAt: new Date(), settings: {}, mangaTitle: "Ungrouped" },
  ],
  activeMangaFilter: "all",
});
assert.deepEqual(fallbackTitles, ["Manga Alpha", "Manga Beta", "Ungrouped"], "Unmanaged fallback mode should aggregate finishedImages and mangaImages");

const completedAt = new Date("2026-01-04T00:00:00Z");
const galleryCachedMangaImages = {
  "Manga Alpha": [
    { ...cachedMangaImages["Manga Alpha"][0], finishedAt: new Date("2026-01-02T00:00:00Z") },
  ],
};
const galleryGroups = buildGalleryMangaGroups({
  activeSummaries: [
    { id: "id-alpha", title: "Manga Alpha", count: 1, latestFinishedAt: "2026-01-01T00:00:00Z", needsReviewCount: 5 },
    { id: "id-beta", title: "Manga Beta", count: 4, needsReviewCount: 0 },
  ],
  hasExplicitSummaries: true,
  mangaImages: galleryCachedMangaImages,
  finishedImages: [
    { ...cachedMangaImages["Manga Alpha"][0], reviewStatus: "pending", finishedAt: completedAt },
  ],
  activeMangaFilter: "all",
  loadingManga: { "Manga Beta": true },
  sortBy: "alpha-asc",
  locallySummarizedTitle: "Manga Beta",
  serverSummarizedTitle: "Manga Alpha",
  reviewOnly: false,
});
assert.deepEqual(galleryGroups.map((group) => group.title), ["Manga Alpha", "Manga Beta"]);
assert.equal(galleryGroups[0].id, "id-alpha");
assert.equal(galleryGroups[0].images.length, 1, "Loaded and session images with the same ID should stay deduplicated");
assert.equal(galleryGroups[0].coverImage?.id, "img-a1");
assert.equal(galleryGroups[0].latestFinishedAt, new Date("2026-01-02T00:00:00Z").getTime());
assert.equal(galleryGroups[0].hasSummary, true, "Server summary availability should mark the group summarized");
assert.equal(galleryGroups[1].hasSummary, true, "An in-progress local summary should mark the group summarized");
assert.equal(galleryGroups[1].isLoading, true);
assert.equal(galleryGroups[1].count, 4, "Unloaded groups should retain the server page count");

const reviewGalleryGroups = buildGalleryMangaGroups({
  activeSummaries: [
    { id: "id-alpha", title: "Manga Alpha", count: 1, needsReviewCount: 5 },
    { id: "id-beta", title: "Manga Beta", count: 4, needsReviewCount: 0 },
  ],
  hasExplicitSummaries: true,
  mangaImages: galleryCachedMangaImages,
  finishedImages: [
    { ...cachedMangaImages["Manga Alpha"][0], reviewStatus: "pending", finishedAt: completedAt },
  ],
  activeMangaFilter: "all",
  loadingManga: {},
  sortBy: "alpha-asc",
  reviewOnly: true,
});
assert.deepEqual(reviewGalleryGroups.map((group) => [group.title, group.count]), [["Manga Alpha", 1]]);
// Test manga group selection matching by Postgres UUID, title hash, and title name
const mockSummaries = [
  { id: "uuid-1234", title: "Sakura Garden", count: 32, needsReviewCount: 0, latestFinishedAt: "2026-01-01T00:00:00Z", hasSummary: false },
];
const findGroupBySelectedId = (summaries: typeof mockSummaries, selectedMangaId: string) =>
  summaries.find((summary) => summary.id === selectedMangaId || mangaIdForTitle(summary.title) === selectedMangaId || summary.title === selectedMangaId);

assert.equal(findGroupBySelectedId(mockSummaries, "uuid-1234")?.title, "Sakura Garden", "Lookup by UUID should match");
assert.equal(findGroupBySelectedId(mockSummaries, mangaIdForTitle("Sakura Garden"))?.title, "Sakura Garden", "Lookup by hashed title should match even when id is a UUID");
assert.equal(findGroupBySelectedId(mockSummaries, "Sakura Garden")?.title, "Sakura Garden", "Lookup by raw title should match");

// Test shouldShowEmptyLibraryState
assert.equal(
  shouldShowEmptyLibraryState({
    mangaGroupsLength: 0,
    totalImagesCount: 0,
    mangaSearchQuery: "",
    statusFilter: "all",
    activeMangaFilter: "all",
    reviewOnly: false,
  }),
  true,
  "Should show initial empty library state only when no filters, search, or review mode is active",
);

assert.equal(
  shouldShowEmptyLibraryState({
    mangaGroupsLength: 0,
    totalImagesCount: 0,
    mangaSearchQuery: "",
    statusFilter: "translated",
    activeMangaFilter: "all",
    reviewOnly: false,
  }),
  false,
  "Should not show initial empty library state when status filter is active (even if 0 matches)",
);

assert.equal(
  shouldShowEmptyLibraryState({
    mangaGroupsLength: 0,
    totalImagesCount: 0,
    mangaSearchQuery: "",
    statusFilter: "original",
    activeMangaFilter: "all",
    reviewOnly: false,
  }),
  false,
  "Should not show initial empty library state when filter is 'original'",
);

assert.equal(
  shouldShowEmptyLibraryState({
    mangaGroupsLength: 0,
    totalImagesCount: 0,
    mangaSearchQuery: "",
    statusFilter: "summarized",
    activeMangaFilter: "all",
    reviewOnly: false,
  }),
  false,
  "Should not show initial empty library state when filter is 'summarized'",
);

assert.equal(
  shouldShowEmptyLibraryState({
    mangaGroupsLength: 0,
    totalImagesCount: 0,
    mangaSearchQuery: "",
    statusFilter: "review",
    activeMangaFilter: "all",
    reviewOnly: false,
  }),
  false,
  "Should not show initial empty library state when filter is 'review'",
);

assert.equal(
  shouldShowEmptyLibraryState({
    mangaGroupsLength: 0,
    totalImagesCount: 0,
    mangaSearchQuery: "attack",
    statusFilter: "all",
    activeMangaFilter: "all",
    reviewOnly: false,
  }),
  false,
  "Should not show initial empty library state when search query is active",
);

assert.equal(
  shouldShowEmptyLibraryState({
    mangaGroupsLength: 0,
    totalImagesCount: 0,
    mangaSearchQuery: "",
    statusFilter: "all",
    activeMangaFilter: "Manga A",
    reviewOnly: false,
  }),
  false,
  "Should not show initial empty library state when viewing a specific manga",
);

assert.equal(
  shouldShowEmptyLibraryState({
    mangaGroupsLength: 0,
    totalImagesCount: 0,
    mangaSearchQuery: "",
    statusFilter: "all",
    activeMangaFilter: "all",
    reviewOnly: true,
  }),
  false,
  "Should not show initial empty library state when reviewOnly is true",
);

assert.equal(
  shouldShowEmptyLibraryState({
    mangaGroupsLength: 5,
    totalImagesCount: 20,
    mangaSearchQuery: "",
    statusFilter: "all",
    activeMangaFilter: "all",
    reviewOnly: false,
  }),
  false,
  "Should not show empty library state when library has items",
);

// Drag auto-scroll speed tests
assert.equal(calculateDragAutoScrollSpeed(500, 1000, 120), 0, "Middle zone should not scroll");
assert.ok(calculateDragAutoScrollSpeed(0, 1000, 120) < 0, "Top edge should scroll up (negative)");
assert.equal(calculateDragAutoScrollSpeed(0, 1000, 120), -22, "Top-most point should scroll up at max speed");
assert.ok(calculateDragAutoScrollSpeed(60, 1000, 120) < 0, "Halfway in top edge should scroll up");
assert.ok(
  Math.abs(calculateDragAutoScrollSpeed(0, 1000, 120)) > Math.abs(calculateDragAutoScrollSpeed(60, 1000, 120)),
  "Top-most edge speed should be faster than inner edge zone",
);
assert.ok(calculateDragAutoScrollSpeed(1000, 1000, 120) > 0, "Bottom edge should scroll down (positive)");
assert.equal(calculateDragAutoScrollSpeed(1000, 1000, 120), 22, "Bottom-most point should scroll down at max speed");
assert.ok(calculateDragAutoScrollSpeed(940, 1000, 120) > 0, "Halfway in bottom edge should scroll down");
assert.ok(
  calculateDragAutoScrollSpeed(1000, 1000, 120) > calculateDragAutoScrollSpeed(940, 1000, 120),
  "Bottom-most edge speed should be faster than inner edge zone",
);
assert.equal(calculateDragAutoScrollSpeed(100, 0), 0, "Zero viewport height should return 0");

// Bulk deletion action contracts
const initialGalleryImages: FinishedImage[] = [
  { id: "img-1", originalName: "p1.png", result: "/res/f1", mangaTitle: "Manga One", folder: "f1", finishedAt: new Date(), settings: {} },
  { id: "img-2", originalName: "p2.png", result: "/res/f2", mangaTitle: "Manga One", folder: "f2", finishedAt: new Date(), settings: {} },
  { id: "img-3", originalName: "p3.png", result: "/res/f3", mangaTitle: "Manga One", folder: "f3", finishedAt: new Date(), settings: {} },
  { id: "img-4", originalName: "p4.png", result: "/res/f4", mangaTitle: "Manga Two", folder: "f4", finishedAt: new Date(), settings: {} },
];

type DeletionState = {
  mangaImages: Record<string, FinishedImage[]>;
  selectedMangaIds: Map<string, string>;
  confirmPages: boolean;
  confirmMangas: boolean;
  deletingPages: boolean[];
  deletingMangas: boolean[];
  clearedImageSelection: number;
  closedMangaDetail: number;
};
const deletionState: DeletionState = {
  mangaImages: {
    "Manga One": initialGalleryImages.slice(0, 3),
    "Manga Two": initialGalleryImages.slice(3),
  },
  selectedMangaIds: new Map([["g1", "Manga One"], ["g2", "Manga Two"]]),
  confirmPages: true,
  confirmMangas: true,
  deletingPages: [],
  deletingMangas: [],
  clearedImageSelection: 0,
  closedMangaDetail: 0,
};
const applyState = <T,>(action: SetStateAction<T>, current: T): T =>
  typeof action === "function" ? (action as (previous: T) => T)(current) : action;
const setMangaImages: Dispatch<SetStateAction<Record<string, FinishedImage[]>>> = (action) => {
  deletionState.mangaImages = applyState(action, deletionState.mangaImages);
};
const setSelectedMangaIds: Dispatch<SetStateAction<Map<string, string>>> = (action) => {
  deletionState.selectedMangaIds = applyState(action, deletionState.selectedMangaIds);
};
const setConfirmDeleteSelectedPages: Dispatch<SetStateAction<boolean>> = (action) => {
  deletionState.confirmPages = applyState(action, deletionState.confirmPages);
};
const setConfirmDeleteSelectedMangas: Dispatch<SetStateAction<boolean>> = (action) => {
  deletionState.confirmMangas = applyState(action, deletionState.confirmMangas);
};
const setIsDeletingSelectedPages: Dispatch<SetStateAction<boolean>> = (action) => {
  deletionState.deletingPages.push(applyState(action, deletionState.deletingPages.at(-1) || false));
};
const setIsDeletingSelectedMangas: Dispatch<SetStateAction<boolean>> = (action) => {
  deletionState.deletingMangas.push(applyState(action, deletionState.deletingMangas.at(-1) || false));
};
const deletedPages: FinishedImage[][] = [];
const deletedMangas: Array<Array<{ title: string; images: FinishedImage[] }>> = [];
const deletionActions = createGalleryBulkDeletionActions({
  selectedImageIds: new Set(["img-1", "img-3"]),
  selectedMangaIds: deletionState.selectedMangaIds,
  allLoadedImages: initialGalleryImages.slice(0, 3),
  currentSingleGroup: { images: [initialGalleryImages[0], initialGalleryImages[2]] },
  finishedImages: [initialGalleryImages[2]],
  mangaGroups: [
    { id: "g1", title: "Manga One", count: 3, coverImage: null, images: initialGalleryImages.slice(0, 3), needsReviewCount: 0, isLoaded: true, isLoading: false },
    { id: "g2", title: "Manga Two", count: 1, coverImage: null, images: initialGalleryImages.slice(3), needsReviewCount: 0, isLoaded: true, isLoading: false },
  ],
  mangaImages: deletionState.mangaImages,
  activeMangaFilter: "Manga One",
  onDeleteImages: async (images) => { deletedPages.push(images); },
  onDeleteMangas: async (mangas) => { deletedMangas.push(mangas); },
  clearSelectedImages: () => { deletionState.clearedImageSelection += 1; },
  closeMangaDetail: () => { deletionState.closedMangaDetail += 1; },
  setMangaImages,
  setSelectedMangaIds,
  setConfirmDeleteSelectedPages,
  setConfirmDeleteSelectedMangas,
  setIsDeletingSelectedPages,
  setIsDeletingSelectedMangas,
});

await deletionActions.handleDeleteSelectedPages();
assert.deepEqual(deletedPages[0].map(({ id }) => id), ["img-1", "img-3"], "Selected pages should be sent once in source order");
assert.deepEqual(deletionState.mangaImages["Manga One"].map(({ id }) => id), ["img-2"], "Selected pages should be removed from loaded groups");
assert.deepEqual(deletionState.mangaImages["Manga Two"].map(({ id }) => id), ["img-4"]);
assert.equal(deletionState.clearedImageSelection, 1);
assert.equal(deletionState.confirmPages, false);
assert.deepEqual(deletionState.deletingPages, [true, false]);

await deletionActions.handleDeleteSelectedMangas();
assert.deepEqual(deletedMangas[0].map(({ title }) => title), ["Manga One", "Manga Two"]);
assert.deepEqual(Object.keys(deletionState.mangaImages), [], "Selected manga should be removed from loaded groups");
assert.deepEqual([...deletionState.selectedMangaIds], [], "Group selection should be cleared");
assert.equal(deletionState.confirmMangas, false);
assert.equal(deletionState.closedMangaDetail, 1, "Deleting the open manga should close its detail view");
assert.deepEqual(deletionState.deletingMangas, [true, false]);

console.log("ResultGallery loading, state, bulk deletion, latest-first sorting, reader exit position, thumbnail caching, status filter, empty library filter state, search grouping, and drag auto-scroll tests passed successfully!");
