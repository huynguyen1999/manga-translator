import type { StoryArchive, StoryPlan, StorySegment, StudioFile } from "@/types";

const looseArchiveId = "__loose__";

export function buildStoryPlan(files: StudioFile[], enabled = true, autoDetect = true): StoryPlan {
  const ordered = [...files].sort((a, b) => a.dropOrder - b.dropOrder);
  const archives: StoryArchive[] = [];
  let page = 1;
  for (const entry of ordered) {
    const id = entry.archiveId || looseArchiveId;
    const name = entry.archiveName || "Loose images";
    const current = archives.find((archive) => archive.id === id);
    if (current) {
      current.endPage = page;
      current.pageCount += 1;
    } else {
      archives.push({ id, name, startPage: page, endPage: page, pageCount: 1 });
    }
    page += 1;
  }
  const segments: StorySegment[] = archives.map((archive, index) => ({
    id: `story-${index + 1}`,
    label: `Story ${index + 1}`,
    startPage: archive.startPage,
    endPage: archive.endPage,
    archiveId: archive.id,
  }));
  return { enabled, autoDetect, mergeAllPages: false, archives, segments };
}

export function mergeStoryPlan(plan: StoryPlan): StoryPlan {
  const totalPages = plan.archives.reduce((total, archive) => total + archive.pageCount, 0);
  return {
    ...plan,
    mergeAllPages: true,
    segments: totalPages ? [{ id: "story-1", label: "All pages", startPage: 1, endPage: totalPages }] : [],
  };
}

export function updateStoryBoundary(plan: StoryPlan, index: number, endPage: number): StoryPlan {
  const current = plan.segments[index];
  const next = plan.segments[index + 1];
  if (!current || !next) return plan;
  const min = current.startPage;
  const max = next.endPage - 1;
  const clamped = Math.max(min, Math.min(max, Math.round(endPage)));
  const segments = plan.segments.map((segment, segmentIndex) => {
    if (segmentIndex === index) return { ...segment, endPage: clamped };
    if (segmentIndex === index + 1) return { ...segment, startPage: clamped + 1 };
    return segment;
  });
  return { ...plan, mergeAllPages: false, segments };
}

export function addStorySplit(plan: StoryPlan, archiveId?: string): StoryPlan {
  const candidate = plan.segments
    .map((segment, index) => ({ segment, index, span: segment.endPage - segment.startPage + 1 }))
    .filter(({ segment, span }) => span > 1 && (!archiveId || segment.archiveId === archiveId))
    .sort((a, b) => b.span - a.span)[0];
  if (!candidate) return plan;
  const { segment, index } = candidate;
  const split = Math.floor((segment.startPage + segment.endPage) / 2);
  const nextSegments = [
    ...plan.segments.slice(0, index),
    { ...segment, id: `${segment.id}-a`, endPage: split, label: segment.label || `Story ${index + 1}` },
    { id: `${segment.id}-b`, startPage: split + 1, endPage: segment.endPage, label: `Story ${index + 2}`, archiveId: segment.archiveId },
    ...plan.segments.slice(index + 1),
  ];
  return { ...plan, mergeAllPages: false, segments: nextSegments };
}

export function addStorySplitAt(plan: StoryPlan, afterPage: number): StoryPlan {
  const index = plan.segments.findIndex(
    (segment) => segment.startPage <= afterPage && afterPage < segment.endPage
  );
  if (index === -1) return plan;
  const segment = plan.segments[index];
  const first = {
    ...segment,
    id: `${segment.id}-a-${afterPage}`,
    endPage: afterPage,
    label: segment.label || `Story ${index + 1}`,
  };
  const second: StorySegment = {
    id: `${segment.id}-b-${afterPage}`,
    startPage: afterPage + 1,
    endPage: segment.endPage,
    label: `Story ${index + 2}`,
    archiveId: segment.archiveId,
  };
  const nextSegments = [
    ...plan.segments.slice(0, index),
    first,
    second,
    ...plan.segments.slice(index + 1),
  ];
  return { ...plan, mergeAllPages: false, segments: nextSegments };
}

export function removeStorySplitAt(plan: StoryPlan, afterPage: number): StoryPlan {
  const index = plan.segments.findIndex((segment) => segment.endPage === afterPage);
  if (index === -1 || index >= plan.segments.length - 1) return plan;
  const current = plan.segments[index];
  const next = plan.segments[index + 1];
  if (!plan.mergeAllPages && current.archiveId && next.archiveId && current.archiveId !== next.archiveId) {
    return plan;
  }
  const merged: StorySegment = { ...current, endPage: next.endPage };
  return {
    ...plan,
    mergeAllPages: false,
    segments: [...plan.segments.slice(0, index), merged, ...plan.segments.slice(index + 2)],
  };
}

export function removeStorySplit(plan: StoryPlan, index: number): StoryPlan {
  if (index < 0 || index >= plan.segments.length || plan.segments.length <= 1) return plan;
  if (index > 0) {
    const previous = plan.segments[index - 1];
    const current = plan.segments[index];
    if (!plan.mergeAllPages && previous.archiveId && current.archiveId && previous.archiveId !== current.archiveId) {
      if (index < plan.segments.length - 1) {
        const next = plan.segments[index + 1];
        if (plan.mergeAllPages || !current.archiveId || !next.archiveId || current.archiveId === next.archiveId) {
          const merged: StorySegment = { ...current, endPage: next.endPage };
          return {
            ...plan,
            mergeAllPages: false,
            segments: [...plan.segments.slice(0, index), merged, ...plan.segments.slice(index + 2)],
          };
        }
      }
      return plan;
    }
    const merged: StorySegment = { ...previous, endPage: current.endPage };
    return {
      ...plan,
      mergeAllPages: false,
      segments: [...plan.segments.slice(0, index - 1), merged, ...plan.segments.slice(index + 1)],
    };
  }
  // index === 0
  const current = plan.segments[0];
  const next = plan.segments[1];
  if (plan.mergeAllPages || !current.archiveId || !next.archiveId || current.archiveId === next.archiveId) {
    const merged: StorySegment = { ...current, endPage: next.endPage };
    return {
      ...plan,
      mergeAllPages: false,
      segments: [merged, ...plan.segments.slice(2)],
    };
  }
  return plan;
}

export function renameStorySegment(plan: StoryPlan, id: string, label: string): StoryPlan {
  return { ...plan, segments: plan.segments.map((segment) => segment.id === id ? { ...segment, label: label.trim() || undefined } : segment) };
}

export function validateStoryPlan(plan: StoryPlan, totalPages: number): { valid: boolean; errors: string[] } {
  const errors: string[] = [];
  const segments = [...plan.segments].sort((a, b) => a.startPage - b.startPage);
  if (totalPages === 0 && segments.length) errors.push("There are no pages to assign.");
  if (totalPages > 0 && (segments[0]?.startPage !== 1 || segments[segments.length - 1]?.endPage !== totalPages)) {
    errors.push("Story ranges must cover every page.");
  }
  for (const [index, segment] of segments.entries()) {
    if (segment.startPage < 1 || segment.endPage < segment.startPage || segment.endPage > totalPages) errors.push("A story range is outside the page list.");
    const next = segments[index + 1];
    if (next && segment.endPage + 1 !== next.startPage) errors.push("Story ranges must be contiguous.");
  }
  return { valid: errors.length === 0 && (totalPages === 0 || segments.length > 0), errors };
}
