import assert from 'node:assert/strict';
import { finishedSearchItems, formatRankDelta, formatSimilarity, isActiveSearchJob, type SearchJob, type SearchManga, type SearchStatusFilter } from './searchLab';
import { parseAppPath, validatePriorRoute } from './routeState';

assert.equal(formatSimilarity(null), 'Not indexed');
assert.equal(formatSimilarity(0), '0.0000');
assert.equal(formatSimilarity(0.912345), '0.9123');
assert.equal(formatRankDelta(3, 4), '↑ +3 (from #4)');
assert.equal(formatRankDelta(-2, 1), '↓ -2 (from #1)');
assert.equal(formatRankDelta(0, 2), '= #2');
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
  summaryAvailable: true,
  summaryStale: false,
  summaryIndexed: true,
  summaryOutdated: false,
  outdated: false,
  partial: false,
  coverUrl: '/result/page1/thumbnail.webp',
};
assert.equal(sampleManga.coverUrl, '/result/page1/thumbnail.webp');

const filters: SearchStatusFilter[] = ['all', 'indexed', 'not-indexed', 'summarized', 'not-summarized'];
assert.equal(filters.length, 5);
assert.equal(filters.includes('summarized'), true);

console.log('Search Lab checks passed');
