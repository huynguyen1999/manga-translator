import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { QueuedImage } from "@/types";
import { VirtualizedJobItems } from "./VirtualizedJobItems";

const renderList = (items: QueuedImage[]) => renderToStaticMarkup(
  React.createElement(VirtualizedJobItems, {
    items,
    renderItem: (item) => React.createElement("button", { type: "button" }, item.id),
  }),
);
const items = (count: number) => Array.from({ length: count }, (_, index) => ({ id: `page-${index}` }) as QueuedImage);

const regularList = renderList(items(2));
assert.match(regularList, /role="list"/);
assert.equal((regularList.match(/role="listitem"/g) || []).length, 2);

const largeList = renderList(items(20));
assert.match(largeList, /aria-label="Batch pages"/);
assert.match(largeList, /aria-setsize="20"/);
assert.match(largeList, /aria-posinset="1"/);
assert.match(largeList, /page-0/);
assert.doesNotMatch(largeList, /page-19/);

console.log("virtualized batch page list contracts passed");
