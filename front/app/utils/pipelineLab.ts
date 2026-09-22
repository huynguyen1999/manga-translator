import { imageMimeTypes } from "@/config";
import { apiUrl } from "@/utils/api";
import { loadSettings } from "@/utils/localStorage";

export const PIPELINE_STAGES = [
  { id: "input", label: "Input", icon: "carbon:upload" },
  { id: "colorization", label: "Colorization", icon: "carbon:color-palette" },
  { id: "upscaling", label: "Upscaling", icon: "carbon:zoom-in" },
  { id: "detection", label: "Detection", icon: "carbon:search" },
  { id: "ocr", label: "OCR", icon: "carbon:character-patterns" },
  { id: "textline_merge", label: "Text-line merge", icon: "carbon:merge" },
  { id: "bubble_detection", label: "Bubble detection", icon: "carbon:chat" },
  { id: "mask_generation", label: "Mask generation", icon: "carbon:gradient" },
  { id: "inpainting", label: "Inpainting", icon: "carbon:erase" },
  { id: "translation", label: "Translation", icon: "carbon:language" },
  { id: "rendering", label: "Rendering / final", icon: "carbon:checkmark" },
] as const;

export type PipelineStageId = (typeof PIPELINE_STAGES)[number]["id"];
export type PipelineStageStatus =
  | "pending"
  | "running"
  | "completed"
  | "skipped"
  | "unavailable"
  | "failed"
  | "cancelled";

export type PipelineStagePlan = Record<PipelineStageId, boolean>;

export interface PipelineLabStage {
  id: PipelineStageId;
  label: string;
  status: PipelineStageStatus;
  reason?: string;
  startedAt?: string;
  finishedAt?: string;
  durationMs?: number;
  artifacts?: string[];
  metadata?: Record<string, unknown>;
}

export interface PipelineLabManifest {
  version: 1;
  kind: "pipeline-lab";
  folder: string;
  status: "running" | "paused" | "completed" | "failed" | "cancelled" | "partial";
  createdAt?: string;
  updatedAt?: string;
  source: { filename: string; width: number; height: number; mode: string };
  config: Record<string, unknown>;
  stagePlan: PipelineStagePlan;
  manual?: boolean;
  waitingFor?: PipelineStageId;
  stages: PipelineLabStage[];
  error?: string;
}

export interface PipelineLabRunSummary {
  folder: string;
  filename: string;
  status: PipelineLabManifest["status"];
  createdAt: string;
  updatedAt: string;
  width?: number;
  height?: number;
}

export interface PipelineLabSettings {
  detectionResolution: string;
  textDetector: string;
  renderTextDirection: string;
  letterCase: "none" | "uppercase" | "lowercase";
  translator: string;
  targetLanguage: string;
  inpaintingSize: string;
  customUnclipRatio: number;
  customBoxThreshold: number;
  customOcrProb?: number;
  maskDilationOffset: number;
  bubbleDetection: boolean;
  inpainter: string;
  colorizer: string;
  colorizeOnly: boolean;
  colorizationSize: string;
  denoiseSigma: number;
  colorThreshold: number;
  ocr: string;
  renderer: string;
  upscaler: string;
  upscaleRatio: number;
  revertUpscaling: boolean;
  manual: boolean;
}

export const DEFAULT_STAGE_PLAN: PipelineStagePlan = {
  ...Object.fromEntries(PIPELINE_STAGES.map(({ id }) => [id, true])),
  upscaling: false,
} as PipelineStagePlan;

export const defaultPipelineLabSettings = (): PipelineLabSettings => {
  const stored = loadSettings();
  return {
    detectionResolution: stored.detectionResolution ?? "2560",
    textDetector: stored.textDetector ?? "default",
    renderTextDirection: stored.renderTextDirection ?? "auto",
    letterCase: stored.letterCase ?? (stored.uppercase ? "uppercase" : stored.lowercase ? "lowercase" : "none"),
    translator: "deepseek",
    targetLanguage: stored.targetLanguage ?? "ENG",
    inpaintingSize: stored.inpaintingSize ?? "2048",
    customUnclipRatio: stored.customUnclipRatio ?? 2.3,
    customBoxThreshold: stored.customBoxThreshold ?? 0.45,
    customOcrProb: stored.customOcrProb ?? stored.ocrMinConfidence ?? undefined,
    maskDilationOffset: stored.maskDilationOffset ?? 30,
    bubbleDetection: stored.bubbleDetection ?? false,
    inpainter: stored.inpainter ?? "default",
    colorizer: "mc2",
    colorizeOnly: stored.colorizeOnly ?? false,
    colorizationSize: stored.colorizationSize ?? "576",
    denoiseSigma: stored.denoiseSigma ?? 25,
    colorThreshold: stored.colorThreshold ?? 31,
    ocr: stored.ocr ?? "48px",
    renderer: "default",
    upscaler: "4xultrasharp",
    upscaleRatio: 2,
    revertUpscaling: true,
    manual: false,
  };
};

export const buildPipelineLabConfig = (
  settings: PipelineLabSettings,
  stagePlan: PipelineStagePlan,
  filename?: string,
) => {
  const isColorizeOnly = Boolean(settings.colorizeOnly);
  return {
    detector: {
      detector: (isColorizeOnly || !stagePlan.detection) ? "none" : settings.textDetector,
      detection_size: Number(settings.detectionResolution),
      box_threshold: settings.customBoxThreshold,
      unclip_ratio: settings.customUnclipRatio,
    },
    render: {
      renderer: (isColorizeOnly || !stagePlan.rendering) ? "none" : settings.renderer,
      direction: settings.renderTextDirection,
      uppercase: settings.letterCase === "uppercase",
      lowercase: settings.letterCase === "lowercase",
    },
    translator: {
      translator: (isColorizeOnly || !stagePlan.translation) ? "original" : settings.translator,
      target_lang: settings.targetLanguage,
    },
    inpainter: {
      inpainter: (isColorizeOnly || !stagePlan.inpainting) ? "none" : settings.inpainter,
      inpainting_size: Number(settings.inpaintingSize),
    },
    colorizer: {
      colorizer: stagePlan.colorization ? settings.colorizer : "none",
      colorization_size: Number(settings.colorizationSize),
      denoise_sigma: settings.denoiseSigma,
      color_threshold: settings.colorThreshold,
      restore_size: true,
    },
    upscale: {
      upscaler: settings.upscaler,
      upscale_ratio: stagePlan.upscaling ? settings.upscaleRatio : null,
      revert_upscaling: stagePlan.upscaling ? settings.revertUpscaling : false,
    },
    ocr: {
      ocr: settings.ocr,
      prob: settings.customOcrProb != null ? settings.customOcrProb : undefined,
    },
    bubble_detection: { enabled: settings.bubbleDetection, model: "manga109" },
    mask_dilation_offset: settings.maskDilationOffset,
    original_name: filename,
    pipeline_lab: { enabled: true, stage_plan: stagePlan, manual: settings.manual },
  };
};

export const artifactUrl = (folder: string, filename: string) =>
  apiUrl(`/result/${encodeURIComponent(folder)}/${encodeURIComponent(filename)}`);

export const isPipelineImage = (file: File | null): boolean =>
  Boolean(file && imageMimeTypes.includes(file.type) && file.size > 0);

export const stageArtifactNames: Partial<Record<PipelineStageId, string[]>> = {
  input: ["input.jpg", "input.png"],
  colorization: ["colorized.png"],
  upscaling: ["upscaled.png"],
  detection: ["mask_raw.png", "detection.json", "bubble_mask.png"],
  ocr: ["ocr.json"],
  textline_merge: ["text_regions_merged.json"],
  bubble_detection: ["bubble_mask.png"],
  translation: ["translations.json", "translation_detail.json"],
  mask_generation: ["text_mask.png", "bubble_mask.png", "mask_final.png"],
  inpainting: ["inpainted.jpg", "inpainted.png"],
  rendering: ["final.jpg", "final.png"],
};

export const stageFromProgress = (state: string): PipelineStageId | null => {
  const map: Record<string, PipelineStageId> = {
    colorizing: "colorization",
    colorization: "colorization",
    upscaling: "upscaling",
    detection: "detection",
    ocr: "ocr",
    textline_merge: "textline_merge",
    "bubble-detection": "bubble_detection",
    bubble_detection: "bubble_detection",
    translating: "translation",
    translation: "translation",
    "mask-generation": "mask_generation",
    mask_generation: "mask_generation",
    inpainting: "inpainting",
    rendering: "rendering",
  };
  return map[state] ?? null;
};

export const stageFromManualWait = (state: string): PipelineStageId | null => {
  if (!state.startsWith("manual_wait:")) return null;
  const raw = state.slice("manual_wait:".length).trim();
  const direct = PIPELINE_STAGES.find((s) => s.id === raw);
  if (direct) return direct.id;
  return stageFromProgress(raw);
};

export interface PipelineStreamFrame {
  status: number;
  payload: Uint8Array;
}

export const decodePipelineFrames = (
  input: Uint8Array,
): { frames: PipelineStreamFrame[]; remainder: Uint8Array } => {
  const frames: PipelineStreamFrame[] = [];
  let offset = 0;
  while (input.length - offset >= 5) {
    const length = new DataView(input.buffer, input.byteOffset + offset + 1, 4).getUint32(0);
    if (input.length - offset < length + 5) break;
    const start = offset + 5;
    frames.push({ status: input[offset], payload: input.slice(start, start + length) });
    offset = start + length;
  }
  return { frames, remainder: input.slice(offset) };
};

export interface RetryPipelineStageResponse {
  status: string;
  stage: PipelineStageId;
  durationMs?: number;
  manifest: PipelineLabManifest;
}

export const retryPipelineStage = async (
  folder: string,
  stage?: PipelineStageId,
  config?: Record<string, unknown>,
): Promise<RetryPipelineStageResponse> => {
  const response = await fetch(
    apiUrl(`/api/pipeline-lab/runs/${encodeURIComponent(folder)}/retry-step`),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stage, config }),
    },
  );
  if (!response.ok) {
    const errorData = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(errorData?.detail || `Failed to retry stage (${response.status})`);
  }
  return (await response.json()) as RetryPipelineStageResponse;
};

export const stopPipelineRun = async (
  folder: string,
): Promise<{ status: string; folder: string }> => {
  const response = await fetch(
    apiUrl(`/api/pipeline-lab/runs/${encodeURIComponent(folder)}/stop`),
    { method: "POST" },
  );
  if (!response.ok) {
    const errorData = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(errorData?.detail || `Failed to stop pipeline (${response.status})`);
  }
  return (await response.json()) as { status: string; folder: string };
};
