import { Icon } from "@iconify/react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Header } from "@/components/Header";
import { PreviewImage } from "@/components/PreviewImage";
import {
  colorizationSizes,
  colorizerOptions,
  detectionResolutions,
  imageMimeTypes,
  inpaintingSizes,
  inpainterOptions,
  languageOptions,
  ocrOptions,
  textDetectorOptions,
} from "@/config";
import { validTranslators } from "@/types";
import { getTranslatorName, getTranslatorGroup } from "@/utils/getTranslatorName";
import {
  artifactUrl,
  buildPipelineLabConfig,
  decodePipelineFrames,
  defaultPipelineLabSettings,
  DEFAULT_STAGE_PLAN,
  isPipelineImage,
  PIPELINE_STAGES,
  stageArtifactNames,
  stageFromManualWait,
  stageFromProgress,
  retryPipelineStage,
  stopPipelineRun,
  type PipelineLabManifest,
  type PipelineLabRunSummary,
  type PipelineLabStage,
  type PipelineLabSettings,
  type PipelineStageId,
  type PipelineStagePlan,
  type PipelineStageStatus,
} from "@/utils/pipelineLab";
import { apiUrl } from "@/utils/api";

type RunState = "idle" | "running" | "paused" | "completed" | "failed" | "cancelled" | "partial";

const LOCKED_STAGES: PipelineStageId[] = ["input", "ocr", "textline_merge", "mask_generation"];
const IMAGE_ARTIFACTS = new Set([".png", ".jpg", ".jpeg", ".webp"]);
const ACTIVE_RUN_STORAGE_KEY = "manga-studio-pipeline-lab-active-run";

const fieldClass =
  "mt-1 w-full rounded-lg border border-zinc-200 bg-white px-3 py-2 text-sm text-zinc-900 outline-none transition-colors focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-100";

const statusLabel = (status: PipelineStageStatus | RunState) =>
  ({
    idle: "Ready",
    running: "Running",
    paused: "Waiting for Continue",
    completed: "Complete",
    partial: "Partial",
    failed: "Failed",
    cancelled: "Cancelled",
    pending: "Waiting",
    skipped: "Skipped",
    unavailable: "Unavailable",
  })[status] ?? "In progress";

const statusTone = (status: PipelineStageStatus | RunState) =>
  ({
    running: "text-amber-700 dark:text-amber-300",
    paused: "text-amber-700 dark:text-amber-300",
    completed: "text-emerald-700 dark:text-emerald-300",
    skipped: "text-zinc-500 dark:text-zinc-400",
    unavailable: "text-zinc-400 dark:text-zinc-500",
    failed: "text-rose-700 dark:text-rose-300",
    cancelled: "text-amber-700 dark:text-amber-300",
  } as Record<string, string>)[status] ?? "text-zinc-600 dark:text-zinc-300";

const stageMarkerTone = (status: PipelineStageStatus) => {
  if (status === "completed") return "border-emerald-500 bg-emerald-500 text-white";
  if (status === "running") return "border-amber-500 text-amber-600";
  return "border-zinc-300 text-zinc-400 dark:border-zinc-700";
};

const formatTimestamp = (timestamp?: string) => timestamp ? new Date(timestamp).toLocaleString() : "Not recorded";

function Field({
  label,
  tooltip,
  children,
}: {
  label: string;
  tooltip?: string;
  children: ReactNode;
}) {
  return (
    <label
      className="block text-xs font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400"
      title={tooltip}
    >
      <span className="flex items-center justify-between gap-1">
        <span>{label}</span>
        {tooltip && (
          <Icon
            icon="carbon:information"
            className="h-3.5 w-3.5 shrink-0 text-zinc-400 dark:text-zinc-500"
          />
        )}
      </span>
      {children}
    </label>
  );
}

function emptyLiveStages(stagePlan: PipelineStagePlan, hasFile: boolean): PipelineLabStage[] {
  return PIPELINE_STAGES.map((stage) => ({
    ...stage,
    status: stage.id === "input" && hasFile
      ? "completed" as PipelineStageStatus
      : stagePlan[stage.id]
        ? "pending" as PipelineStageStatus
        : "skipped" as PipelineStageStatus,
  }));
}

export function PipelineLab({
  showHeader = true,
  initialFile = null,
}: {
  showHeader?: boolean;
  initialFile?: File | null;
} = {}) {
  const [theme, setTheme] = useState<"light" | "dark">("dark");
  const [file, setFile] = useState<File | null>(null);
  const [settings, setSettings] = useState<PipelineLabSettings>(defaultPipelineLabSettings);
  const [stagePlan, setStagePlan] = useState<PipelineStagePlan>({ ...DEFAULT_STAGE_PLAN });
  const [manifest, setManifest] = useState<PipelineLabManifest | null>(null);
  const [folder, setFolder] = useState<string | null>(null);
  const [selectedStage, setSelectedStage] = useState<PipelineStageId>("input");
  const [runState, setRunState] = useState<RunState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<PipelineLabRunSummary[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [rerunAfterLoad, setRerunAfterLoad] = useState(false);
  const [artifactData, setArtifactData] = useState<unknown[] | Record<string, unknown> | null>(null);
  const [stageDetailStage, setStageDetailStage] = useState<PipelineLabStage | null>(null);
  const [stageDetailData, setStageDetailData] = useState<Record<string, unknown> | null>(null);
  const [stageDetailLoading, setStageDetailLoading] = useState(false);
  const [retryingStage, setRetryingStage] = useState<PipelineStageId | null>(null);
  const [cacheBuster, setCacheBuster] = useState(0);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!stageDetailStage) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setStageDetailStage(null);
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [stageDetailStage]);

  useEffect(() => {
    if (folder) localStorage.setItem(ACTIVE_RUN_STORAGE_KEY, folder);
  }, [folder]);

  useEffect(() => {
    const saved = localStorage.getItem("manga-studio-theme") as "light" | "dark" | null;
    const initialTheme =
      saved || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    setTheme(initialTheme);
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
  }, [theme]);

  const toggleTheme = () => {
    setTheme((value) => {
      const next = value === "dark" ? "light" : "dark";
      localStorage.setItem("manga-studio-theme", next);
      return next;
    });
  };

  const refreshHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const response = await fetch(apiUrl("/api/pipeline-lab/runs"));
      if (response.ok) setHistory((await response.json()) as PipelineLabRunSummary[]);
    } catch {
      // The run screen remains useful while the backend is offline.
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshHistory();
  }, [refreshHistory]);

  const chooseFile = useCallback((candidate: File | null) => {
    if (!candidate) return;
    if (!isPipelineImage(candidate)) {
      setError(`Use one PNG, JPEG, BMP, or WEBP image (${imageMimeTypes.join(", ")}).`);
      return;
    }
    setFile(candidate);
    localStorage.removeItem(ACTIVE_RUN_STORAGE_KEY);
    setManifest(null);
    setFolder(null);
    setSelectedStage("input");
    setRunState("idle");
    setError(null);
  }, []);

  useEffect(() => {
    if (initialFile) {
      chooseFile(initialFile);
    }
  }, [initialFile, chooseFile]);

  useEffect(() => {
    const onPaste = (event: ClipboardEvent) => {
      const pasted = Array.from(event.clipboardData?.files ?? []).find((item) => isPipelineImage(item));
      if (pasted) chooseFile(pasted);
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [chooseFile]);

  const updateSettings = <K extends keyof PipelineLabSettings>(key: K, value: PipelineLabSettings[K]) => {
    setSettings((current) => ({ ...current, [key]: value }));
  };

  const translatorGroups = useMemo(() => {
    const groups: Record<string, { key: string; name: string }[]> = {};
    validTranslators.forEach((key) => {
      const grp = getTranslatorGroup(key);
      if (!groups[grp]) groups[grp] = [];
      groups[grp].push({ key, name: getTranslatorName(key) });
    });
    return groups;
  }, []);


  const resetDefaults = () => {
    setSettings(defaultPipelineLabSettings());
    setStagePlan({ ...DEFAULT_STAGE_PLAN });
  };

  const handleProgress = (state: string, onFolder: (value: string) => void) => {
    if (state.startsWith("debug_folder:") || state.startsWith("final_ready:")) {
      onFolder(state.slice(state.indexOf(":") + 1));
      return;
    }
    const waitingStage = stageFromManualWait(state);
    if (waitingStage) {
      setRunState("paused");
      setSelectedStage(waitingStage);
      return;
    }
    const currentStage = stageFromProgress(state);
    if (currentStage) {
      setRunState("running");
      setSelectedStage(currentStage);
    }
  };

  const runPipeline = useCallback(async () => {
    if (!file) {
      setError("Choose one image before running the pipeline.");
      return;
    }
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setRunState("running");
    setError(null);
    setManifest(null);
    localStorage.removeItem(ACTIVE_RUN_STORAGE_KEY);
    setFolder(null);
    setSelectedStage("input");
    let runFolder: string | null = null;
    const refreshManifest = async (activeFolder: string) => {
      try {
        const manifestResponse = await fetch(
          apiUrl(`/api/pipeline-lab/runs/${encodeURIComponent(activeFolder)}/manifest`),
        );
        if (!manifestResponse.ok) return;
        const nextManifest = (await manifestResponse.json()) as PipelineLabManifest;
        setManifest(nextManifest);
        setRunState(nextManifest.status);
      } catch {
        // Progress streaming remains authoritative if a live manifest refresh misses.
      }
    };
    try {
      const formData = new FormData();
      formData.append("image", file);
      formData.append("config", JSON.stringify(buildPipelineLabConfig(settings, stagePlan, file.name)));
      const response = await fetch(apiUrl("/api/translate/with-form/image/stream/web"), {
        method: "POST",
        body: formData,
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`Pipeline request failed (${response.status}).`);
      if (!response.body) throw new Error("The server did not return a progress stream.");

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let remainder = new Uint8Array() as Uint8Array<ArrayBufferLike>;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (!value) continue;
        const merged = new Uint8Array(remainder.length + value.length);
        merged.set(remainder);
        merged.set(value, remainder.length);
        const decoded = decodePipelineFrames(merged);
        remainder = decoded.remainder;
        for (const frame of decoded.frames) {
          const message = decoder.decode(frame.payload).trim();
          if (frame.status === 2) throw new Error(message || "The pipeline returned an error.");
          if (frame.status === 1) handleProgress(message, (value) => {
            runFolder = value;
            setFolder(value);
          });
          if (frame.status === 1 && runFolder) await refreshManifest(runFolder);
        }
      }

      if (runFolder) {
        const manifestResponse = await fetch(apiUrl(`/api/pipeline-lab/runs/${encodeURIComponent(runFolder)}/manifest`));
        if (manifestResponse.ok) {
          const nextManifest = (await manifestResponse.json()) as PipelineLabManifest;
          setManifest(nextManifest);
          setRunState(nextManifest.status);
          const firstUseful = nextManifest.stages.find((stage) =>
            stage.status === "completed" || stage.status === "failed" || stage.status === "cancelled",
          );
          if (firstUseful) setSelectedStage(firstUseful.id);
        } else {
          setRunState("partial");
        }
      } else {
        setRunState("partial");
        setError("The server finished without a diagnostic folder. Restart the backend and try again.");
      }
      await refreshHistory();
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "AbortError") {
        setRunState("cancelled");
        setError("Pipeline stopped by user.");
      } else {
        setRunState("failed");
        setError(cause instanceof Error ? cause.message : "The pipeline failed.");
      }
      if (runFolder) {
        const manifestResponse = await fetch(apiUrl(`/api/pipeline-lab/runs/${encodeURIComponent(runFolder)}/manifest`)).catch(() => null);
        if (manifestResponse?.ok) setManifest((await manifestResponse.json()) as PipelineLabManifest);
      }
      await refreshHistory();
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  }, [file, refreshHistory, settings, stagePlan]);

  const stopPipeline = useCallback(async () => {
    abortRef.current?.abort();
    if (folder) {
      try {
        await stopPipelineRun(folder);
      } catch {
        // Pipeline may have already halted
      }
      try {
        const manifestResponse = await fetch(apiUrl(`/api/pipeline-lab/runs/${encodeURIComponent(folder)}/manifest`));
        if (manifestResponse.ok) {
          setManifest((await manifestResponse.json()) as PipelineLabManifest);
        }
      } catch {
        // Ignore
      }
    }
    setRunState("cancelled");
    setError("Pipeline stopped by user.");
    await refreshHistory();
  }, [folder, refreshHistory]);

  const removeImage = useCallback(() => {
    if (runState === "running" || runState === "paused") {
      void stopPipeline();
    }
    setFile(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
    localStorage.removeItem(ACTIVE_RUN_STORAGE_KEY);
    setManifest(null);
    setFolder(null);
    setSelectedStage("input");
    setRunState("idle");
    setError(null);
    setArtifactData(null);
  }, [runState, stopPipeline]);

  const continuePipeline = async () => {
    if (!folder) return;
    const targetStage = manifest?.waitingFor;
    setRunState("running");
    if (targetStage) setSelectedStage(targetStage);
    try {
      const response = await fetch(apiUrl(`/api/pipeline-lab/runs/${encodeURIComponent(folder)}/continue`), { method: "POST" });
      if (!response.ok) {
        setError("Could not continue the pipeline.");
        setRunState("paused");
      }
    } catch {
      setError("Could not connect to the pipeline server.");
      setRunState("paused");
    }
  };

  const handleRetryStep = async (stageId: PipelineStageId) => {
    if (!folder) return;
    setRetryingStage(stageId);
    setError(null);
    try {
      const currentConfig = buildPipelineLabConfig(settings, stagePlan, file?.name);
      const res = await retryPipelineStage(folder, stageId, currentConfig);
      if (res.manifest) {
        setManifest(res.manifest);
      }
      setSelectedStage(stageId);
      setCacheBuster(Date.now());
      const targetStageObj = res.manifest?.stages.find((s) => s.id === stageId);
      const targetJson = targetStageObj?.artifacts?.find((name) => name.endsWith(".json"));
      if (targetJson) {
        try {
          const jsonRes = await fetch(apiUrl(`${artifactUrl(folder, targetJson)}?t=${Date.now()}`));
          if (jsonRes.ok) {
            setArtifactData(await jsonRes.json());
          }
        } catch {
          // ignore
        }
      }
      await refreshHistory();
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to retry stage ${stageId}`);
    } finally {
      setRetryingStage(null);
    }
  };

  const openStageDetails = useCallback(async (stage: PipelineLabStage) => {
    setStageDetailStage(stage);
    setStageDetailData(null);
    const hasTranslationDetail = stage.id === "translation" && stage.artifacts?.includes("translation_detail.json");
    setStageDetailLoading(Boolean(hasTranslationDetail && folder));
    if (!hasTranslationDetail || !folder) return;
    try {
      const response = await fetch(apiUrl(artifactUrl(folder, "translation_detail.json")));
      if (response.ok) setStageDetailData((await response.json()) as Record<string, unknown>);
    } finally {
      setStageDetailLoading(false);
    }
  }, [folder]);

  useEffect(() => {
    if (rerunAfterLoad && file && runState !== "running") {
      setRerunAfterLoad(false);
      void runPipeline();
    }
  }, [file, rerunAfterLoad, runPipeline, runState]);

  const loadRun = useCallback(async (summary: PipelineLabRunSummary, rerun = false) => {
    setError(null);
    const response = await fetch(apiUrl(`/api/pipeline-lab/runs/${encodeURIComponent(summary.folder)}/manifest`));
    if (!response.ok) {
      setError("Could not reopen that run.");
      return;
    }
    const nextManifest = (await response.json()) as PipelineLabManifest;
    setManifest(nextManifest);
    setFolder(summary.folder);
    setRunState(nextManifest.status);
    const usefulStage = nextManifest.stages.find((stage) => stage.status === "completed") ?? nextManifest.stages[0];
    if (usefulStage) setSelectedStage(usefulStage.id);
    const config = nextManifest.config as Record<string, any>;
    const detector = config.detector ?? {};
    const render = config.render ?? {};
    const translator = config.translator ?? {};
    const inpainter = config.inpainter ?? {};
    const colorizer = config.colorizer ?? {};
    const upscale = config.upscale ?? {};
    const ocr = config.ocr ?? {};
    const pipelineLab = config.pipeline_lab ?? {};
    setSettings((current) => ({
      ...current,
      detectionResolution: String(detector.detection_size ?? current.detectionResolution),
      textDetector: String(detector.detector ?? current.textDetector),
      renderTextDirection: String(render.direction ?? current.renderTextDirection),
      letterCase: (render.uppercase ? "uppercase" : render.lowercase ? "lowercase" : "none") as "none" | "uppercase" | "lowercase",
      translator: String(translator.translator ?? current.translator),
      targetLanguage: String(translator.target_lang ?? current.targetLanguage),
      inpaintingSize: String(inpainter.inpainting_size ?? current.inpaintingSize),
      inpainter: String(inpainter.inpainter ?? current.inpainter),
      customUnclipRatio: Number(detector.unclip_ratio ?? current.customUnclipRatio),
      customBoxThreshold: Number(detector.box_threshold ?? current.customBoxThreshold),
      maskDilationOffset: Number(config.mask_dilation_offset ?? current.maskDilationOffset),
      bubbleDetection: Boolean(config.bubble_detection?.enabled ?? current.bubbleDetection),
      colorizer: String(colorizer.colorizer ?? current.colorizer),
      colorizeOnly: Boolean(
        detector.detector === "none" &&
        (translator.translator === "none" || translator.translator === "original") &&
        colorizer.colorizer && colorizer.colorizer !== "none"
      ),
      colorizationSize: String(colorizer.colorization_size ?? current.colorizationSize),
      denoiseSigma: Number(colorizer.denoise_sigma ?? current.denoiseSigma),
      colorThreshold: Number(colorizer.color_threshold ?? current.colorThreshold),
      renderer: String(render.renderer ?? current.renderer),
      upscaler: String(upscale.upscaler ?? current.upscaler),
      upscaleRatio: Number(upscale.upscale_ratio ?? current.upscaleRatio),
      revertUpscaling: Boolean(upscale.revert_upscaling ?? current.revertUpscaling),
      ocr: String(ocr.ocr ?? current.ocr),
      customOcrProb: ocr.prob != null ? Number(ocr.prob) : current.customOcrProb,
      manual: Boolean(pipelineLab.manual ?? current.manual),
    }));
    setStagePlan(nextManifest.stagePlan);
    const inputResponse = await fetch(apiUrl(artifactUrl(summary.folder, "input.jpg")));
    if (inputResponse.ok) {
      const blob = await inputResponse.blob();
      setFile(new File([blob], nextManifest.source.filename, { type: blob.type || "image/png" }));
    }
    if (rerun) setRerunAfterLoad(true);
  }, []);

  useEffect(() => {
    if (folder || history.length === 0) return;
    const savedFolder = localStorage.getItem(ACTIVE_RUN_STORAGE_KEY);
    if (!savedFolder) return;
    const savedRun = history.find((item) => item.folder === savedFolder);
    if (savedRun) {
      void loadRun(savedRun);
    } else {
      localStorage.removeItem(ACTIVE_RUN_STORAGE_KEY);
    }
  }, [folder, history, loadRun]);

  const deleteRun = async (summary: PipelineLabRunSummary) => {
    if (!window.confirm(`Delete the diagnostic bundle for ${summary.filename}?`)) return;
    const response = await fetch(apiUrl(`/api/pipeline-lab/runs/${encodeURIComponent(summary.folder)}`), { method: "DELETE" });
    if (response.ok) {
      if (folder === summary.folder) {
        localStorage.removeItem(ACTIVE_RUN_STORAGE_KEY);
        setFolder(null);
        setManifest(null);
      }
      await refreshHistory();
    } else setError("Could not delete that diagnostic bundle.");
  };

  const toggleStage = (id: PipelineStageId) => {
    if (LOCKED_STAGES.includes(id)) return;
    setStagePlan((current) => {
      const nextEnabled = !current[id];
      if (id === "upscaling" && !nextEnabled) {
        setSettings((prev) => ({ ...prev, revertUpscaling: false }));
      }
      return { ...current, [id]: nextEnabled };
    });
  };

  const liveStages: PipelineLabStage[] = useMemo(
    () => manifest?.stages ?? emptyLiveStages(stagePlan, Boolean(file)),
    [file, manifest, stagePlan],
  );
  const selected = liveStages.find((stage) => stage.id === selectedStage) ?? liveStages[0];
  const waitingForLabel = PIPELINE_STAGES.find((stage) => stage.id === manifest?.waitingFor)?.label ?? "next stage";
  const artifacts = selected?.artifacts?.length
    ? selected.artifacts
    : manifest
      ? []
      : stageArtifactNames[selectedStage] ?? [];
  const imageArtifact = artifacts.find((name) => IMAGE_ARTIFACTS.has(name.slice(name.lastIndexOf(".")))) ?? null;
  const jsonArtifact = artifacts.find((name) => name.endsWith(".json")) ?? null;
  const selectedImage = folder && imageArtifact ? `${apiUrl(artifactUrl(folder, imageArtifact))}${cacheBuster ? `?t=${cacheBuster}` : ""}` : null;

  const fallbackPreviewImage = useMemo(() => {
    if (!folder || !manifest) return null;
    const currentIndex = manifest.stages.findIndex((s) => s.id === selectedStage);
    const stagesToCheck = currentIndex >= 0
      ? manifest.stages.slice(0, currentIndex).reverse()
      : [...manifest.stages].reverse();
    for (const st of stagesToCheck) {
      if (st.status !== "completed") continue;
      const stArtifacts = st.artifacts?.length ? st.artifacts : stageArtifactNames[st.id] ?? [];
      const img = stArtifacts.find((name) => IMAGE_ARTIFACTS.has(name.slice(name.lastIndexOf("."))));
      if (img) {
        return {
          url: `${apiUrl(artifactUrl(folder, img))}${cacheBuster ? `?t=${cacheBuster}` : ""}`,
          stageName: st.label,
        };
      }
    }
    return null;
  }, [folder, manifest, selectedStage, cacheBuster]);

  const lastCompletedStage: PipelineLabStage | null = useMemo(() => {
    if (!manifest) return null;
    if (runState === "paused" && manifest.waitingFor) {
      const waitingIndex = manifest.stages.findIndex((s) => s.id === manifest.waitingFor);
      if (waitingIndex > 0) {
        return manifest.stages[waitingIndex - 1] ?? null;
      }
    }
    const completedStages = manifest.stages.filter(
      (s) => s.status === "completed" && s.id !== "input",
    );
    return completedStages.length > 0 ? completedStages[completedStages.length - 1] : null;
  }, [manifest, runState]);

  useEffect(() => {
    setArtifactData(null);
    if (!folder || !jsonArtifact) return;
    let active = true;
    fetch(apiUrl(artifactUrl(folder, jsonArtifact)))
      .then((response) => response.ok ? response.json() : null)
      .then((data) => { if (active) setArtifactData(data); })
      .catch(() => { if (active) setArtifactData(null); });
    return () => { active = false; };
  }, [folder, jsonArtifact]);

  useEffect(() => {
    if (!folder || !["running", "paused"].includes(runState)) return;
    let active = true;
    const loadManifest = async () => {
      const response = await fetch(apiUrl(`/api/pipeline-lab/runs/${encodeURIComponent(folder)}/manifest`)).catch(() => null);
      if (active && response?.ok) {
        const nextManifest = (await response.json()) as PipelineLabManifest;
        setManifest(nextManifest);
        if (nextManifest.status === "running" || nextManifest.status === "paused") {
          setRunState(nextManifest.status);
        }
      }
    };
    void loadManifest();
    const timer = window.setInterval(() => void loadManifest(), 500);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [folder, runState]);

  const onFileInput = (event: React.ChangeEvent<HTMLInputElement>) => chooseFile(event.target.files?.[0] ?? null);

  return (
    <div className="min-h-screen bg-zinc-50 text-zinc-900 transition-colors dark:bg-zinc-950 dark:text-zinc-100">
      {showHeader && <Header theme={theme} activeView="pipeline" onToggleTheme={toggleTheme} />}
      <main className="mx-auto max-w-[1560px] space-y-4 px-4 py-5 sm:px-6 lg:px-8">
        <section className="flex flex-col justify-between gap-4 md:flex-row md:items-end">
          <div>
            <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-amber-600 dark:text-amber-300">
              <span className="h-2 w-2 rounded-full bg-amber-500 shadow-[0_0_12px] shadow-amber-400" />
              Diagnostic workspace
            </div>
            <h1 className="text-3xl font-semibold tracking-tight">Pipeline Lab</h1>
            <p className="mt-1 max-w-2xl text-sm text-zinc-500 dark:text-zinc-400">
              Run one image through the real translator and inspect the evidence produced by every stage.
            </p>
          </div>
          <div className="flex items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400">
            <span className="rounded-full border border-zinc-200 bg-white px-3 py-1.5 dark:border-zinc-800 dark:bg-zinc-900">one image per run</span>
            <span className={`rounded-full border border-zinc-200 bg-white px-3 py-1.5 dark:border-zinc-800 dark:bg-zinc-900 ${statusTone(runState)}`}>{statusLabel(runState)}</span>
          </div>
        </section>

        <section className="grid gap-4 xl:grid-cols-[280px_minmax(0,1fr)]">
          <aside className="space-y-4">
            <div className="rounded-2xl border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900/70">
              <div className="mb-3 flex items-center justify-between">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">Source image</p>
                  <p className="mt-1 max-w-[180px] truncate text-sm font-medium" title={file?.name}>{file?.name ?? "No image loaded"}</p>
                </div>
                {file ? (
                  <button
                    type="button"
                    onClick={removeImage}
                    className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-rose-600 transition-colors hover:bg-rose-50 hover:text-rose-700 dark:text-rose-400 dark:hover:bg-rose-950/40 dark:hover:text-rose-300"
                    title="Remove source image"
                  >
                    <Icon icon="carbon:trash-can" className="h-3.5 w-3.5" />
                    <span>Remove</span>
                  </button>
                ) : (
                  <Icon icon="carbon:image" className="h-5 w-5 text-amber-500" />
                )}
              </div>
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                onDragOver={(event) => { event.preventDefault(); setIsDragging(true); }}
                onDragLeave={() => setIsDragging(false)}
                onDrop={(event) => { event.preventDefault(); setIsDragging(false); chooseFile(event.dataTransfer.files[0] ?? null); }}
                className={`flex min-h-32 w-full flex-col items-center justify-center rounded-xl border border-dashed p-4 text-center transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500 ${isDragging ? "border-amber-500 bg-amber-50 dark:bg-amber-950/30" : "border-zinc-300 bg-zinc-50 hover:border-amber-400 dark:border-zinc-700 dark:bg-zinc-950/60"}`}
              >
                {file ? <PreviewImage file={file} result={null} className="mb-2 h-20" showComparisonControls={false} /> : <Icon icon="carbon:cloud-upload" className="mb-2 h-8 w-8 text-zinc-400" />}
                <span className="text-sm font-medium">{file ? "Replace image" : "Drop or choose an image"}</span>
                <span className="mt-1 text-xs text-zinc-500">PNG · JPEG · BMP · WEBP · paste works too</span>
              </button>
              {file && (
                <div className="mt-2 flex justify-end">
                  <button
                    type="button"
                    onClick={removeImage}
                    className="inline-flex items-center gap-1 text-xs font-medium text-rose-600 hover:text-rose-700 hover:underline dark:text-rose-400 dark:hover:text-rose-300"
                  >
                    <Icon icon="carbon:trash-can" className="h-3.5 w-3.5" />
                    Remove image
                  </button>
                </div>
              )}
              <input ref={fileInputRef} type="file" accept={imageMimeTypes.join(",")} onChange={onFileInput} className="hidden" />
            </div>

            <div className="rounded-2xl border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900/70">
              <div className="mb-3 flex items-center justify-between">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">Pipeline plan</p>
                  <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">Fixed order · optional phases can be skipped</p>
                </div>
                <Icon icon="carbon:flow" className="h-5 w-5 text-teal-500" />
              </div>
              <div className="space-y-1.5">
                {PIPELINE_STAGES.filter((stage) => stage.id !== "input").map((stage) => {
                  const locked = LOCKED_STAGES.includes(stage.id);
                  return (
                    <button key={stage.id} type="button" onClick={() => toggleStage(stage.id)} disabled={locked} aria-pressed={stagePlan[stage.id]} className="flex w-full items-center justify-between rounded-lg px-2 py-2 text-left text-sm transition-colors hover:bg-zinc-50 disabled:cursor-default dark:hover:bg-zinc-800/70">
                      <span className={`flex items-center gap-2 ${locked ? "text-zinc-500 dark:text-zinc-400" : "text-zinc-700 dark:text-zinc-200"}`}>
                        <Icon icon={stage.icon} className="h-4 w-4" />{stage.label}
                      </span>
                      <span className={`flex items-center gap-1 text-xs ${stagePlan[stage.id] ? "text-teal-600 dark:text-teal-300" : "text-zinc-400"}`}>
                        {locked ? <Icon icon="carbon:locked" className="h-3.5 w-3.5" /> : <span className={`h-2 w-2 rounded-full ${stagePlan[stage.id] ? "bg-teal-500" : "bg-zinc-300 dark:bg-zinc-700"}`} />}
                        {locked ? "required" : stagePlan[stage.id] ? "on" : "off"}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="rounded-2xl border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900/70">
              <div className="mb-3 flex items-center justify-between">
                <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">Run controls</p>
                <Icon icon="carbon:play-filled" className="h-5 w-5 text-amber-500" />
              </div>
              <label className="mb-3 flex items-start gap-2 rounded-lg border border-zinc-200 px-3 py-2.5 text-xs dark:border-zinc-800">
                <input type="checkbox" checked={settings.manual} onChange={(event) => updateSettings("manual", event.target.checked)} disabled={runState === "running" || runState === "paused"} className="mt-0.5 h-4 w-4 accent-indigo-500" />
                <span><span className="block font-semibold text-zinc-700 dark:text-zinc-200">Manual steps</span><span className="mt-0.5 block leading-relaxed text-zinc-500 dark:text-zinc-400">Pause before each stage and continue when ready.</span></span>
              </label>
              <button type="button" onClick={() => void runPipeline()} disabled={!file || runState === "running"} className="flex w-full items-center justify-center gap-2 rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500">
                <Icon icon={runState === "running" ? "carbon:progress-bar" : "carbon:play-filled"} className={runState === "running" ? "h-4 w-4 animate-pulse" : "h-4 w-4"} />
                {runState === "running" ? "Running pipeline…" : manifest ? "Run again" : "Run pipeline"}
              </button>
              {settings.manual && folder && (runState === "running" || runState === "paused") && <button type="button" onClick={() => void continuePipeline()} disabled={runState !== "paused"} className="mt-2 flex w-full items-center justify-center gap-2 rounded-lg border border-indigo-200 px-4 py-2 text-sm font-semibold text-indigo-700 transition-colors hover:bg-indigo-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-indigo-900 dark:text-indigo-300 dark:hover:bg-indigo-950/40"><Icon icon="carbon:chevron-right" className="h-4 w-4" />{runState === "paused" ? `Continue to ${waitingForLabel}` : "Stage running…"}</button>}
              {lastCompletedStage && folder && runState !== "running" && (
                <button
                  type="button"
                  onClick={() => void handleRetryStep(lastCompletedStage.id)}
                  disabled={retryingStage !== null}
                  className="mt-2 flex w-full items-center justify-center gap-2 rounded-lg border border-amber-300 bg-amber-50 px-4 py-2 text-sm font-semibold text-amber-800 transition-colors hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-300 dark:hover:bg-amber-950/70"
                >
                  <Icon
                    icon={retryingStage === lastCompletedStage.id ? "carbon:progress-bar" : "carbon:renew"}
                    className={retryingStage === lastCompletedStage.id ? "h-4 w-4 animate-spin" : "h-4 w-4"}
                  />
                  {retryingStage === lastCompletedStage.id
                    ? `Retrying ${lastCompletedStage.label}…`
                    : `Retry ${lastCompletedStage.label} with new values`}
                </button>
              )}
              {(runState === "running" || runState === "paused") && (
                <button
                  type="button"
                  onClick={() => void stopPipeline()}
                  className="mt-2 flex w-full items-center justify-center gap-2 rounded-lg border border-rose-300 bg-rose-50 px-4 py-2 text-sm font-semibold text-rose-700 transition-colors hover:bg-rose-100 dark:border-rose-800 dark:bg-rose-950/40 dark:text-rose-300 dark:hover:bg-rose-950/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-500"
                >
                  <Icon icon="carbon:stop-filled-alt" className="h-4 w-4 text-rose-600 dark:text-rose-400" />
                  <span>Stop pipeline</span>
                </button>
              )}
              {error && <p role="alert" className="mt-3 rounded-lg border border-rose-200 bg-rose-50 p-2.5 text-xs leading-relaxed text-rose-700 dark:border-rose-900/70 dark:bg-rose-950/30 dark:text-rose-300">{error}</p>}
            </div>
          </aside>

          <section className="min-h-[650px] overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900/70">
            <div className="border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
              <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">Execution timeline</p>
              <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">Select a stage to inspect its output and metadata.</p>
            </div>
            <div className="min-h-[590px]">
              <div className="relative z-10 shrink-0 overflow-x-auto border-b border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
                <div className="flex min-w-max items-start justify-center gap-1">
                  {liveStages.map((stage, index) => {
                    const isWaiting = runState === "paused" && manifest?.waitingFor === stage.id;
                    return (
                      <div key={stage.id} className="flex items-start">
                        <button
                          type="button"
                          onClick={() => setSelectedStage(stage.id)}
                          className={`group flex w-[84px] shrink-0 flex-col items-center gap-1.5 rounded-lg px-1.5 py-1.5 text-center transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500 ${
                            selectedStage === stage.id
                              ? isWaiting
                                ? "bg-amber-100/70 ring-1 ring-amber-400 dark:bg-amber-950/60 dark:ring-amber-600"
                                : "bg-amber-50 dark:bg-amber-950/40"
                              : "hover:bg-zinc-50 dark:hover:bg-zinc-800/60"
                          }`}
                        >
                          <span
                            className={`flex h-6 w-6 items-center justify-center rounded-full border text-xs font-bold ${
                              isWaiting
                                ? "border-amber-500 bg-amber-500 text-white animate-pulse"
                                : stageMarkerTone(stage.status)
                            }`}
                          >
                            {isWaiting ? (
                              <Icon icon="carbon:pause-filled" className="h-3 w-3" />
                            ) : stage.status === "completed" ? (
                              <Icon icon="carbon:checkmark" className="h-3 w-3" />
                            ) : stage.status === "skipped" ? (
                              "–"
                            ) : (
                              index + 1
                            )}
                          </span>
                          <span className="block max-w-full truncate text-xs font-semibold text-zinc-700 dark:text-zinc-200">
                            {stage.label}
                          </span>
                          <span
                            className={`block text-xs ${
                              isWaiting
                                ? "font-semibold text-amber-600 dark:text-amber-400"
                                : statusTone(stage.status)
                            }`}
                          >
                            {isWaiting ? "Paused" : statusLabel(stage.status)}
                          </span>
                        </button>
                        {index < liveStages.length - 1 && (
                          <span
                            className={`mt-4 h-px w-5 shrink-0 ${
                              stage.status === "completed" ? "bg-emerald-500/70" : "bg-zinc-300 dark:bg-zinc-700"
                            }`}
                          />
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
              <div className="relative z-0 flex min-w-0 flex-col">
                <div className="relative isolate flex h-[560px] max-h-[60vh] min-h-[360px] flex-none items-center justify-center overflow-visible bg-[radial-gradient(circle_at_center,rgba(99,102,241,0.07),transparent_60%)] p-4 dark:bg-[radial-gradient(circle_at_center,rgba(45,212,191,0.05),transparent_60%)]">
                  {selectedImage || fallbackPreviewImage ? (
                    <div className="relative h-full max-h-[560px] w-full">
                      {runState === "paused" && manifest?.waitingFor === selected?.id && (
                        <div className="absolute top-3 left-1/2 z-20 flex -translate-x-1/2 items-center gap-2 rounded-full border border-amber-300 bg-amber-50/95 px-3.5 py-1.5 shadow-md backdrop-blur dark:border-amber-700/80 dark:bg-amber-950/90">
                          <Icon icon="carbon:pause-filled" className="h-4 w-4 text-amber-600 dark:text-amber-400" />
                          <span className="text-xs font-semibold text-amber-900 dark:text-amber-200">
                            Ready to run {selected?.label}
                            {fallbackPreviewImage && !selectedImage ? ` · Showing ${fallbackPreviewImage.stageName} output` : ""}
                          </span>
                          <button
                            type="button"
                            onClick={() => void continuePipeline()}
                            className="ml-1.5 rounded bg-amber-600 px-2.5 py-0.5 text-xs font-bold text-white shadow-sm transition-colors hover:bg-amber-500"
                          >
                            Continue
                          </button>
                        </div>
                      )}
                      {!(runState === "paused" && manifest?.waitingFor === selected?.id) && !selectedImage && fallbackPreviewImage && (
                        <div className="absolute top-3 left-1/2 z-20 flex -translate-x-1/2 items-center gap-2 rounded-full border border-zinc-200 bg-white/90 px-3 py-1 text-xs text-zinc-600 shadow-sm backdrop-blur dark:border-zinc-700 dark:bg-zinc-900/90 dark:text-zinc-300">
                          <Icon icon="carbon:information" className="h-3.5 w-3.5 text-zinc-400" />
                          <span>Showing {fallbackPreviewImage.stageName} visual output ({selected?.label} has metadata only)</span>
                        </div>
                      )}
                      <PreviewImage
                        file={file ?? (folder ? artifactUrl(folder, "input.jpg") : null)}
                        result={selectedImage ?? fallbackPreviewImage?.url ?? null}
                        className="h-full max-h-[560px] w-full"
                        floatingToolbarPlacement="below-image"
                      />
                    </div>
                  ) : file && selected?.id === "input" ? (
                    <PreviewImage file={file} result={null} className="h-full max-h-[560px] w-full" showComparisonControls={false} />
                  ) : (
                    <div className="max-w-sm text-center">
                      <Icon icon={selected?.status === "unavailable" ? "carbon:locked" : "carbon:hourglass"} className="mx-auto mb-3 h-10 w-10 text-zinc-300 dark:text-zinc-700" />
                      <p className="text-sm font-medium text-zinc-600 dark:text-zinc-300">{selected?.reason || (folder ? "No visual artifact was saved for this stage." : "Run the pipeline to populate this stage.")}</p>
                    </div>
                  )}
                </div>
                <div className="border-t border-zinc-200 p-4 dark:border-zinc-800">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <h2 className="text-lg font-semibold">{selected?.label}</h2>
                      <p className={`mt-0.5 text-xs font-medium ${
                        runState === "paused" && manifest?.waitingFor === selected?.id
                        ? "font-semibold text-amber-600 dark:text-amber-400"
                          : statusTone(selected?.status ?? "pending")
                      }`}>
                        {runState === "paused" && manifest?.waitingFor === selected?.id
                          ? "Waiting for Continue · Ready to run with current settings"
                          : `${statusLabel(selected?.status ?? "pending")}${selected?.reason ? ` · ${selected.reason}` : ""}`}
                      </p>
                      {selected?.finishedAt && <p className="mt-1 text-xs text-zinc-400 dark:text-zinc-500">Processed {formatTimestamp(selected.finishedAt)}</p>}
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      {selected?.id === "translation" && (
                        <div className="flex items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-2 py-1 dark:border-zinc-700 dark:bg-zinc-850">
                          <Icon icon="carbon:language" className="h-3.5 w-3.5 text-zinc-400" />
                          <label htmlFor="stage-inline-translator" className="text-xs font-medium text-zinc-500 dark:text-zinc-400 sr-only">Translator Provider</label>
                          <select
                            id="stage-inline-translator"
                            value={settings.translator}
                            onChange={(event) => updateSettings("translator", event.target.value)}
                            className="bg-transparent text-xs font-medium text-zinc-800 outline-none dark:text-zinc-200"
                            title="Select translator provider for this step"
                          >
                            <option value="original">Original (No Translation)</option>
                            {Object.entries(translatorGroups).map(([group, list]) => (
                              <optgroup key={group} label={group}>
                                {list.map((item) => (
                                  <option key={item.key} value={item.key}>{item.name}</option>
                                ))}
                              </optgroup>
                            ))}
                          </select>
                        </div>
                      )}
                      {selected?.durationMs != null && <span className="font-mono text-xs text-zinc-500">{selected.durationMs} ms</span>}
                      {selected && <button type="button" onClick={() => void openStageDetails(selected)} className="inline-flex h-7 w-7 items-center justify-center rounded-md text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100" title="View stage details" aria-label={`View ${selected.label} details`}><Icon icon="carbon:view" className="h-4 w-4" /></button>}
                      {selected?.id !== "input" && folder && runState !== "running" && (selected?.status === "completed" || selected?.status === "failed") && (
                        <button
                          type="button"
                          onClick={() => void handleRetryStep(selected.id)}
                          disabled={retryingStage !== null}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 bg-zinc-50 px-2.5 py-1 text-xs font-medium text-black transition-all hover:border-amber-400 hover:bg-amber-50 hover:text-amber-900 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-800 dark:text-white dark:hover:border-amber-600 dark:hover:bg-amber-950/40 dark:hover:text-amber-200"
                          title={`Re-run ${selected.label} using the currently configured values`}
                        >
                          <Icon
                            icon={retryingStage === selected.id ? "carbon:progress-bar" : "carbon:renew"}
                            className={retryingStage === selected.id ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5 text-amber-500"}
                          />
                          <span>{retryingStage === selected.id ? "Retrying…" : "Retry step"}</span>
                        </button>
                      )}
                    </div>
                  </div>
                  {artifactData !== null && (
                    <div className="mt-4 overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-800">
                      <div className="border-b border-zinc-200 bg-zinc-50 px-3 py-2 text-xs font-semibold uppercase tracking-wider text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950/60">Structured data · {jsonArtifact}</div>
                      {Array.isArray(artifactData) ? (
                        <div className="max-h-48 divide-y divide-zinc-100 overflow-auto text-xs dark:divide-zinc-800">
                          {artifactData.map((row, index) => <div key={index} className="grid grid-cols-[32px_1fr] gap-2 px-3 py-2"><span className="font-mono text-zinc-400">{index + 1}</span><span className="break-words text-zinc-700 dark:text-zinc-300">{typeof row === "string" ? row : JSON.stringify(row)}</span></div>)}
                        </div>
                      ) : <pre className="max-h-48 overflow-auto p-3 text-xs leading-relaxed text-zinc-600 dark:text-zinc-300">{JSON.stringify(artifactData, null, 2)}</pre>}
                    </div>
                  )}
                </div>
              </div>
            </div>
          </section>

          <aside className="space-y-4 xl:contents">
            <details className="group overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900/70 xl:col-span-2 xl:order-first">
              <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-500 [&::-webkit-details-marker]:hidden">
                <span>
                  <span className="block text-xs font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">Settings</span>
                  <span className="mt-1 block text-sm text-zinc-500 dark:text-zinc-400">Advanced engine controls</span>
                </span>
                <Icon icon="carbon:chevron-down" className="h-4 w-4 text-zinc-500 transition-transform group-open:rotate-180 dark:text-zinc-400" />
              </summary>
              <div className="grid gap-4 border-t border-zinc-100 p-4 dark:border-zinc-800 xl:grid-cols-[repeat(5,minmax(0,1fr))]">
                <div className="col-span-full flex justify-end border-b border-zinc-100 pb-3 dark:border-zinc-800">
                  <button type="button" onClick={resetDefaults} className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100">
                    <Icon icon="carbon:reset" className="h-3.5 w-3.5" />
                    Reset
                  </button>
                </div>

              {/* Translation & Language */}
              <div className="min-w-0 space-y-3 xl:col-span-1">
                <div className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                  <Icon icon="carbon:language" className="h-3.5 w-3.5" />
                  <span>Translation & Language</span>
                </div>
                <div className="grid grid-cols-2 gap-2.5">
                  <Field label="Target" tooltip="Language into which text in comic speech bubbles will be translated">
                    <select className={fieldClass} value={settings.targetLanguage} onChange={(event) => updateSettings("targetLanguage", event.target.value)}>
                      {languageOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                    </select>
                  </Field>
                  <Field label="Translator" tooltip="Neural translation backend (Offline local models or cloud API providers)">
                    <select className={fieldClass} value={settings.translator} onChange={(event) => updateSettings("translator", event.target.value)}>
                      <option value="original">Original (No Translation)</option>
                      {Object.entries(translatorGroups).map(([group, list]) => (
                        <optgroup key={group} label={group}>
                          {list.map((item) => (
                            <option key={item.key} value={item.key}>{item.name}</option>
                          ))}
                        </optgroup>
                      ))}
                    </select>
                  </Field>
                  <Field label="Text direction" tooltip="Orientation of translated font rendering inside speech bubbles">
                    <select className={fieldClass} value={settings.renderTextDirection} onChange={(event) => updateSettings("renderTextDirection", event.target.value)}>
                      <option value="auto">Auto (Detect)</option>
                      <option value="vertical">Vertical (Manga)</option>
                      <option value="horizontal">Horizontal (Webtoon)</option>
                    </select>
                  </Field>
                  <Field label="Lettering case" tooltip="Turn translated text into ALL CAPS, lowercase, or keep original mixed case">
                    <select className={fieldClass} value={settings.letterCase} onChange={(event) => updateSettings("letterCase", event.target.value as "none" | "uppercase" | "lowercase")}>
                      <option value="none">Original (Mixed case)</option>
                      <option value="uppercase">ALL CAPS (Uppercase)</option>
                      <option value="lowercase">lowercase</option>
                    </select>
                  </Field>
                  <Field label="Renderer" tooltip="Typesetting renderer for font placement and balloon text">
                    <select className={fieldClass} value={settings.renderer} onChange={(event) => updateSettings("renderer", event.target.value)}>
                      {[["default", "Default"], ["manga2eng", "Manga2Eng"], ["manga2eng_pillow", "Pillow"], ["none", "Pass-through"]].map(([value, label]) => (
                        <option key={value} value={value}>{label}</option>
                      ))}
                    </select>
                  </Field>
                </div>
              </div>

              {/* Colorization Row / Card */}
              <div className="min-w-0 rounded-xl border border-purple-200/60 bg-purple-50/40 p-3 dark:border-purple-900/40 dark:bg-purple-950/20 xl:col-span-1 xl:border-l-0">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-purple-700 dark:text-purple-300">
                    <Icon icon="carbon:color-palette" className="h-3.5 w-3.5" />
                    <span>Colorization</span>
                  </div>
                  <label className="flex cursor-pointer items-center space-x-1.5 select-none text-xs font-medium text-purple-700 dark:text-purple-300">
                    <input
                      type="checkbox"
                      checked={settings.colorizeOnly}
                      onChange={(e) => {
                        const checked = e.target.checked;
                        updateSettings("colorizeOnly", checked);
                        if (checked && settings.colorizer === "none") {
                          updateSettings("colorizer", "mc2");
                        }
                      }}
                      className="h-3.5 w-3.5 rounded border-zinc-300 text-purple-600 focus:ring-purple-500 dark:border-zinc-600"
                    />
                    <span className={settings.colorizeOnly ? "font-semibold text-purple-600 dark:text-purple-400" : ""}>
                      Colorize Only
                    </span>
                  </label>
                </div>
                <div className="mt-2.5 grid grid-cols-2 gap-2.5">
                  <Field label="Colorizer" tooltip="MC2 colorizes black and white manga pages with vibrant colors">
                    <select
                      className={fieldClass}
                      value={settings.colorizer}
                      onChange={(event) => {
                        const val = event.target.value;
                        updateSettings("colorizer", val);
                        if (val === "none") updateSettings("colorizeOnly", false);
                      }}
                    >
                      {colorizerOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                    </select>
                  </Field>
                  <Field label="Color size" tooltip="Internal processing resolution for coloring">
                    <select className={fieldClass} value={settings.colorizationSize} onChange={(event) => updateSettings("colorizationSize", event.target.value)}>
                      {colorizationSizes.map((value) => <option key={value} value={String(value)}>{value}px</option>)}
                    </select>
                  </Field>
                  <Field label="Denoise Sigma" tooltip="Strength of denoising filter applied during colorization (0–255, -1 to disable)">
                    <input
                      className={fieldClass}
                      type="number"
                      step="1"
                      value={settings.denoiseSigma}
                      onChange={(event) => updateSettings("denoiseSigma", Number(event.target.value))}
                    />
                  </Field>
                  <Field label="Tolerance" tooltip="CIELAB color distance tolerance to skip already colored pages (default 31, -1 to disable)">
                    <input
                      className={fieldClass}
                      type="number"
                      step="1"
                      value={settings.colorThreshold}
                      onChange={(event) => updateSettings("colorThreshold", Number(event.target.value))}
                    />
                  </Field>
                </div>
              </div>

              {/* Detection & OCR Group */}
              <div className="min-w-0 space-y-3 border-t border-zinc-100 pt-3 dark:border-zinc-800 xl:mt-0 xl:border-l xl:border-t-0 xl:pl-4">
                <div className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                  <Icon icon="carbon:search-locate" className="h-3.5 w-3.5" />
                  <span>Detection & OCR</span>
                </div>
                <div className="grid grid-cols-2 gap-2.5">
                  <Field label="Detector" tooltip="CTD is optimized for stylized manga fonts. Paddle is great for webtoons.">
                    <select className={fieldClass} value={settings.textDetector} onChange={(event) => updateSettings("textDetector", event.target.value)}>
                      {textDetectorOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                    </select>
                  </Field>
                  <Field label="Detect size" tooltip="Input resolution for OCR detector. Higher values improve small text recognition.">
                    <select className={fieldClass} value={settings.detectionResolution} onChange={(event) => updateSettings("detectionResolution", event.target.value)}>
                      {detectionResolutions.map((value) => <option key={value} value={String(value)}>{value}px</option>)}
                    </select>
                  </Field>
                  <Field label="OCR Engine" tooltip="Optical Character Recognition model for transcribing Japanese/Korean text">
                    <select className={fieldClass} value={settings.ocr} onChange={(event) => updateSettings("ocr", event.target.value)}>
                      {ocrOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                    </select>
                  </Field>
                  <Field label="Box Threshold" tooltip="Confidence threshold (0.0–1.0) for detecting text boxes. Lower catches faint text.">
                    <input className={fieldClass} type="number" min="0" max="1" step="0.01" value={settings.customBoxThreshold} onChange={(event) => updateSettings("customBoxThreshold", Number(event.target.value))} />
                  </Field>
                  <Field label="OCR Min Confidence" tooltip="Minimum confidence threshold (0.0–1.0) for OCR text recognition. Drop regions whose OCR confidence is below this score.">
                    <input className={fieldClass} type="number" min="0" max="1" step="0.05" placeholder="Default" value={settings.customOcrProb !== undefined ? settings.customOcrProb : ""} onChange={(event) => updateSettings("customOcrProb", event.target.value ? Number(event.target.value) : undefined)} />
                  </Field>
                  <div className="col-span-2">
                    <Field label="Unclip Ratio" tooltip="Expansion ratio around detected text lines. Prevents cutting off font descenders.">
                      <input className={fieldClass} type="number" step="0.05" value={settings.customUnclipRatio} onChange={(event) => updateSettings("customUnclipRatio", Number(event.target.value))} />
                    </Field>
                  </div>
                </div>
              </div>

              {/* Inpainting & Mask Group */}
              <div className="min-w-0 space-y-3 border-t border-zinc-100 pt-3 dark:border-zinc-800 xl:mt-0 xl:border-l xl:border-t-0 xl:pl-4">
                <div className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                  <Icon icon="carbon:paint-brush" className="h-3.5 w-3.5" />
                  <span>Inpainting & Mask</span>
                </div>
                <div className="grid grid-cols-2 gap-2.5">
                  <Field label="Inpainter" tooltip="Lama Large produces clean background reconstruction behind removed text.">
                    <select className={fieldClass} value={settings.inpainter} onChange={(event) => updateSettings("inpainter", event.target.value)}>
                      {inpainterOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                    </select>
                  </Field>
                  <Field label="Inpaint size" tooltip="Resolution at which inpainter neural network fills background textures.">
                    <select className={fieldClass} value={settings.inpaintingSize} onChange={(event) => updateSettings("inpaintingSize", event.target.value)}>
                      {inpaintingSizes.map((value) => <option key={value} value={String(value)}>{value}px</option>)}
                    </select>
                  </Field>
                  <div className="col-span-2">
                    <Field label="Mask Dilation" tooltip="Pixel padding expanding around characters to erase original text edges completely.">
                      <input className={fieldClass} type="number" step="1" value={settings.maskDilationOffset} onChange={(event) => updateSettings("maskDilationOffset", Number(event.target.value))} />
                    </Field>
                  </div>
                </div>
              </div>

              {/* Upscaling */}
              <div className="min-w-0 border-t border-zinc-100 pt-3 dark:border-zinc-800 xl:mt-0 xl:border-l xl:border-t-0 xl:pl-4">
                <label className="flex items-center justify-between text-xs font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                  <span className="flex items-center gap-1.5">
                    <Icon icon="carbon:zoom-in" className="h-3.5 w-3.5" />
                    <span>Upscaling</span>
                  </span>
                  <input type="checkbox" checked={stagePlan.upscaling} onChange={() => toggleStage("upscaling")} className="h-4 w-4 accent-teal-500" />
                </label>
                <div className="mt-2.5 grid grid-cols-2 gap-2.5">
                  <Field label="Model" tooltip="Super-resolution neural model for upscaling">
                    <select className={fieldClass} value={settings.upscaler} onChange={(event) => updateSettings("upscaler", event.target.value)} disabled={!stagePlan.upscaling}>
                      <option value="4xultrasharp">4x UltraSharp</option>
                      <option value="esrgan">ESRGAN</option>
                      <option value="waifu2x">Waifu2x</option>
                    </select>
                  </Field>
                  <Field label="Ratio" tooltip="Upscale multiplier factor (2x to 4x)">
                    <input className={fieldClass} type="number" min="2" max="4" step="1" value={settings.upscaleRatio} onChange={(event) => updateSettings("upscaleRatio", Number(event.target.value))} disabled={!stagePlan.upscaling} />
                  </Field>
                </div>
                <label className={`mt-2 flex items-center gap-2 text-xs font-medium ${stagePlan.upscaling ? "text-zinc-600 dark:text-zinc-300 cursor-pointer" : "text-zinc-400 dark:text-zinc-500 opacity-60 cursor-not-allowed"}`}>
                  <input
                    type="checkbox"
                    checked={stagePlan.upscaling && settings.revertUpscaling}
                    disabled={!stagePlan.upscaling}
                    onChange={(event) => updateSettings("revertUpscaling", event.target.checked)}
                    className="h-4 w-4 accent-teal-500 disabled:opacity-50"
                  />
                  <span title="Downscale the final result back to the original image dimensions">Return final image to original size</span>
                </label>
              </div>
              </div>
            </details>

            <div className="rounded-2xl border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900/70 xl:col-span-2">
              <button type="button" onClick={() => setHistoryOpen((value) => !value)} className="flex w-full items-center justify-between text-left">
                <span><span className="block text-xs font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">Run history</span><span className="mt-1 block text-sm text-zinc-500 dark:text-zinc-400">{history.length} saved diagnostic bundle{history.length === 1 ? "" : "s"}</span></span>
                <Icon icon={historyOpen ? "carbon:chevron-up" : "carbon:chevron-down"} className="h-4 w-4 text-zinc-400" />
              </button>
              {historyOpen && <div className="mt-3 space-y-2">
                {historyLoading && <p className="text-xs text-zinc-400">Loading history…</p>}
                {!historyLoading && history.length === 0 && <p className="rounded-lg bg-zinc-50 p-3 text-xs leading-relaxed text-zinc-500 dark:bg-zinc-950/60 dark:text-zinc-400">Completed and partial runs will appear here.</p>}
                {history.map((item) => <div key={item.folder} className="rounded-lg border border-zinc-200 p-2.5 dark:border-zinc-800">
                  <button type="button" onClick={() => void loadRun(item)} className="block w-full text-left"><span className="block truncate text-sm font-medium">{item.filename}</span><span className={`mt-1 block text-xs ${statusTone(item.status)}`}>{statusLabel(item.status)} · {new Date(item.updatedAt).toLocaleString()}</span></button>
                  <div className="mt-2 flex gap-2"><button type="button" onClick={() => void loadRun(item, true)} className="text-xs font-medium text-indigo-600 hover:text-indigo-500 dark:text-indigo-300">Rerun</button><button type="button" onClick={() => void deleteRun(item)} className="text-xs font-medium text-zinc-500 hover:text-rose-600 dark:text-zinc-400">Delete bundle</button></div>
                </div>)}
              </div>}
            </div>

            {manifest && <div className="rounded-2xl border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900/70 xl:col-span-2">
              <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">Run metadata</p>
              <dl className="mt-3 space-y-2 text-xs"><div className="flex justify-between gap-3"><dt className="text-zinc-500">Folder</dt><dd className="max-w-[170px] truncate font-mono text-zinc-700 dark:text-zinc-300">{manifest.folder}</dd></div><div className="flex justify-between gap-3"><dt className="text-zinc-500">Source</dt><dd className="font-mono text-zinc-700 dark:text-zinc-300">{manifest.source.width} × {manifest.source.height}</dd></div><div className="flex justify-between gap-3"><dt className="text-zinc-500">Started</dt><dd className="text-zinc-700 dark:text-zinc-300">{manifest.createdAt ? new Date(manifest.createdAt).toLocaleString() : "—"}</dd></div></dl>
              {artifacts.length > 0 && <div className="mt-3 border-t border-zinc-100 pt-3 dark:border-zinc-800"><p className="mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-500">Artifacts</p><div className="space-y-1">{artifacts.map((name) => folder && <a key={name} href={artifactUrl(folder, name)} target="_blank" rel="noreferrer" className="flex items-center gap-1.5 truncate text-xs text-indigo-600 hover:underline dark:text-indigo-300"><Icon icon={name.endsWith(".json") ? "carbon:json" : "carbon:image"} className="h-3.5 w-3.5 shrink-0" />{name}</a>)}</div></div>}
            </div>}
          </aside>
        </section>
      </main>
      {stageDetailStage && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-xs" role="presentation">
          <section className="max-h-[min(720px,calc(100vh-2rem))] w-full max-w-3xl overflow-y-auto rounded-2xl border border-zinc-200 bg-white p-5 shadow-2xl dark:border-zinc-700 dark:bg-zinc-900" role="dialog" aria-modal="true" aria-labelledby="stage-detail-title">
            <div className="flex items-start justify-between gap-4 border-b border-zinc-100 pb-4 dark:border-zinc-800">
              <div>
                <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">Stage details</p>
                <h2 id="stage-detail-title" className="mt-1 text-xl font-semibold">{stageDetailStage.label}</h2>
              </div>
              <button type="button" onClick={() => setStageDetailStage(null)} className="rounded-md p-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 dark:hover:bg-zinc-800 dark:hover:text-zinc-100" title="Close details" aria-label="Close details"><Icon icon="carbon:close" className="h-5 w-5" /></button>
            </div>
            <dl className="grid gap-3 py-4 text-sm sm:grid-cols-3">
              <div><dt className="text-xs text-zinc-500">Status</dt><dd className={`mt-1 font-medium ${statusTone(stageDetailStage.status)}`}>{statusLabel(stageDetailStage.status)}</dd></div>
              <div><dt className="text-xs text-zinc-500">Started</dt><dd className="mt-1 text-zinc-700 dark:text-zinc-300">{formatTimestamp(stageDetailStage.startedAt)}</dd></div>
              <div><dt className="text-xs text-zinc-500">Processed</dt><dd className="mt-1 text-zinc-700 dark:text-zinc-300">{formatTimestamp(stageDetailStage.finishedAt)}</dd></div>
            </dl>
            {stageDetailStage.reason && <p className="mb-4 rounded-lg bg-zinc-50 px-3 py-2 text-sm text-zinc-600 dark:bg-zinc-950/60 dark:text-zinc-300">{stageDetailStage.reason}</p>}
            {stageDetailStage.id === "translation" && stageDetailLoading && <p className="py-6 text-sm text-zinc-500">Loading translator details…</p>}
            {stageDetailStage.id === "translation" && !stageDetailLoading && stageDetailData && (
              <div className="space-y-4">
                <div className="grid gap-3 rounded-xl border border-zinc-200 bg-zinc-50 p-3 text-sm dark:border-zinc-800 dark:bg-zinc-950/50 sm:grid-cols-4">
                  {Object.entries((stageDetailData.translator as Record<string, unknown>) || {}).map(([label, value]) => <div key={label} className="min-w-0"><dt className="text-xs capitalize text-zinc-500">{label.replace(/[A-Z]/g, (letter) => ` ${letter.toLowerCase()}`)}</dt><dd className="mt-1 truncate font-medium text-zinc-800 dark:text-zinc-200" title={String(value ?? "Not recorded")}>{String(value ?? "Not recorded")}</dd></div>)}
                </div>
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="overflow-hidden rounded-xl border border-zinc-200 dark:border-zinc-800"><div className="border-b border-zinc-200 bg-zinc-50 px-3 py-2 text-xs font-semibold uppercase tracking-wider text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950/50">Translator request</div><pre className="max-h-64 overflow-auto p-3 text-xs leading-relaxed text-zinc-700 dark:text-zinc-300">{JSON.stringify(stageDetailData.request ?? [], null, 2)}</pre></div>
                  <div className="overflow-hidden rounded-xl border border-zinc-200 dark:border-zinc-800"><div className="border-b border-zinc-200 bg-zinc-50 px-3 py-2 text-xs font-semibold uppercase tracking-wider text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950/50">Translator response</div><pre className="max-h-64 overflow-auto p-3 text-xs leading-relaxed text-zinc-700 dark:text-zinc-300">{JSON.stringify(stageDetailData.response ?? [], null, 2)}</pre></div>
                </div>
              </div>
            )}
            {stageDetailStage.id === "translation" && !stageDetailLoading && !stageDetailData && <p className="py-6 text-sm text-zinc-500">No translator detail was saved for this run.</p>}
            {stageDetailStage.artifacts && stageDetailStage.artifacts.length > 0 && <div className="mt-4 border-t border-zinc-100 pt-4 dark:border-zinc-800"><p className="mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-500">Artifacts</p><div className="flex flex-wrap gap-x-4 gap-y-2">{stageDetailStage.artifacts.map((name) => folder && <a key={name} href={artifactUrl(folder, name)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-xs text-indigo-600 hover:underline dark:text-indigo-300"><Icon icon={name.endsWith(".json") ? "carbon:json" : "carbon:image"} className="h-3.5 w-3.5" />{name}</a>)}</div></div>}
          </section>
        </div>
      )}
    </div>
  );
}
