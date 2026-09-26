import type { FinishedImage } from "@/types";
import { apiUrl } from "@/utils/api";

export const downloadGalleryImage = async (img: FinishedImage) => {
  const isOriginal = img.sourceType === "original";
  const source = isOriginal ? img.inputUrl : img.result;
  let url: string;
  let isBlob = false;
  if (source instanceof Blob) {
    if (!isOriginal && source.size < 1000 && img.folder) {
      url = apiUrl(`/result/${img.folder}/final.png`);
    } else {
      url = URL.createObjectURL(source);
      isBlob = true;
    }
  } else if (typeof source === "string" && source) {
    try {
      const response = await fetch(apiUrl(source));
      if (!response.ok) throw new Error(`Download failed: ${response.status}`);
      url = URL.createObjectURL(await response.blob());
      isBlob = true;
    } catch (error) {
      console.error("Failed to download image:", error);
      return;
    }
  } else if (img.folder) {
    url = apiUrl(
      `/result/${img.folder}/${isOriginal ? "input.png" : "final.png"}`,
    );
  } else {
    return;
  }
  const a = document.createElement("a");
  a.href = url;
  a.download = `${isOriginal ? "original" : "translated"}_${img.originalName}`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  if (isBlob) {
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
};

export const downloadMangaCbzArchive = async (
  mangaTitle: string,
  images: FinishedImage[],
  groupId: string | null | undefined,
  original: boolean,
): Promise<void> => {
  // For named manga series, direct GET stream allows browser native download streaming
  // avoiding massive JS heap memory allocations and connection timeouts
  if (mangaTitle && mangaTitle !== "Ungrouped") {
    const a = document.createElement("a");
    a.href = apiUrl(
      `/api/results/export/cbz?groupId=${encodeURIComponent(groupId || mangaTitle)}&manga=${encodeURIComponent(mangaTitle)}&original=${original}`,
    );
    const safeTitle =
      mangaTitle
        .replace(/[\\:*?"<>|\u0000-\u001f]/g, "_")
        .replace(/\//g, "_")
        .replace(/[. ]+$/, "") || "manga";
    a.download = `${safeTitle}${original ? "_original" : ""}.cbz`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    return;
  }

  const folders = images.map((img) => img.folder).filter(Boolean) as string[];
  const response = await fetch(apiUrl("/api/results/export/cbz"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      groupId: groupId || undefined,
      mangaTitle,
      folders,
      original,
    }),
  });

  if (!response.ok) {
    throw new Error(`Export failed: ${response.statusText}`);
  }

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  const safeTitle =
    mangaTitle
      .replace(/[\\:*?"<>|\u0000-\u001f]/g, "_")
      .replace(/\//g, "_")
      .replace(/[. ]+$/, "") || "manga";
  a.download = `${safeTitle}${original ? "_original" : ""}.cbz`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 2000);
};
