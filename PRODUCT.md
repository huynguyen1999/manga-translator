# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Manga, manhwa, comic readers, and scanlators who need fast, automated visual translation for Japanese, Korean, Chinese, and other comic formats directly in their browser.

## Product Purpose

Seamlessly translate text in comic and manga pages with accurate OCR detection, clean speech bubble inpainting, and localized typography rendering using multiple translation engines (local offline models and online APIs).

## Operating Context

A responsive web studio running in modern browsers connected to the manga-image-translator FastAPI backend (/api/translate/with-form/image/stream). Users inspect single images or batch queue whole chapters, needing quick before/after comparison to verify accuracy.

## Capabilities and Constraints

- Streaming Pipeline: Real-time status updates from backend chunks: detection, OCR, inpainting, translating, rendering.
- Engines: Offline (Sugoi, NLLB, M2M100, Qwen2) and Online (DeepL, OpenAI, Groq, Gemini, DeepSeek, Papago, Baidu, Youdao).
- Text Detectors & Inpainters: CTD, Paddle, Default; Inpainters include Lama Large, Lama MPE, SD, None, Original.
- Constraints: Preserves image aspect ratios, supports clipboard paste, prevents memory leaks with Blob ObjectURLs.

## Product Principles

1. Comparison First: Reading translated manga requires easy before/after verification of text bubbles against original raw art.
2. Speed & Clarity: Streamlined controls with sensible presets for manga vs webtoon; advanced neural parameters remain accessible without cluttering basic tasks.
3. Robust Workflow: Support single-page quick paste as well as multi-image batch queue processing with resilient error handling.
