import assert from 'node:assert/strict';
import type { TranslationBatch, QueuedImage } from '@/types';
import { renameBatchQueuedOnly, countWaitingPages } from './batchRename';

console.log('Running batchRename unit tests...');

const dummyFile = (name: string) => ({ name, size: 100, type: 'image/png' } as unknown as File);

const createItem = (id: string, name: string, status: QueuedImage['status'], mangaTitle: string): QueuedImage => ({
  id,
  file: dummyFile(name),
  addedAt: new Date('2026-01-01T00:00:00Z'),
  status,
  mangaTitle,
});

const batch: TranslationBatch = {
  id: 'batch-test-1',
  addedAt: new Date('2026-01-01T00:00:00Z'),
  mangaTitle: 'Original Manga Title',
  settings: {
    translator: 'deepseek',
  } as any,
  totalItems: 4,
  completedCount: 1,
  status: 'processing',
  items: [
    createItem('item-1', 'page1.png', 'processing', 'Original Manga Title'),
    createItem('item-2', 'page2.png', 'queued', 'Original Manga Title'),
    createItem('item-3', 'page3.png', 'queued', 'Original Manga Title'),
    createItem('item-4', 'page4.png', 'error', 'Original Manga Title'),
  ],
};

// 1. countWaitingPages
assert.equal(countWaitingPages(batch), 2);

// 2. renameBatchQueuedOnly
const renamed = renameBatchQueuedOnly(batch, 'New Destination Manga');

// Batch level title should be updated
assert.equal(renamed.mangaTitle, 'New Destination Manga');

// Processing item (item-1) should keep its original title
assert.equal(renamed.items[0].id, 'item-1');
assert.equal(renamed.items[0].status, 'processing');
assert.equal(renamed.items[0].mangaTitle, 'Original Manga Title');

// Queued items (item-2, item-3) should have the new title
assert.equal(renamed.items[1].id, 'item-2');
assert.equal(renamed.items[1].status, 'queued');
assert.equal(renamed.items[1].mangaTitle, 'New Destination Manga');

assert.equal(renamed.items[2].id, 'item-3');
assert.equal(renamed.items[2].status, 'queued');
assert.equal(renamed.items[2].mangaTitle, 'New Destination Manga');

// Failed item (item-4) should retain original destination
assert.equal(renamed.items[3].id, 'item-4');
assert.equal(renamed.items[3].status, 'error');
assert.equal(renamed.items[3].mangaTitle, 'Original Manga Title');

// 3. Renaming with spaces trims whitespace
const trimmed = renameBatchQueuedOnly(batch, '   Spaced Manga   ');
assert.equal(trimmed.mangaTitle, 'Spaced Manga');
assert.equal(trimmed.items[1].mangaTitle, 'Spaced Manga');

// 4. Renaming to empty string defaults to Ungrouped
const ungrouped = renameBatchQueuedOnly(batch, '   ');
assert.equal(ungrouped.mangaTitle, 'Ungrouped');
assert.equal(ungrouped.items[1].mangaTitle, 'Ungrouped');

console.log('All batchRename tests passed successfully!');
