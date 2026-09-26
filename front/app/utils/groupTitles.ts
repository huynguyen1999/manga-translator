import type { FinishedImage, MangaGroupSummary } from "@/types";

export interface ExistingGroupTitle {
  id?: string;
  title: string;
  count?: number;
}

export function buildExistingGroupEntries(
  serverGroupTitles: string[],
  mangaSummaries: MangaGroupSummary[],
  finishedImages: FinishedImage[],
): ExistingGroupTitle[] {
  const byTitle = new Map<string, ExistingGroupTitle>();
  serverGroupTitles.forEach((title) => {
    const clean = title.trim();
    if (clean && clean.toLocaleLowerCase() !== "ungrouped") {
      byTitle.set(clean.toLocaleLowerCase(), { title: clean });
    }
  });
  mangaSummaries.forEach((grp) => {
    const title = (grp.title || "").trim();
    if (title && title.toLocaleLowerCase() !== "ungrouped") {
      const key = title.toLocaleLowerCase();
      const existing = byTitle.get(key);
      byTitle.set(key, {
        id: grp.id || existing?.id,
        title,
        count: grp.count !== undefined ? grp.count : existing?.count,
      });
    }
  });
  finishedImages.forEach((img) => {
    const title = (img.mangaTitle || "").trim();
    if (title && title.toLocaleLowerCase() !== "ungrouped") {
      const key = title.toLocaleLowerCase();
      if (!byTitle.has(key)) {
        byTitle.set(key, { id: img.groupId || undefined, title });
      }
    }
  });
  return Array.from(byTitle.values()).sort((a, b) =>
    a.title.localeCompare(b.title, undefined, { numeric: true, sensitivity: "base" })
  );
}
