import type { Dispatch, SetStateAction } from "react";
import type { FinishedImage, MangaGroupSummary } from "@/types";
import { apiUrl } from "@/utils/api";
import {
  fetchServerBatch,
  fetchServerBatches,
  getBatchKind,
} from "@/utils/serverBatches";

type GalleryMutationOptions = {
  setFinishedImages: Dispatch<SetStateAction<FinishedImage[]>>;
  setMangaSummaries: Dispatch<SetStateAction<MangaGroupSummary[]>>;
  setTotalMangaCount: Dispatch<SetStateAction<number>>;
  setTotalGalleryCount: Dispatch<SetStateAction<number>>;
  setIsGalleryLoading: Dispatch<SetStateAction<boolean>>;
  setGalleryRevision: Dispatch<SetStateAction<number>>;
  galleryPageCacheRef: { current: Map<string, { groups: MangaGroupSummary[]; totalGroups: number; totalImages: number }> };
  loadMangaSummaries: (signal?: AbortSignal) => Promise<void>;
};

export const createGalleryMutationActions = ({
  setFinishedImages,
  setMangaSummaries,
  setTotalMangaCount,
  setTotalGalleryCount,
  setIsGalleryLoading,
  setGalleryRevision,
  galleryPageCacheRef,
  loadMangaSummaries,
}: GalleryMutationOptions) => {
  const handleUpdateMangaTitle = async (
    pageIds: string[],
    newMangaTitle: string,
    oldMangaTitle?: string,
    groupId?: string,
    folders: string[] = pageIds,
  ) => {
    const cleanTitle = newMangaTitle.trim() || "Ungrouped";
    setFinishedImages((prev) =>
      prev.map((img) =>
        pageIds.includes(img.id) ||
        (img.folder && folders.includes(img.folder)) ||
        (oldMangaTitle && img.mangaTitle === oldMangaTitle)
          ? { ...img, mangaTitle: cleanTitle }
          : img
      )
    );

    try {
      const validPageIds = pageIds.filter((id) => id.length > 0);
      const validFolders = folders.filter((folder) => !folder.includes("/") && folder.length > 0);
      await fetch(apiUrl("/api/results/update-meta"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          folders: validFolders.length > 0 ? validFolders : undefined,
          pageIds: validPageIds.length > 0 ? validPageIds : undefined,
          oldMangaTitle: oldMangaTitle || undefined,
          mangaTitle: cleanTitle,
          groupId: groupId || undefined,
        }),
      });
      await loadMangaSummaries();
    } catch (err) {
      console.warn("Failed to update manga title on server:", err);
    }
  };

  const restoreBatchPages = async (groupId: string, mangaTitle: string): Promise<number> => {
    const cleanTitle = mangaTitle.trim().toLocaleLowerCase();
    const summaries = await fetchServerBatches();
    const candidates = summaries.filter((batch) =>
      getBatchKind(batch) === "translation" &&
      (batch.status === "completed" || batch.status === "error") &&
      (batch.mangaGroupId === groupId || batch.mangaTitle.trim().toLocaleLowerCase() === cleanTitle)
    );
    const batches = await Promise.all(candidates.map((batch) => fetchServerBatch(batch.id)));
    const folders = Array.from(new Set(batches.flatMap((batch) =>
      batch.items
        .filter((item) => item.status === "completed" && item.resultFolder)
        .map((item) => item.resultFolder as string)
    )));
    if (folders.length === 0) throw new Error("No completed batch pages were found for this manga.");

    const response = await fetch(apiUrl("/api/results/update-meta"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folders, mangaTitle }),
    });
    const payload = await response.json().catch(() => ({})) as { detail?: string; updated?: number };
    if (!response.ok) {
      throw new Error(payload.detail || `Could not restore batch pages (${response.status})`);
    }

    galleryPageCacheRef.current.clear();
    setGalleryRevision((revision) => revision + 1);
    await loadMangaSummaries();
    return payload.updated ?? folders.length;
  };

  const updateFinishedImage = (updated: FinishedImage) => {
    setFinishedImages((prev) =>
      prev.map((img) =>
        img.id === updated.id || (img.folder && img.folder === updated.folder) ? updated : img
      )
    );
  };

  const deleteFinishedImage = async (image: FinishedImage) => {
    if (image.folder) {
      try {
        await fetch(apiUrl(`/api/results/${image.folder}`), { method: "DELETE" });
      } catch (err) {
        console.warn(`Failed to delete result ${image.folder} on server:`, err);
      }
    }
    setFinishedImages((prev) => prev.filter((img) => img.id !== image.id));
    setTotalGalleryCount((prev) => Math.max(0, prev - 1));
    setMangaSummaries((prev) => {
      const mangaTitle = (image.mangaTitle || "Ungrouped").trim() || "Ungrouped";
      return prev
        .map((g) => (g.title === mangaTitle ? { ...g, count: Math.max(0, g.count - 1) } : g))
        .filter((g) => g.count > 0);
    });
  };

  const deleteFinishedImages = async (images: FinishedImage[]) => {
    if (images.length === 0) return;
    galleryPageCacheRef.current.clear();
    const folders = [...new Set(images.map((img) => img.folder).filter(Boolean) as string[])];
    if (folders.length > 0) {
      const response = await fetch(apiUrl("/api/results/batch-delete"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folders }),
      });
      if (!response.ok) throw new Error(`Could not delete selected pages (${response.status}).`);
    }
    const deletedIds = new Set(images.map((img) => img.id));
    const titleCounts = new Map<string, number>();
    for (const img of images) {
      const title = (img.mangaTitle || "Ungrouped").trim() || "Ungrouped";
      titleCounts.set(title, (titleCounts.get(title) || 0) + 1);
    }

    setFinishedImages((prev) => prev.filter((img) => !deletedIds.has(img.id)));
    setTotalGalleryCount((prev) => Math.max(0, prev - images.length));
    setMangaSummaries((prev) => prev
      .map((g) => {
        const removed = titleCounts.get(g.title) || 0;
        return removed > 0 ? { ...g, count: Math.max(0, g.count - removed) } : g;
      })
      .filter((g) => g.count > 0));
  };

  const reorderMangaPages = async (groupId: string, pageIds: string[]) => {
    const response = await fetch(apiUrl(`/api/manga/${encodeURIComponent(groupId)}/pages/order`), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pageIds }),
    });
    if (!response.ok) {
      throw new Error(`Could not save page order (${response.status})`);
    }
    const payload = await response.json() as { pages?: Array<{ id: string; pageOrder: number }> };
    const orders = new Map((payload.pages || []).map((page) => [page.id, page.pageOrder]));
    setFinishedImages((previous) => previous.map((image) => (
      orders.has(image.id) ? { ...image, pageOrder: orders.get(image.id) } : image
    )));
    await loadMangaSummaries();
  };

  const deleteMangaGroup = async (images: FinishedImage[], mangaTitle?: string) => {
    const title = mangaTitle || images[0]?.mangaTitle || "Ungrouped";
    galleryPageCacheRef.current.clear();
    try {
      await fetch(apiUrl(`/api/results/group?title=${encodeURIComponent(title)}`), { method: "DELETE" });
    } catch (err) {
      await Promise.allSettled(
        images
          .filter((img) => img.folder)
          .map((img) => fetch(apiUrl(`/api/results/${img.folder}`), { method: "DELETE" }))
      );
    }
    const ids = new Set(images.map((img) => img.id));
    setFinishedImages((prev) => prev.filter((img) => !ids.has(img.id) && img.mangaTitle !== title));
    setMangaSummaries((prev) => {
      const match = prev.find((g) => g.title === title);
      if (match) {
        setTotalGalleryCount((count) => Math.max(0, count - match.count));
        setTotalMangaCount((count) => Math.max(0, count - 1));
      }
      return prev.filter((g) => g.title !== title);
    });
  };

  const deleteMangaGroups = async (mangaList: Array<{ title: string; images?: FinishedImage[] }>) => {
    if (mangaList.length === 0) return;
    galleryPageCacheRef.current.clear();
    await Promise.allSettled(
      mangaList.map(async ({ title, images = [] }) => {
        try {
          await fetch(apiUrl(`/api/results/group?title=${encodeURIComponent(title)}`), { method: "DELETE" });
        } catch {
          await Promise.allSettled(
            images
              .filter((img) => img.folder)
              .map((img) => fetch(apiUrl(`/api/results/${img.folder}`), { method: "DELETE" }))
          );
        }
      })
    );
    const titlesToDelete = new Set(mangaList.map((m) => m.title));
    const allImagesToDelete = mangaList.flatMap((m) => m.images || []);
    const idsToDelete = new Set(allImagesToDelete.map((img) => img.id));
    setFinishedImages((prev) =>
      prev.filter(
        (img) => !idsToDelete.has(img.id) && !titlesToDelete.has((img.mangaTitle || "Ungrouped").trim() || "Ungrouped")
      )
    );
    setMangaSummaries((prev) => {
      let removedTotal = 0;
      let removedCount = 0;
      for (const g of prev) {
        if (titlesToDelete.has(g.title)) {
          removedTotal += g.count;
          removedCount += 1;
        }
      }
      setTotalGalleryCount((count) => Math.max(0, count - removedTotal));
      setTotalMangaCount((count) => Math.max(0, count - removedCount));
      return prev.filter((g) => !titlesToDelete.has(g.title));
    });
  };

  return {
    handleUpdateMangaTitle,
    restoreBatchPages,
    updateFinishedImage,
    deleteFinishedImage,
    deleteFinishedImages,
    reorderMangaPages,
    deleteMangaGroup,
    deleteMangaGroups,
  };
};
