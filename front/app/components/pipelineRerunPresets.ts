import type { PipelineRerunMode } from "@/types";

export interface PresetOption {
  id: PipelineRerunMode;
  title: string;
  badge?: string;
  description: string;
  summarySteps: string;
  details: Array<{
    stage: string;
    action: "rerun" | "reuse" | "remap";
    note?: string;
  }>;
}

export const PRESETS: PresetOption[] = [
  {
    id: "reprocess_text",
    title: "Re-detect text + preserve translations",
    badge: "Recommended for fixing OCR / inpainting",
    description: "Improve text detection, OCR, and inpainting while keeping your existing translations.",
    summarySteps: "Detection → OCR → Inpaint → Remap → Typeset",
    details: [
      { stage: "Detection", action: "rerun", note: "Re-detects all text lines with chosen detector settings" },
      { stage: "OCR", action: "rerun", note: "Extracts Japanese text using selected OCR model" },
      { stage: "Speech Bubbles", action: "reuse", note: "Reuses existing bubble shapes when available" },
      { stage: "Mask & Inpaint", action: "rerun", note: "Regenerates tight masks and cleans speech bubbles" },
      { stage: "Translation", action: "remap", note: "Remaps previous translations using geometry & bubble affinity" },
      { stage: "Typesetting", action: "rerun", note: "Lays out dialogue with shape-aware typography solver" },
    ],
  },
  {
    id: "typesetting",
    title: "Re-typeset only (Fast)",
    badge: "Instant layout refresh",
    description: "Rerender text layout with updated font, lettering case, alignment, or renderer settings. Skips OCR and inpainting.",
    summarySteps: "Reuse inpainting & translations → Shape-aware layout & render",
    details: [
      { stage: "Detection & OCR", action: "reuse", note: "Reuses existing text regions and boundaries" },
      { stage: "Inpainting", action: "reuse", note: "Keeps existing clean background canvas" },
      { stage: "Translation", action: "reuse", note: "Preserves existing translations" },
      { stage: "Typesetting", action: "rerun", note: "Re-runs typography solver with updated font and styling" },
    ],
  },
  {
    id: "translation_typesetting",
    title: "Retranslate + typeset",
    description: "Retranslate using a different AI model or target language, then typeset onto the existing inpainted canvas.",
    summarySteps: "Reuse OCR & inpainting → Retranslate → Typeset",
    details: [
      { stage: "Detection & OCR", action: "reuse", note: "Uses current OCR textlines" },
      { stage: "Inpainting", action: "reuse", note: "Keeps existing clean background" },
      { stage: "Translation", action: "rerun", note: "Queries selected translator model" },
      { stage: "Typesetting", action: "rerun", note: "Rerenders translated text into bubbles" },
    ],
  },
  {
    id: "full",
    title: "Full pipeline rerun",
    description: "Rerun everything from the original raw image (detection, OCR, inpainting, translation, and typesetting).",
    summarySteps: "Raw input → Full translation pipeline",
    details: [
      { stage: "Detection", action: "rerun" },
      { stage: "OCR", action: "rerun" },
      { stage: "Speech Bubbles", action: "rerun" },
      { stage: "Inpainting", action: "rerun" },
      { stage: "Translation", action: "rerun" },
      { stage: "Typesetting", action: "rerun" },
    ],
  },
];
