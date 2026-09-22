import type { TranslationBatch } from '@/types';

/**
 * Renames a translation batch such that only pages currently waiting in the queue
 * receive the new manga destination title. Active, completed, or failed items retain
 * their original destination title.
 */
export function renameBatchQueuedOnly(batch: TranslationBatch, newTitle: string): TranslationBatch {
  const cleanTitle = newTitle.trim() || 'Ungrouped';
  return {
    ...batch,
    mangaTitle: cleanTitle,
    items: batch.items.map((item) =>
      item.status === 'queued'
        ? { ...item, mangaTitle: cleanTitle }
        : item
    ),
  };
}

/**
 * Returns the number of pages currently waiting (queued) in a batch.
 */
export function countWaitingPages(batch: TranslationBatch): number {
  return batch.items.filter((item) => item.status === 'queued').length;
}
