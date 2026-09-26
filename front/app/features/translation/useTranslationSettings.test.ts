import assert from "node:assert/strict";
import React, { useState } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { TranslationSettings } from "@/types";
import { useTranslationSettings } from "./useTranslationSettings";

const values = new Map<string, string>();
const localStorage = {
  getItem: (key: string) => values.get(key) ?? null,
  setItem: (key: string, value: string) => values.set(key, value),
  removeItem: (key: string) => values.delete(key),
} as unknown as Storage;
const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
Object.defineProperty(globalThis, "window", {
  configurable: true,
  value: { localStorage },
});

values.set("manga-translator-settings", JSON.stringify({
  rememberSettings: true,
  detectionResolution: "1536",
  textDetector: "ctd",
  ocr: "48px",
  renderFont: "wildwords",
  renderTextDirection: "vertical",
  uppercase: true,
  translator: "youdao",
  migratedDefaultTranslator: false,
  summaryModel: "deepseek-flash",
  targetLanguage: "CHS",
  migratedDefaultTargetLang: false,
  customOcrProb: 0.25,
  ocrMinConfidence: 0.9,
  bubbleDetection: false,
  migratedDefaultBubbleDetection: false,
  upscaleRatio: 2.5,
  revertUpscaling: true,
  translationBatchSize: 200,
} satisfies Partial<TranslationSettings>));

let observed!: ReturnType<typeof useTranslationSettings>;
function Harness() {
  const [hydrated, setHydrated] = useState(false);
  const settings = useTranslationSettings();
  observed = settings;
  if (!hydrated) {
    setHydrated(true);
    settings.hydrateSettings();
  }
  return null;
}

try {
  renderToStaticMarkup(React.createElement(Harness));
  const current = observed.getCurrentSettings();
  assert.equal(observed.rememberSettings, true);
  assert.equal(current.detectionResolution, "1536");
  assert.equal(current.textDetector, "ctd");
  assert.equal(current.letterCase, "uppercase");
  assert.equal(current.translator, "deepseek");
  assert.equal(current.targetLanguage, "ENG");
  assert.equal(current.customOcrProb, 0.25);
  assert.equal(current.bubbleDetection, true);
  assert.equal(current.upscaleRatio, 2.5);
  assert.equal(current.revertUpscaling, true);
  assert.equal(current.translationBatchSize, 100);

  observed.persistSettings();
  const saved = JSON.parse(values.get("manga-translator-settings") || "{}") as Partial<TranslationSettings>;
  assert.equal(saved.rememberSettings, true);
  assert.equal(saved.translator, "deepseek");
  assert.equal(saved.targetLanguage, "ENG");
  assert.equal(saved.translationBatchSize, 100);
  assert.equal(saved.migratedDefaultBubbleDetection, true);
  assert.equal(saved.renderFont, undefined);
} finally {
  if (originalWindow) {
    Object.defineProperty(globalThis, "window", originalWindow);
  } else {
    Reflect.deleteProperty(globalThis, "window");
  }
}

console.log("translation settings contracts passed");
