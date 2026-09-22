export type StatusKey =
  | "upload"
  | "pending"
  | "colorizing"
  | "detection"
  | "ocr"
  | "textline_merge"
  | "mask-generation"
  | "inpainting"
  | "upscaling"
  | "translating"
  | "rendering"
  | "skip-no-regions"
  | "finished"
  | "error"
  | "error-upload"
  | "error-lang"
  | "error-translating"
  | "error-too-large"
  | "error-disconnect"
  | null;

export interface ChunkProcessingResult {
  updatedBuffer: Uint8Array<ArrayBufferLike>;
}

export const processingStatuses = [
  "upload",
  "pending",
  "colorizing",
  "detection",
  "ocr",
  "textline_merge",
  "mask-generation",
  "inpainting",
  "upscaling",
  "translating",
  "rendering",
];

export type TranslatorKey =
  | "deepseek"
  | "gemini"
  | "openai"
  | "groq"
  | "openrouter"
  | "sugoi"
  | "custom_openai"
  | "sakura"
  | "deepl"
  | "youdao"
  | "baidu"
  | "caiyun"
  | "none";

export const validTranslators: TranslatorKey[] = [
  "deepseek",
  "gemini",
  "openai",
  "groq",
  "openrouter",
  "sugoi",
  "custom_openai",
  "sakura",
  "deepl",
  "youdao",
  "baidu",
  "caiyun",
  "none",
];  

export interface FileStatus {
  status: StatusKey | null;
  progress: string | null;
  queuePos: string | null;
  offlineModel?: string;
  geminiModel?: string;
  result: Blob | string | null;
  error: string | null;
  isAutoColorDetected?: boolean;
}

export interface StudioFile {
  id: string;
  file: File;
  sourcePath: string;
  addedAt: number;
  dropOrder: number;
  archiveId?: string;
  archiveName?: string;
  archivePageIndex?: number;
}

export interface PendingStudioFile {
  id: string;
  file: File;
  pages: StudioFile[];
}

export interface StorySegment {
  id: string;
  label?: string;
  startPage: number;
  endPage: number;
  archiveId?: string;
}

export interface StoryArchive {
  id: string;
  name: string;
  startPage: number;
  endPage: number;
  pageCount: number;
}

export interface StoryPlan {
  enabled: boolean;
  autoDetect: boolean;
  mergeAllPages: boolean;
  archives: StoryArchive[];
  segments: StorySegment[];
}

// New types for the improved UI
export interface QueuedImage {
  id: string;
  mangaGroupId?: string | null;
  pageId?: string | null;
  pageOrder?: number | null;
  sourcePath?: string | null;
  file: File;
  addedAt: Date;
  status: 'queued' | 'processing' | 'finished' | 'error';
  mangaTitle?: string;
  step?: string;
  offlineModel?: string;
  geminiModel?: string;
  result?: Blob | string;
  inputUrl?: string | null;
  resultUrl?: string | null;
  batchPreviewUrl?: string | null;
  coverUrl?: string | null;
  detailPreviewUrl?: string | null;
  readerUrl?: string | null;
  fullUrl?: string | null;
  folder?: string;
  error?: string;
  excludeColor?: boolean;
  needsReview?: boolean;
  isAutoColorDetected?: boolean;
}

export type TranslationBatchStatus =
  | "uploading"
  | "waiting"
  | "processing"
  | "stopping"
  | "paused"
  | "completed"
  | "error";

export type TranslationBatchKind = "translation" | "manga-upload" | "rerender";

export interface MangaGroupSelection {
  title: string;
  groupId?: string | null;
  isNewGroup?: boolean;
}

export interface TranslationBatch {
  id: string;
  kind?: TranslationBatchKind;
  addedAt: Date;
  updatedAt?: Date;
  mangaTitle: string;
  mangaGroupId?: string | null;
  isNewGroup?: boolean;
  settings: TranslationSettings;
  items: QueuedImage[];
  totalItems: number;
  completedCount: number;
  uploadProgress?: number;
  queuedCount?: number;
  processingCount?: number;
  failedCount?: number;
  needsReviewCount?: number;
  detailsLoaded?: boolean;
  status: TranslationBatchStatus;
  priority?: boolean;
  dismissed?: boolean;
}

export interface TranslationSettings {
  rememberSettings?: boolean;
  detectionResolution: string;
  textDetector: string;
  ocr?: string;
  renderTextDirection: string;
  letterCase?: "none" | "uppercase" | "lowercase";
  uppercase?: boolean;
  lowercase?: boolean;
  translator: TranslatorKey;
  summaryModel?: string;
  translatorModel?: string;
  offlineModel?: string;
  geminiModel?: string;
  targetLanguage: string;
  translationQuality?: "fast" | "professional";
  storyPlan?: StoryPlan;
  storyPageRanges?: string;
  inpaintingSize: string;
  customUnclipRatio: number;
  customBoxThreshold: number;
  customOcrProb?: number;
  ocrMinConfidence?: number;
  maskDilationOffset: number;
  bubbleDetection?: boolean;
  bubbleModel?: string;
  bubbleConfidence?: number;
  bubbleMaskThreshold?: number;
  inpainter: string;
  inpaintingPrecision?: string;
  kernelSize?: number;
  renderer?: string;
  renderAlignment?: string;
  renderFont?: string;
  useMocrMerge?: boolean;
  colorizer?: string;
  colorizationSize?: string;
  denoiseSigma?: number;
  colorThreshold?: number;
  upscaler?: string;
  upscaleRatio?: number | null;
  revertUpscaling?: boolean;
  colorTolerance?: number;
  colorizeOnly?: boolean;
  migratedDefaultTranslator?: boolean;
  migratedDefaultQwen2?: boolean;
  migratedDefaultSugoi?: boolean;
  migratedDefaultGemini?: boolean;
  migratedDefaultBubbleDetection?: boolean;
  migratedDefaultTargetLang?: boolean;
  keepFailedPagesForEditing?: boolean;
  translationBatchSize?: number;
}

export interface FinishedImage {
  id: string;
  groupId?: string | null;
  originalName: string;
  pageOrder?: number | null;
  sourcePath?: string | null;
  result: Blob | string;
  thumbnailUrl?: string | null;
  batchPreviewUrl?: string | null;
  coverUrl?: string | null;
  detailPreviewUrl?: string | null;
  readerUrl?: string | null;
  fullUrl?: string | null;
  sourceType?: 'original' | 'translated';
  inputUrl?: string | File | null;
  inpaintedUrl?: string | null;
  textRegionsUrl?: string | null;
  bubbleMaskUrl?: string | null;
  hasTextRegions?: boolean;
  reviewStatus?: 'pending' | 'approved' | 'not_required';
  reviewedAt?: string | null;
  folder?: string;
  mangaTitle?: string;
  seriesId?: string | null;
  seriesTitle?: string | null;
  finishedAt: Date | string;
  startedAt?: Date | string | null;
  durationMs?: number | null;
  settings: Partial<TranslationSettings>;
}

export interface MangaGroupSummary {
  id?: string;
  title: string;
  count: number;
  cover?: FinishedImage | null;
  latestFinishedAt?: string;
  seriesId?: string | null;
  seriesTitle?: string | null;
  hasSummary?: boolean;
  needsReviewCount?: number;
}

export interface SeriesMember {
  id: string;
  title: string;
  position: number;
  count: number;
  latestFinishedAt?: string;
  cover?: FinishedImage | null;
}

export interface SeriesSummary {
  id: string;
  title: string;
  memberCount: number;
  cover?: FinishedImage | null;
  firstGroupId?: string | null;
  updatedAt?: string;
}

export interface SeriesDetail extends SeriesSummary {
  members: SeriesMember[];
}

export interface MangaSummary {
  groupId?: string | null;
  mangaTitle: string;
  summary: string | null;
  provider?: string | null;
  model?: string | null;
  language?: string | null;
  generatedAt?: string | null;
  sourceFingerprint?: string | null;
  stale: boolean;
  jobStatus?: "queued" | "generating" | "paused" | "ready" | "error" | null;
  jobError?: string | null;
  jobUpdatedAt?: string | null;
  jobStage?: SummaryJobStage | null;
  jobProgress?: number | null;
  jobMessage?: string | null;
  jobCurrentPage?: number | null;
  jobPageCount?: number | null;
  jobPagesWithText?: number | null;
  jobExtractionRequired?: boolean;
  pageCount: number;
  textPageCount: number;
  missingPages?: string[];
  skippedPages?: string[];
  ocrErrors?: Record<string, string>;
}

export type SummaryJobStage =
  | "detecting"
  | "ocr"
  | "textline_merge"
  | "concatenating"
  | "summarizing"
  | "complete";

export interface SummaryJob {
  id: string;
  kind: "summary";
  groupId?: string | null;
  title: string;
  status: "queued" | "generating" | "paused" | "ready" | "error";
  provider?: string | null;
  model?: string | null;
  updatedAt?: string | null;
  jobStage?: SummaryJobStage | null;
  jobProgress?: number | null;
  jobMessage?: string | null;
  jobError?: string | null;
  jobCurrentPage?: number | null;
  jobPageCount?: number | null;
  jobPagesWithText?: number | null;
  jobExtractionRequired?: boolean;
  summaryAvailable?: boolean;
  stale?: boolean;
}

export interface EditableTextBlock {
  bubble_safe_shape?: { x: number; y: number; png: string } | null;
  group_members?: string[];
  review_required?: boolean;
  review_reason?: string | null;
  confidence?: number | null;
  prob?: number | null;
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  lines?: Array<Array<[number, number]>>;
  cover_background?: boolean;
  original_text?: string;
  translation: string;
  positioned_lines?: Array<{ text: string; x: number; y: number }>;
  rendered_png?: string | null;
  font_size: number;
  font_family: string;
  fg_color: [number, number, number];
  bg_color: [number, number, number];
  stroke_width: number;
  angle: number;
  direction: 'h' | 'v';
  alignment: 'center' | 'left' | 'right';
  line_spacing: number;
  letter_spacing: number;
  bold: boolean;
  italic: boolean;
  layout_bounds?: {
    x: number;
    y: number;
    width: number;
    height: number;
  };
  layout_segments?: Array<{
    x: number;
    y: number;
    width: number;
    height: number;
    text: string;
    font_size?: number;
    positioned_lines?: Array<{ text: string; x: number; y: number }>;
    rendered_png?: string | null;
  }>;
}
