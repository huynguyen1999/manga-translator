import assert from 'node:assert/strict';
import {
  normalizeExistingGroups,
  filterExistingGroups,
  type ExistingGroupEntry,
  type ExistingGroupItem,
} from './GroupSelectionModal';

// Test 1: normalizeExistingGroups handles string array and deduplicates (case-insensitive)
{
  const raw: ExistingGroupEntry[] = [
    'One Piece',
    'one piece',
    '  Bleach  ',
    'Ungrouped',
    'ungrouped',
    '',
    '   ',
    'Naruto',
  ];
  const normalized = normalizeExistingGroups(raw);
  assert.deepEqual(
    normalized,
    [
      { title: 'One Piece' },
      { title: 'Bleach' },
      { title: 'Naruto' },
    ],
    'Should normalize string items, trim whitespace, deduplicate case-insensitively, and exclude Ungrouped/empty'
  );
}

// Test 2: normalizeExistingGroups handles ExistingGroupItem objects
{
  const raw: ExistingGroupEntry[] = [
    { id: 'g1', title: 'Oricon Comics', count: 12 },
    { id: 'g2', title: 'oricon comics', count: 5 },
    { id: 'g3', title: 'Ungrouped', count: 100 },
    { id: 'g4', title: 'Solo Leveling', count: 24 },
  ];
  const normalized = normalizeExistingGroups(raw);
  assert.deepEqual(
    normalized,
    [
      { id: 'g1', title: 'Oricon Comics', count: 12 },
      { id: 'g4', title: 'Solo Leveling', count: 24 },
    ],
    'Should handle object entries, preserving id and count, while deduplicating'
  );
}

// Test 3: normalizeExistingGroups handles mixed strings and objects
{
  const raw: ExistingGroupEntry[] = [
    'Chainsaw Man',
    { id: 'c1', title: 'chainsaw man', count: 10 },
    { id: 'c2', title: 'Demon Slayer', count: 15 },
  ];
  const normalized = normalizeExistingGroups(raw);
  assert.deepEqual(
    normalized,
    [
      { title: 'Chainsaw Man' },
      { id: 'c2', title: 'Demon Slayer', count: 15 },
    ],
    'Should deduplicate across mixed string and object types'
  );
}

// Test 4: filterExistingGroups returns all groups when query is empty or whitespace
{
  const groups: ExistingGroupItem[] = [
    { title: 'Oricon Comics' },
    { title: 'One Piece' },
    { title: 'Naruto' },
  ];
  assert.deepEqual(filterExistingGroups(groups, ''), groups, 'Empty query should return all groups');
  assert.deepEqual(filterExistingGroups(groups, '   '), groups, 'Whitespace query should return all groups');
}

// Test 5: filterExistingGroups matches substring case-insensitively (e.g. "orico" -> "Oricon Comics")
{
  const groups: ExistingGroupItem[] = [
    { title: 'Oricon Comics' },
    { title: 'One Piece' },
    { title: 'Moriarty the Patriot' },
    { title: 'Naruto' },
  ];
  const filtered = filterExistingGroups(groups, 'orico');
  assert.deepEqual(
    filtered,
    [
      { title: 'Oricon Comics' },
    ],
    'Query "orico" should match "Oricon Comics"'
  );

  const filteredOr = filterExistingGroups(groups, 'ori');
  assert.deepEqual(
    filteredOr,
    [
      { title: 'Oricon Comics' },
      { title: 'Moriarty the Patriot' },
    ],
    'Query "ori" should match "Oricon Comics" and "Moriarty the Patriot"'
  );
}

// Test 6: filterExistingGroups returns empty array when nothing matches
{
  const groups: ExistingGroupItem[] = [
    { title: 'One Piece' },
    { title: 'Bleach' },
  ];
  const filtered = filterExistingGroups(groups, 'nonexistent');
  assert.deepEqual(filtered, [], 'Non-matching query should return empty array');
}

console.log('GroupSelectionModal unit tests passed successfully!');
