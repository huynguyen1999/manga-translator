import type { SeriesMember } from '@/types';

export function getReaderPreloadIndices(
  currentPage: number,
  totalPages: number,
  _isPhone: boolean,
): number[] {
  const currentIndex = currentPage - 1;
  const indices: number[] = [];
  const ahead = 5;

  if (currentIndex >= 0 && currentIndex < totalPages) indices.push(currentIndex);
  for (let offset = 1; offset <= ahead; offset++) {
    const idx = currentIndex + offset;
    if (idx < totalPages) indices.push(idx);
  }

  for (let offset = 1; offset <= ahead; offset++) {
    const idx = currentIndex - offset;
    if (idx >= 0) indices.push(idx);
  }

  return indices;
}

export function getReaderPriorityIndices(currentPage: number, totalPages: number): number[] {
  const currentIndex = Math.max(0, Math.min(totalPages - 1, currentPage - 1));
  const start = Math.max(0, currentIndex - 5);
  const end = Math.min(totalPages, currentIndex + 6);
  return Array.from({ length: end - start }, (_, offset) => start + offset);
}

export function getSeriesMemberNavigation(
  members: SeriesMember[],
  currentId?: string,
  currentTitle?: string,
): { index: number; previous: SeriesMember | null; next: SeriesMember | null } {
  const index = members.findIndex((member) =>
    currentId ? member.id === currentId : member.title === currentTitle,
  );
  return {
    index,
    previous: index > 0 ? members[index - 1] : null,
    next: index >= 0 && index < members.length - 1 ? members[index + 1] : null,
  };
}
