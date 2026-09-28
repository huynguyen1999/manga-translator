import React from "react";
import { Icon } from "@iconify/react";
import type { QueuedImage, TranslationBatch } from "@/types";

type AsyncAction = () => void | Promise<void>;

export const BatchActions: React.FC<{
  batch: TranslationBatch;
  failedItems: QueuedImage[];
  failed: number;
  hasDetails: boolean;
  retryAllActionKey: string;
  isActionPending: (key: string) => boolean;
  runAction: (key: string, label: string, action: AsyncAction) => void;
  onRetryItem: (batchId: string, itemId: string, keepFailedPagesForEditing?: boolean) => void | Promise<void>;
  onPriorityChange: (batchId: string, priority: boolean) => void | Promise<void>;
}> = ({
  batch,
  failedItems,
  failed,
  hasDetails,
  retryAllActionKey,
  isActionPending,
  runAction,
  onRetryItem,
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
              className="inline-flex min-h-9 items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium text-indigo-700 hover:bg-indigo-100 disabled:cursor-wait disabled:opacity-70 dark:text-indigo-300 dark:hover:bg-indigo-950/70"
              title={`Retry all ${failed} failed ${failed === 1 ? "page" : "pages"}`}
              aria-label={`Retry all failed pages in ${batch.mangaTitle}`}
            >
              <Icon icon="carbon:renew" className={`h-3.5 w-3.5 ${isActionPending(retryAllActionKey) ? "animate-spin" : ""}`} />
              {isActionPending(retryAllActionKey) ? "Retrying…" : "Retry failed"}
            </button>
          )}
          {batch.status === "waiting" && (
            <button
              type="button"
              onClick={() => runAction(`priority:${batch.id}`, batch.priority ? "Removing priority…" : "Prioritizing…", () => onPriorityChange(batch.id, !batch.priority))}
              disabled={isActionPending(`priority:${batch.id}`)}
              aria-busy={isActionPending(`priority:${batch.id}`)}
              className={`inline-flex min-h-9 items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium disabled:cursor-wait disabled:opacity-70 ${batch.priority
                ? "text-indigo-700 hover:bg-indigo-100 dark:text-indigo-300 dark:hover:bg-indigo-950/70"
                : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-700"
              }`}
              title={batch.priority ? "Remove priority from batch" : "Move batch to the front of the queue"}
              aria-label={batch.priority ? `Remove priority from ${batch.mangaTitle}` : `Prioritize ${batch.mangaTitle}`}
            >
              <Icon icon={isActionPending(`priority:${batch.id}`) ? "carbon:renew" : batch.priority ? "carbon:star-filled" : "carbon:star"} className={`h-3.5 w-3.5 ${isActionPending(`priority:${batch.id}`) ? "animate-spin" : ""}`} />
              {isActionPending(`priority:${batch.id}`) ? (batch.priority ? "Removing…" : "Prioritizing…") : (batch.priority ? "Prioritized" : "Prioritize")}
            </button>
          )}
  </>
);
