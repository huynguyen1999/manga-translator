import React, { useEffect, useState } from "react";
import type { QueuedImage } from "@/types";

export const VirtualizedJobItems: React.FC<{
  items: QueuedImage[];
  renderItem: (item: QueuedImage) => React.ReactNode;
}> = React.memo(({ items, renderItem }) => {
  const listRef = React.useRef<HTMLDivElement>(null);
  const rowElements = React.useRef(new Map<string, HTMLElement>());
  const pendingFocus = React.useRef<{ index: number; backwards: boolean } | null>(null);
  const heights = React.useRef(new Map<string, number>());
  const observer = React.useRef<ResizeObserver | null>(null);
  const callbacks = React.useRef(new Map<string, (element: HTMLDivElement | null) => void>());
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(480);
  const [layoutVersion, setLayoutVersion] = useState(0);
  const virtualized = items.length > 16;
  const estimatedHeight = 136;
  const overscan = 240;

  useEffect(() => {
    if (!virtualized || !listRef.current) return;
    const element = listRef.current;
    setViewportHeight(element.clientHeight);
    const resizeObserver = new ResizeObserver((entries) => {
      let changed = false;
      for (const entry of entries) {
        if (entry.target === element) {
          setViewportHeight(element.clientHeight);
          continue;
        }
        const id = (entry.target as HTMLElement).dataset.itemId;
        if (!id) continue;
        const height = entry.contentRect.height;
        if (Math.abs((heights.current.get(id) ?? 0) - height) > 1) {
          heights.current.set(id, height);
          changed = true;
        }
      }
      if (changed) setLayoutVersion((version) => version + 1);
    });
    observer.current = resizeObserver;
    resizeObserver.observe(element);
    rowElements.current.forEach((row) => resizeObserver.observe(row));
    return () => {
      observer.current = null;
      resizeObserver.disconnect();
    };
  }, [virtualized]);

  const setRowRef = (id: string) => {
    let callback = callbacks.current.get(id);
    if (!callback) {
      callback = (element) => {
        const previous = rowElements.current.get(id);
        if (previous) observer.current?.unobserve(previous);
        if (element) {
          rowElements.current.set(id, element);
          observer.current?.observe(element);
        } else {
          rowElements.current.delete(id);
        }
      };
      callbacks.current.set(id, callback);
    }
    return callback;
  };

  const { offsets, totalHeight } = React.useMemo(() => {
    let totalHeight = 0;
    const offsets = items.map((item) => {
      const offset = totalHeight;
      totalHeight += (heights.current.get(item.id) ?? estimatedHeight) + 8;
      return offset;
    });
    return { offsets, totalHeight };
  }, [items, layoutVersion]);

  const viewportStart = Math.max(0, scrollTop - overscan);
  const viewportEnd = scrollTop + viewportHeight + overscan;
  let first = offsets.findIndex((offset, index) => offset + (heights.current.get(items[index].id) ?? estimatedHeight) >= viewportStart);
  if (first < 0) first = 0;
  let last = first;
  while (last < items.length && offsets[last] < viewportEnd) last += 1;

  useEffect(() => {
    const pending = pendingFocus.current;
    if (!pending) return;
    const row = rowElements.current.get(items[pending.index]?.id);
    if (!row) return;
    const focusable = row.querySelectorAll<HTMLElement>("button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex='-1'])");
    focusable[pending.backwards ? focusable.length - 1 : 0]?.focus();
    pendingFocus.current = null;
  }, [first, last, items]);

  const handleVirtualizedTab = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Tab") return;
    const row = (event.target as HTMLElement).closest<HTMLElement>("[data-virtual-index]");
    if (!row) return;
    const index = Number(row.dataset.virtualIndex);
    const focusable = row.querySelectorAll<HTMLElement>("button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex='-1'])");
    const active = document.activeElement;
    const isBoundary = event.shiftKey ? active === focusable[0] : active === focusable[focusable.length - 1];
    const nextIndex = event.shiftKey ? index - 1 : index + 1;
    if (!isBoundary || nextIndex < 0 || nextIndex >= items.length) return;
    const movesPastWindow = event.shiftKey ? index === first : index === last - 1;
    if (!movesPastWindow) return;
    event.preventDefault();
    pendingFocus.current = { index: nextIndex, backwards: event.shiftKey };
    listRef.current?.scrollTo({ top: offsets[nextIndex] });
  };

  if (!virtualized) {
    return (
      <div className="space-y-2 border-t border-zinc-100 p-4 dark:border-zinc-800" role="list">
        {items.map((item) => <div key={item.id} role="listitem">{renderItem(item)}</div>)}
      </div>
    );
  }

  return (
    <div
      ref={listRef}
      className="max-h-[60vh] overflow-y-auto border-t border-zinc-100 dark:border-zinc-800"
      role="list"
      aria-label="Batch pages"
      onKeyDown={handleVirtualizedTab}
      onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
    >
      <div className="relative px-4" style={{ height: totalHeight + 24 }}>
        {items.slice(first, last).map((item, relativeIndex) => {
          const index = first + relativeIndex;
          return (
            <div
              key={item.id}
              ref={setRowRef(item.id)}
              data-item-id={item.id}
              data-virtual-index={index}
              role="listitem"
              aria-posinset={index + 1}
              aria-setsize={items.length}
              className="absolute left-4 right-4"
              style={{ top: offsets[index] + 12 }}
            >
              {renderItem(item)}
            </div>
          );
        })}
      </div>
    </div>
  );
});
