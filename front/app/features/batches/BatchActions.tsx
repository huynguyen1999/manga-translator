import React from "react";
import { Icon } from "@iconify/react";
import type { QueuedImage, TranslationBatch } from "@/types";

type AsyncAction = () => void | Promise<void>;

export const BatchActions: React.FC<{
  batch: TranslationBatch;
  failedItems: QueuedImage[];
  failed: number;
  hasDetails: boolean;
  isUploading: boolean;
  retryAllActionKey: string;
  stopActionKey: string;
  isActionPending: (key: string) => boolean;
  runAction: (key: string, label: string, action: AsyncAction) => void;
  onRetryItem: (batchId: string, itemId: string, keepFailedPagesForEditing?: boolean) => void | Promise<void>;
  onRemove: (batchId: string) => void | Promise<void>;
  onPriorityChange: (batchId: string, priority: boolean) => void | Promise<void>;
}> = ({
  batch,
  failedItems,
  failed,
  hasDetails,
  isUploading,
  retryAllActionKey,
  stopActionKey,
  isActionPending,
  runAction,
  onRetryItem,
  onRemove,
  onPriorityChange,
}) => (
  <>
          {failed > 0 && hasDetails && (
            <button
              type="button"
              onClick={() => runAction(retryAllActionKey, "Retrying failed pages…", async () => {
                for (const item of failedItems) {
                  await onRetryItem(batch.id, item.id);
                }
              })}
              disabled={isActionPending(retryAllActionKey)}
              aria-busy={isActionPending(retryAllActionKey)}
              className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs font-semibold text-indigo-700 hover:bg-indigo-100 disabled:cursor-wait disabled:opacity-70 dark:border-indigo-900/60 dark:bg-indigo-950/40 dark:text-indigo-300 dark:hover:bg-indigo-950/70 transition-colors"
              title={`Retry all ${failed} failed ${failed === 1 ? "page" : "pages"}`}
              aria-label={`Retry all failed pages in ${batch.mangaTitle}`}
            >
              <Icon icon="carbon:renew" className={`h-3.5 w-3.5 ${isActionPending(retryAllActionKey) ? "animate-spin" : ""}`} />
              {isActionPending(retryAllActionKey) ? "Retrying…" : "Retry failed"}
            </button>
          )}
          {(batch.status === "processing" || batch.status === "paused" || batch.status === "stopping") && (
            <button
              type="button"
              onClick={() => runAction(stopActionKey, "Stopping…", () => onRemove(batch.id))}
              disabled={batch.status === "stopping" || isActionPending(stopActionKey)}
              aria-busy={isActionPending(stopActionKey)}
              className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-700 hover:bg-rose-100 disabled:cursor-wait disabled:opacity-70 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-300 dark:hover:bg-rose-950/70 transition-colors"
              title="Stop and remove batch from process"
            >
              {(batch.status === "stopping" || isActionPending(stopActionKey)) ? <Icon icon="carbon:renew" className="h-3.5 w-3.5 animate-spin" /> : <Icon icon="carbon:stop-filled-alt" className="h-3.5 w-3.5" />}
              <span>{batch.status === "stopping" || isActionPending(stopActionKey) ? "Stopping…" : "Stop"}</span>
            </button>
          )}
          {isUploading && (
            <button
              type="button"
              onClick={() => runAction(`cancel:${batch.id}`, "Cancelling…", () => onRemove(batch.id))}
              disabled={isActionPending(`cancel:${batch.id}`)}
              aria-busy={isActionPending(`cancel:${batch.id}`)}
              className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-700 hover:bg-rose-100 disabled:cursor-wait disabled:opacity-70 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-300 dark:hover:bg-rose-950/70 transition-colors"
              title="Cancel and remove upload"
            >
              {isActionPending(`cancel:${batch.id}`) ? <Icon icon="carbon:renew" className="h-3.5 w-3.5 animate-spin" /> : <Icon icon="carbon:close" className="h-3.5 w-3.5" />}
              <span>{isActionPending(`cancel:${batch.id}`) ? "Cancelling…" : "Cancel upload"}</span>
            </button>
          )}
          {batch.status === "waiting" && (
            <button
              type="button"
              onClick={() => runAction(`priority:${batch.id}`, batch.priority ? "Removing priority…" : "Prioritizing…", () => onPriorityChange(batch.id, !batch.priority))}
              disabled={isActionPending(`priority:${batch.id}`)}
              aria-busy={isActionPending(`priority:${batch.id}`)}
              className={`inline-flex min-h-11 items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-semibold transition-colors disabled:cursor-wait disabled:opacity-70 ${batch.priority
                ? "border-indigo-200 bg-indigo-50 text-indigo-700 hover:bg-indigo-100 dark:border-indigo-900/60 dark:bg-indigo-950/40 dark:text-indigo-300 dark:hover:bg-indigo-950/70"
                : "border-zinc-200 text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
              }`}
              title={batch.priority ? "Remove priority from batch" : "Move batch to the front of the queue"}
              aria-label={batch.priority ? `Remove priority from ${batch.mangaTitle}` : `Prioritize ${batch.mangaTitle}`}
            >
              <Icon icon={isActionPending(`priority:${batch.id}`) ? "carbon:renew" : batch.priority ? "carbon:star-filled" : "carbon:star"} className={`h-3.5 w-3.5 ${isActionPending(`priority:${batch.id}`) ? "animate-spin" : ""}`} />
              {isActionPending(`priority:${batch.id}`) ? (batch.priority ? "Removing…" : "Prioritizing…") : (batch.priority ? "Prioritized" : "Prioritize")}
            </button>
          )}
          {(batch.status === "waiting" || batch.status === "error") && (
            <button
              type="button"
              onClick={() => runAction(`remove:${batch.id}`, "Removing…", () => onRemove(batch.id))}
              disabled={isActionPending(`remove:${batch.id}`)}
              aria-busy={isActionPending(`remove:${batch.id}`)}
              className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg p-2 text-zinc-400 hover:bg-zinc-100 hover:text-rose-600 disabled:cursor-wait disabled:opacity-70 dark:hover:bg-zinc-800 transition-colors"
              title={batch.status === "error" ? "Remove failed batch" : "Remove waiting batch"}
              aria-label={batch.status === "error" ? `Remove failed batch ${batch.mangaTitle}` : `Remove waiting batch ${batch.mangaTitle}`}
            >
              <Icon icon={isActionPending(`remove:${batch.id}`) ? "carbon:renew" : "carbon:trash-can"} className={`h-4 w-4 ${isActionPending(`remove:${batch.id}`) ? "animate-spin" : ""}`} />
            </button>
          )}
  </>
);
