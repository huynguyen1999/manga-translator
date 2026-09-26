import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { QueuedImage } from "@/types";
import { ItemRow } from "./BatchItemRow";

const renderRow = (item: QueuedImage) => renderToStaticMarkup(React.createElement(ItemRow, {
  item,
  now: 0,
  onRetryItem: () => {},
  onRemoveItem: () => {},
  removeActionKey: `remove:${item.id}`,
  retryActionKey: `retry:${item.id}`,
  isActionPending: () => false,
  runAction: () => {},
}));

const makeItem = (
  id: string,
  status: QueuedImage["status"],
  extras: Partial<QueuedImage> = {},
): QueuedImage => ({
  id,
  file: new File([], `${id}.png`),
  addedAt: new Date(0),
  status,
  ...extras,
});

const queued = renderRow(makeItem("queued", "queued"));
assert.match(queued, /aria-label="Skip queued\.png"/);
assert.match(queued, /Waiting to start/);
assert.match(queued, /job-card job-offscreen-row/);

const awaitingTranslation = renderRow(makeItem("awaiting", "processing", { step: "awaiting_translation" }));
assert.match(awaitingTranslation, /Prepared · Waiting for batch translation/);
assert.match(awaitingTranslation, /aria-label="Skip awaiting\.png"/);

const failed = renderRow(makeItem("failed", "error", { error: "Provider unavailable" }));
assert.match(failed, /Provider unavailable/);
assert.match(failed, />Retry</);
assert.match(failed, />Pass &amp; edit</);
assert.match(failed, /aria-label="Remove failed page failed\.png"/);

console.log("batch item row contracts passed");
