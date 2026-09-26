import React, { useEffect, useRef, useState } from "react";
import { Icon } from "@iconify/react";
import type { PendingStudioFile } from "@/types";
import PreviewImage from "./PreviewImage";

interface PendingFilesOrderPanelProps {
  pendingFiles: PendingStudioFile[];
  onConfirm: () => void;
  onRemove: (fileId: string) => void;
  onReorder: (sourceId: string, targetId: string) => void;
  onClear: () => void;
}

const PendingFilesOrderPanel: React.FC<PendingFilesOrderPanelProps> = ({
  pendingFiles,
  onConfirm,
  onRemove,
  onReorder,
  onClear,
}) => {
  const [draggedPendingFileId, setDraggedPendingFileId] = useState<string | null>(null);
  const [pendingDragOverId, setPendingDragOverId] = useState<string | null>(null);
  const pendingFileListRef = useRef<HTMLDivElement>(null);
  const pendingAutoScrollFrameRef = useRef<number | null>(null);
  const pendingAutoScrollDirectionRef = useRef<-1 | 0 | 1>(0);

  const stopPendingAutoScroll = () => {
    pendingAutoScrollDirectionRef.current = 0;
    if (pendingAutoScrollFrameRef.current !== null) {
      cancelAnimationFrame(pendingAutoScrollFrameRef.current);
      pendingAutoScrollFrameRef.current = null;
    }
  };

  const runPendingAutoScroll = () => {
    const list = pendingFileListRef.current;
    const direction = pendingAutoScrollDirectionRef.current;
    if (!list || direction === 0) {
      pendingAutoScrollFrameRef.current = null;
      return;
    }
    list.scrollTop += direction * 12;
    pendingAutoScrollFrameRef.current = requestAnimationFrame(runPendingAutoScroll);
  };

  const updatePendingAutoScroll = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    const list = pendingFileListRef.current;
    if (!list) return;

    const bounds = list.getBoundingClientRect();
    const edgeSize = Math.min(120, bounds.height / 3);
    pendingAutoScrollDirectionRef.current =
      event.clientY <= bounds.top + edgeSize ? -1 : event.clientY >= bounds.bottom - edgeSize ? 1 : 0;

    if (pendingAutoScrollDirectionRef.current === 0) {
      stopPendingAutoScroll();
    } else if (pendingAutoScrollFrameRef.current === null) {
      pendingAutoScrollFrameRef.current = requestAnimationFrame(runPendingAutoScroll);
    }
  };

  useEffect(() => stopPendingAutoScroll, []);

  return pendingFiles.length > 0 && (
        <section
          aria-labelledby="pending-file-order-title"
          className="space-y-3 rounded-xl border border-indigo-200 bg-indigo-50/50 p-3 dark:border-indigo-900/70 dark:bg-indigo-950/20 sm:p-4"
        >
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 id="pending-file-order-title" className="flex items-center gap-2 text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                <Icon icon="carbon:sort-ascending" className="h-4 w-4 text-indigo-600 dark:text-indigo-400" />
                Arrange original files before loading
              </h2>
              <p className="mt-1 text-xs text-indigo-900/80 dark:text-indigo-200/80">
                Drag the original files into manga order. Pages inside each archive keep their internal order.
              </p>
            </div>
            <span className="rounded-full bg-white px-2.5 py-1 text-xs font-semibold text-indigo-700 shadow-2xs dark:bg-zinc-900 dark:text-indigo-300">
              {pendingFiles.length} {pendingFiles.length === 1 ? "file" : "files"} waiting
            </span>
          </div>

          <div
            ref={pendingFileListRef}
            onDragOver={updatePendingAutoScroll}
            onDragLeave={(event) => {
              const relatedTarget = event.relatedTarget as Node | null;
              if (!relatedTarget || !event.currentTarget.contains(relatedTarget)) stopPendingAutoScroll();
            }}
            className="max-h-[min(56rem,80vh)] space-y-2 overflow-y-auto overscroll-contain pr-1"
          >
            {pendingFiles.map((source, index) => (
              <div
                key={source.id}
                draggable
                onDragStart={(event) => {
                  setDraggedPendingFileId(source.id);
                  event.dataTransfer.effectAllowed = "move";
                  event.dataTransfer.setData("text/plain", source.id);
                }}
                onDragOver={(event) => {
                  event.preventDefault();
                  event.dataTransfer.dropEffect = "move";
                  setPendingDragOverId(source.id);
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  stopPendingAutoScroll();
                  onReorder(event.dataTransfer.getData("text/plain") || draggedPendingFileId || "", source.id);
                }}
                onDragEnd={() => {
                  stopPendingAutoScroll();
                  setDraggedPendingFileId(null);
                  setPendingDragOverId(null);
                }}
                className={`flex items-center gap-2 rounded-lg border bg-white p-2 transition-all dark:bg-zinc-900 sm:gap-3 sm:p-2.5 ${
                  pendingDragOverId === source.id
                    ? "border-indigo-500 ring-2 ring-indigo-200 dark:ring-indigo-900/60"
                    : "border-zinc-200/80 dark:border-zinc-800"
                } ${draggedPendingFileId === source.id ? "opacity-40" : ""}`}
              >
                <span className="cursor-grab select-none text-base leading-none text-zinc-400 active:cursor-grabbing" title="Drag to reorder" aria-hidden="true">
                  ⋮⋮
                </span>
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-zinc-100 text-xs font-bold text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
                  {index + 1}
                </span>
                <div className="h-16 w-12 shrink-0 overflow-hidden rounded-md border border-zinc-200 bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-950">
                  <PreviewImage file={source.pages[0]?.file ?? null} result={null} showComparisonControls={false} className="h-full w-full" />
                </div>
                <div className="min-w-0 flex-1">
                  <span className="block whitespace-normal break-words text-xs font-medium leading-snug text-zinc-800 dark:text-zinc-200" title={source.file.name}>
                    {source.file.name}
                  </span>
                  {source.pages.length > 1 && (
                    <span className="mt-0.5 block text-xs text-zinc-500 dark:text-zinc-400">
                      {source.pages.length} pages inside
                    </span>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-0.5">
                  <button
                    type="button"
                    onClick={() => {
                      if (index > 0) onReorder(source.id, pendingFiles[index - 1].id);
                    }}
                    disabled={index === 0}
                    className="flex min-h-8 min-w-8 items-center justify-center rounded-lg p-1.5 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800 disabled:cursor-not-allowed disabled:opacity-30 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
                    aria-label={`Move ${source.file.name} up`}
                    title="Move up"
                  >
                    <Icon icon="carbon:chevron-up" className="h-4 w-4" />
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (index < pendingFiles.length - 1) onReorder(source.id, pendingFiles[index + 1].id);
                    }}
                    disabled={index === pendingFiles.length - 1}
                    className="flex min-h-8 min-w-8 items-center justify-center rounded-lg p-1.5 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800 disabled:cursor-not-allowed disabled:opacity-30 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
                    aria-label={`Move ${source.file.name} down`}
                    title="Move down"
                  >
                    <Icon icon="carbon:chevron-down" className="h-4 w-4" />
                  </button>
                  <button
                    type="button"
                    onClick={() => onRemove(source.id)}
                    className="flex min-h-8 min-w-8 items-center justify-center rounded-lg p-1.5 text-rose-500/70 hover:bg-rose-50 hover:text-rose-600 dark:text-rose-400/70 dark:hover:bg-rose-950/40 dark:hover:text-rose-400"
                    aria-label={`Remove ${source.file.name} from pending files`}
                    title="Remove file"
                  >
                    <Icon icon="carbon:close" className="h-4 w-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>

          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-indigo-200/70 pt-3 dark:border-indigo-900/60">
            <button
              type="button"
              onClick={onClear}
              className="rounded-lg px-3 py-1.5 text-xs font-medium text-zinc-600 hover:bg-white hover:text-rose-600 dark:text-zinc-400 dark:hover:bg-zinc-900 dark:hover:text-rose-400"
            >
              Discard waiting files
            </button>
            <button
              type="button"
              onClick={onConfirm}
              className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-1.5 text-xs font-semibold text-white shadow-xs transition-colors hover:bg-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
            >
              <Icon icon="carbon:checkmark" className="h-4 w-4" />
              Load files in this order
            </button>
          </div>
        </section>
      );
};

export default PendingFilesOrderPanel;
