import assert from "node:assert/strict";
import {
  buildPipelineLabConfig,
  decodePipelineFrames,
  defaultPipelineLabSettings,
  DEFAULT_STAGE_PLAN,
  stageFromProgress,
  stageFromManualWait,
} from "@/utils/pipelineLab";

const settings = defaultPipelineLabSettings();
assert.equal(settings.detectionResolution, "2048");
assert.equal(settings.ocr, "48px");
assert.equal(settings.translator, "deepseek");
assert.equal(settings.colorizer, "mc2");
assert.equal(settings.colorizeOnly, false);
assert.equal(settings.customBoxThreshold, 0.5);
assert.equal(settings.customUnclipRatio, 2.3);
assert.equal(settings.maskDilationOffset, 20);
assert.equal(settings.denoiseSigma, 25);
assert.equal(settings.colorThreshold, 31);
assert.equal(settings.letterCase, "none");
assert.equal(settings.revertUpscaling, true);
assert.equal(settings.bubbleDetection, true);
assert.equal(DEFAULT_STAGE_PLAN.upscaling, false);

const config = buildPipelineLabConfig(settings, { ...DEFAULT_STAGE_PLAN, upscaling: false }, "page.png");
assert.equal(config.original_name, "page.png");
assert.equal(config.detector.detector, "default");
assert.equal(config.ocr.ocr, "48px");
assert.equal(buildPipelineLabConfig({ ...settings, ocr: "mocr" }, DEFAULT_STAGE_PLAN, "page.png").ocr.ocr, "mocr");
assert.equal(buildPipelineLabConfig({ ...settings, letterCase: "uppercase" }, DEFAULT_STAGE_PLAN, "page.png").render.uppercase, true);
assert.equal(buildPipelineLabConfig({ ...settings, letterCase: "lowercase" }, DEFAULT_STAGE_PLAN, "page.png").render.lowercase, true);
assert.equal(config.detector.box_threshold, 0.5);
assert.equal(config.detector.unclip_ratio, 2.3);
assert.equal(config.mask_dilation_offset, 20);
assert.equal(config.colorizer.colorizer, "mc2");
assert.equal(config.colorizer.denoise_sigma, 25);
assert.equal(config.colorizer.color_threshold, 31);
assert.equal(config.upscale.upscale_ratio, null);
assert.equal(config.upscale.revert_upscaling, false);
assert.equal(config.pipeline_lab.stage_plan.upscaling, false);
assert.equal(config.pipeline_lab.manual, false);

const fullUpscaleConfig = buildPipelineLabConfig(
  { ...settings, revertUpscaling: false },
  { ...DEFAULT_STAGE_PLAN, upscaling: true },
  "page.png",
);
assert.equal(fullUpscaleConfig.upscale.upscale_ratio, 2);
assert.equal(fullUpscaleConfig.upscale.revert_upscaling, false);

const revertUpscaleConfig = buildPipelineLabConfig(
  { ...settings, revertUpscaling: true },
  { ...DEFAULT_STAGE_PLAN, upscaling: true },
  "page.png",
);
assert.equal(revertUpscaleConfig.upscale.upscale_ratio, 2);
assert.equal(revertUpscaleConfig.upscale.revert_upscaling, true);
assert.equal(stageFromProgress("mask-generation"), "mask_generation");
assert.equal(stageFromProgress("mask_generation"), "mask_generation");
assert.equal(stageFromProgress("translating"), "translation");
assert.equal(stageFromProgress("translation"), "translation");
assert.equal(stageFromProgress("colorizing"), "colorization");
assert.equal(stageFromProgress("colorization"), "colorization");
assert.equal(stageFromProgress("bubble-detection"), "bubble_detection");
assert.equal(stageFromManualWait("manual_wait:bubble_detection"), "bubble_detection");
assert.equal(stageFromManualWait("manual_wait:colorization"), "colorization");
assert.equal(stageFromManualWait("manual_wait:upscaling"), "upscaling");
assert.equal(stageFromManualWait("manual_wait:detection"), "detection");
assert.equal(stageFromManualWait("manual_wait:ocr"), "ocr");
assert.equal(stageFromManualWait("manual_wait:textline_merge"), "textline_merge");
assert.equal(stageFromManualWait("manual_wait:translation"), "translation");
assert.equal(stageFromManualWait("manual_wait:mask_generation"), "mask_generation");
assert.equal(stageFromManualWait("manual_wait:inpainting"), "inpainting");
assert.equal(stageFromManualWait("manual_wait:rendering"), "rendering");

// Verify colorizeOnly behavior
const colorizeConfig = buildPipelineLabConfig({ ...settings, colorizeOnly: true }, DEFAULT_STAGE_PLAN, "page.png");
assert.equal(colorizeConfig.detector.detector, "none");
assert.equal(colorizeConfig.render.renderer, "none");
assert.equal(colorizeConfig.translator.translator, "original");
assert.equal(colorizeConfig.inpainter.inpainter, "none");
assert.equal(colorizeConfig.colorizer.colorizer, "mc2");

const payload = new TextEncoder().encode("detection");
const frame = new Uint8Array(payload.length + 5);
frame[0] = 1;
new DataView(frame.buffer).setUint32(1, payload.length);
frame.set(payload, 5);
const first = decodePipelineFrames(frame.slice(0, 3));
assert.equal(first.frames.length, 0);
const second = decodePipelineFrames(new Uint8Array([...first.remainder, ...frame.slice(3)]));
assert.equal(second.frames.length, 1);
assert.equal(new TextDecoder().decode(second.frames[0].payload), "detection");

// Verify retryPipelineStage API helper
const originalFetch = globalThis.fetch;
try {
  let capturedUrl = "";
  let capturedBody = "";
  let capturedMethod = "";
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    capturedUrl = String(input);
    capturedMethod = init?.method || "GET";
    capturedBody = String(init?.body || "");
    return new Response(JSON.stringify({
      status: "ok",
      stage: "detection",
      durationMs: 42,
      manifest: { version: 1, folder: "test_run", stages: [] }
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;

  const res = await (await import("@/utils/pipelineLab")).retryPipelineStage("test_run", "detection", { test: 123 });
  assert.equal(res.status, "ok");
  assert.equal(res.stage, "detection");
  assert.equal(res.durationMs, 42);
  assert.match(capturedUrl, /(?:\/api)?\/pipeline-lab\/runs\/test_run\/retry-step$/);
  assert.equal(capturedMethod, "POST");
  const parsed = JSON.parse(capturedBody);
  assert.equal(parsed.stage, "detection");
  assert.equal(parsed.config.test, 123);

  // Verify stopPipelineRun API helper
  let stopCapturedUrl = "";
  let stopCapturedMethod = "";
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    stopCapturedUrl = String(input);
    stopCapturedMethod = init?.method || "GET";
    return new Response(JSON.stringify({ status: "stop_requested", folder: "test_run" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  const stopRes = await (await import("@/utils/pipelineLab")).stopPipelineRun("test_run");
  assert.equal(stopRes.status, "stop_requested");
  assert.equal(stopRes.folder, "test_run");
  assert.match(stopCapturedUrl, /(?:\/api)?\/pipeline-lab\/runs\/test_run\/stop$/);
  assert.equal(stopCapturedMethod, "POST");
} finally {
  globalThis.fetch = originalFetch;
}

console.log("pipelineLab utilities: ok");
