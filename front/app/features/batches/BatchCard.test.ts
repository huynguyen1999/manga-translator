import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import type { TranslationBatch } from "@/types";
import { BatchCard } from "./BatchCard";

const batch: TranslationBatch = {
  id: "waiting-batch",
  addedAt: new Date(0),
  mangaTitle: "Chapter 1",
  settings: { translator: "deepseek", inpainter: "lama" } as TranslationBatch["settings"],
  items: [],
  totalItems: 1,
  completedCount: 0,
  status: "waiting",
};

const markup = renderToStaticMarkup(React.createElement(MemoryRouter, null,
  React.createElement(BatchCard, {
    batch,
    onLoadDetails: async () => {},
    onDismiss: () => {},
    onRemove: () => {},
    onRetryItem: () => {},
    onRemoveItem: () => {},
    onTranslatorChange: () => {},
    onManualReviewChange: () => {},
    onPriorityChange: () => {},
    isActionPending: () => false,
    runAction: () => {},
  }),
));

assert.match(markup, /job-card job-offscreen-row/);
assert.match(markup, /Chapter 1/);
assert.match(markup, /Translation/);
assert.match(markup, /aria-label="Remove waiting batch Chapter 1"/);

console.log("batch card contracts passed");
