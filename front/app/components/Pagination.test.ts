import assert from 'node:assert/strict';
import { generatePaginationItems, getItemRange } from './Pagination';

// Test 1: Empty and single page cases
assert.deepEqual(generatePaginationItems(1, 0), [], '0 total pages should return empty array');
assert.deepEqual(generatePaginationItems(1, 1), [1], '1 total page should return [1]');

// Test 2: Small page count (no ellipses needed)
assert.deepEqual(
  generatePaginationItems(1, 5),
  [1, 2, 3, 4, 5],
  '5 pages should display all numbers'
);
assert.deepEqual(
  generatePaginationItems(4, 7),
  [1, 2, 3, 4, 5, 6, 7],
  '7 pages should display all numbers without ellipses'
);

// Test 3: Beginning of large range (right ellipsis only)
assert.deepEqual(
  generatePaginationItems(1, 10),
  [1, 2, 3, 4, 5, 'ellipsis-right', 10],
  'Page 1 of 10 should show [1, 2, 3, 4, 5, ..., 10]'
);
assert.deepEqual(
  generatePaginationItems(2, 10),
  [1, 2, 3, 4, 5, 'ellipsis-right', 10],
  'Page 2 of 10 should show [1, 2, 3, 4, 5, ..., 10]'
);

// Test 4: Middle of large range (both left and right ellipses)
assert.deepEqual(
  generatePaginationItems(5, 10),
  [1, 'ellipsis-left', 4, 5, 6, 'ellipsis-right', 10],
  'Page 5 of 10 should show [1, ..., 4, 5, 6, ..., 10]'
);
assert.deepEqual(
  generatePaginationItems(6, 10),
  [1, 'ellipsis-left', 5, 6, 7, 'ellipsis-right', 10],
  'Page 6 of 10 should show [1, ..., 5, 6, 7, ..., 10]'
);

// Test 5: End of large range (left ellipsis only)
assert.deepEqual(
  generatePaginationItems(9, 10),
  [1, 'ellipsis-left', 6, 7, 8, 9, 10],
  'Page 9 of 10 should show [1, ..., 6, 7, 8, 9, 10]'
);
assert.deepEqual(
  generatePaginationItems(10, 10),
  [1, 'ellipsis-left', 6, 7, 8, 9, 10],
  'Page 10 of 10 should show [1, ..., 6, 7, 8, 9, 10]'
);

// Test 6: Custom sibling count (siblingCount = 2)
assert.deepEqual(
  generatePaginationItems(10, 20, 2),
  [1, 'ellipsis-left', 8, 9, 10, 11, 12, 'ellipsis-right', 20],
  'Page 10 of 20 with siblingCount=2 should show 5 numbers around current'
);

// Test 7: Item range calculation
assert.deepEqual(
  getItemRange(1, 24, 0),
  { start: 0, end: 0, total: 0 },
  '0 items should return 0 range'
);
assert.deepEqual(
  getItemRange(1, 24, 160),
  { start: 1, end: 24, total: 160 },
  'First page of 160 with pageSize 24 should be 1-24'
);
assert.deepEqual(
  getItemRange(2, 24, 160),
  { start: 25, end: 48, total: 160 },
  'Second page of 160 with pageSize 24 should be 25-48'
);
assert.deepEqual(
  getItemRange(7, 24, 160),
  { start: 145, end: 160, total: 160 },
  'Last page of 160 with pageSize 24 should be 145-160'
);
assert.deepEqual(
  getItemRange(0, 24, 160),
  { start: 1, end: 24, total: 160 },
  'Page 0 should be clamped to 1'
);

console.log('Pagination tests passed successfully!');
