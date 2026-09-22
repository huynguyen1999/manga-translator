---
name: Manga Translator Web Studio
description: Professional AI Manga & Webtoon Translation Studio
colors:
  primary: "#6366f1"
  primary-hover: "#4f46e5"
  primary-subtle: "#eef2ff"
  neutral-bg: "#fafafa"
  neutral-surface: "#ffffff"
  neutral-border: "#e4e4e7"
  neutral-text: "#18181b"
  neutral-muted: "#71717a"
  dark-bg: "#09090b"
  dark-surface: "#18181b"
  dark-border: "#27272a"
  dark-text: "#f4f4f5"
  dark-muted: "#a1a1aa"
  success: "#10b981"
  warning: "#f59e0b"
  danger: "#ef4444"
typography:
  headline:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  body:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  label:
    fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "0.02em"
rounded:
  sm: "6px"
  md: "10px"
  lg: "16px"
  full: "9999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "#ffffff"
    rounded: "{rounded.md}"
    padding: "8px 16px"
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
---

# Design System: Manga Translator Web Studio

## Overview

**Creative North Star: "The Scanlator's Lightbox"**

A high-productivity, tactile translation studio tailored for manga and webtoon scanlators, readers, and localizers. Rather than treating image translation like a generic form submit, the interface functions as a specialized graphics workstation: crisp panel previews, responsive side-by-side or split before/after verification, immediate clipboard pasting (`⌘V`), and progressive disclosure of deep neural network hyperparameters.

The interface stays neutral and unobtrusive so that the comic artwork itself commands attention. Chromes are sculpted with crisp 1px borders, subtle surface elevations, and an electric Iris/Indigo accent reserved strictly for states and primary translation actions.

**Key Characteristics:**
- **Comparison-First Architecture**: Interactive before/after split slider and toggle built directly into previews and lightbox.
- **Progressive Disclosure**: High-level presets ("Classic Manga", "Webtoon", "High Quality", "Fast") sit up front; deep model parameters (unclip, box threshold, mask dilation) live in collapsible accordions with tooltips.
- **Dual-Mode Fluidity**: Single-page quick paste mode flows naturally into batch chapter queue processing.
- **Theme Fidelity**: Polished light and dark themes with WCAG AA compliance (≥ 4.5:1 text contrast).

## Colors

A restrained neutral foundation (Zinc scale) punctuated by an energetic Iris accent for focus, active states, and translation executions.

### Primary
- **Electric Iris** (`#6366f1` / `#4f46e5`): Interactive highlights, primary execution buttons, and active phase badges.

### Neutral
- **Canvas Light** (`#fafafa`) / **Canvas Dark** (`#09090b`): High-comfort background surfaces.
- **Card Surface** (`#ffffff` / `#18181b`): Lifted panels with crisp definition.
- **Borders & Dividers** (`#e4e4e7` / `#27272a`): 1px structural boundaries.
- **High-Contrast Text** (`#18181b` / `#f4f4f5`): Crisp readable typography.
- **Muted Label Text** (`#71717a` / `#a1a1aa`): Secondary descriptions with contrast ≥ 4.5:1.

### Semantic
- **Emerald Success** (`#10b981`): Translation complete and verified.
- **Amber Warning** (`#f59e0b`): Queued / awaiting processing.
- **Rose Danger** (`#ef4444`): OCR/Network errors and destructive actions.

### Named Rules
**The Artwork-First Rule.** Never place high-saturation color cards behind previewed manga artwork. Canvases and card containers must remain neutral zinc/slate so the art's original tones are never contaminated.

## Typography

**Display Font:** Inter, ui-sans-serif, system-ui, sans-serif
**Body Font:** Inter, ui-sans-serif, system-ui, sans-serif
**Mono Font:** ui-monospace, SFMono-Regular, Menlo, Monaco, monospace

### Hierarchy
- **Headline** (Bold 700, 1.5rem, line-height 1.2, tracking -0.02em): Studio section headers.
- **Title** (Semibold 600, 1.125rem, line-height 1.3, tracking -0.01em): Image card headers and panel titles.
- **Body** (Regular 400, 0.875rem, line-height 1.5): Descriptions and status notes.
- **Label** (Semibold 600, 0.75rem, line-height 1.2, tracking 0.02em): Form input headers and step pills.

## Layout

A responsive studio workspace capped at `max-w-7xl` with 16px–24px outer margins:
- Sticky header with branding, real-time pipeline status, active preset indicators, and theme switcher.
- Quick Translation Bar with source/target language picker, translator engine, orientation, and preset buttons.
- Collapsible "Advanced Engine Settings" drawer.
- Dual-mode Studio view: visual comparison workspace and batch queue manager.
- Full-viewport comparison lightbox with keyboard shortcuts.

## Elevation & Depth

Surfaces rely on structural 1px borders (`border-zinc-200 dark:border-zinc-800`) paired with subtle, natural drop shadows (`shadow-sm` and `shadow-md`). No muddy heavy shadows or artificial blur halos.

## Shapes

- Small controls (chips, badges, buttons): `rounded-md` (6px to 8px) or `rounded-full` for status pills.
- Containers, cards, and modal windows: `rounded-xl` (12px to 16px).
- Preview viewports: `rounded-lg` with `overflow-hidden`.

## Components

### Buttons
- **Primary**: Solid Indigo (`bg-indigo-600 hover:bg-indigo-500 text-white shadow-sm`), 150ms ease-out, accessible focus ring.
- **Secondary**: Subtle border (`border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-800 text-zinc-800 dark:text-zinc-200 hover:bg-zinc-50 dark:hover:bg-zinc-700/60`).
- **Ghost / Destructive**: Tonal hover (`hover:bg-red-50 dark:hover:bg-red-950/40 text-zinc-500 hover:text-red-600 dark:hover:text-red-400`).

### Interactive Preview Cards
- Dual view toggle (Original vs Translated).
- Split comparison slider for live before/after reveal.
- Pipeline progress indicator badge displaying active neural network step (`OCR`, `Inpainting`, `Translating`, `Rendering`).

### Lightbox Modal
- Fullscreen dark backdrop (`bg-black/85 backdrop-blur-sm`).
- Side-by-side or split view comparison.
- Arrow key navigation (`←` / `→`) and keyboard exit (`Esc`).

## Do's and Don'ts

### Do:
- **Do** allow instant comparison between original and translated comic pages.
- **Do** support pasting directly from clipboard (`⌘V` / `Ctrl+V`).
- **Do** maintain a strict 4.5:1 text contrast ratio in both light and dark modes.
- **Do** provide presets for Manga vs Webtoon formats.

### Don't:
- **Don't** stack two separate drag-and-drop zones on top of each other.
- **Don't** use low-contrast gray text on tinted backgrounds (`text-gray-600 on bg-red-50`).
- **Don't** hide the translation error message or make failed files impossible to retry.
