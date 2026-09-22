import assert from 'node:assert/strict';
import { finishedSearchItems, formatSimilarity, isActiveSearchJob, type SearchJob, type SearchManga, type SearchStatusFilter } from './searchLab';
import { parseAppPath, validatePriorRoute } from './routeState';

assert.equal(formatSimilarity(null), 'Not indexed');
assert.equal(formatSimilarity(0), '0.0000');
assert.equal(formatSimilarity(0.912345), '0.9123');
const job = { status: 'running', completed: 3, unchanged: 2, skipped: 1, failed: 1 } as SearchJob;
assert.equal(finishedSearchItems(job), 7);
assert.equal(isActiveSearchJob(job), true);
assert.equal(isActiveSearchJob({ ...job, status: 'interrupted' }), false);
assert.equal(parseAppPath('/search-lab').view, 'search');
assert.equal(validatePriorRoute('/search-lab'), '/search-lab');

const sampleManga: SearchManga = {
  id: 'group1',
  title: 'Sample Manga',
  pageCount: 10,
  originalCount: 10,
  indexedPages: 10,
  outdatedPages: 0,
  summaryAvailable: true,
  summaryStale: false,
  summaryIndexed: true,
  summaryOutdated: false,
  outdated: false,
  partial: false,
  coverUrl: '/result/page1/thumbnail.webp',
};
assert.equal(sampleManga.coverUrl, '/result/page1/thumbnail.webp');

const filters: SearchStatusFilter[] = ['all', 'summarized', 'not-summarized'];
assert.equal(filters.length, 3);
assert.equal(filters.includes('summarized'), true);

console.log('Search Lab checks passed');
