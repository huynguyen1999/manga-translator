import { useState, type Dispatch, type MutableRefObject, type SetStateAction } from "react";
import type { EditableTextBlock, FinishedImage } from "@/types";
import { renderMangaEditorCanvas } from "./canvas";
import { saveEditorEdits } from "./saveEditorEdits";

export function useEditorExports({
  image,
  blocks,
  bgImageRef,
  imageSize,
  setIsDirty,
  resetHistory,
  onSave,
}: {
  image: FinishedImage;
  blocks: EditableTextBlock[];
  bgImageRef: MutableRefObject<HTMLImageElement | null>;
  imageSize: { width: number; height: number };
  setIsDirty: Dispatch<SetStateAction<boolean>>;
  resetHistory: () => void;
  onSave?: (updatedImage: FinishedImage) => void;
}) {
  const [saving, setSaving] = useState(false);

  const handleExportPng = async () => {
    try {
      const canvas = await renderMangaEditorCanvas(bgImageRef.current, imageSize, blocks);
      canvas.toBlob((blob) => {
        if (!blob) return;
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        const nameWithoutExt = image.originalName.replace(/\.[^/.]+$/, "");
        anchor.download = `${nameWithoutExt}_typeset.png`;
        anchor.click();
        URL.revokeObjectURL(url);
      }, "image/png");
    } catch (error) {
      console.error("Failed to export composite image:", error);
      alert("Failed to export image. Check console for details.");
    }
  };

  const handleSaveToStudio = async () => {
    if (!image.folder) {
      alert("Image does not have an active result folder on the server.");
      return;
    }
    setSaving(true);
    try {
      const canvas = await renderMangaEditorCanvas(bgImageRef.current, imageSize, blocks);
      const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
      if (!blob) throw new Error("Failed to encode canvas image.");

      const base64Image = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onloadend = () => {
          if (typeof reader.result === "string") resolve(reader.result);
          else reject(new Error("Failed to convert image to data URL"));
        };
        reader.onerror = () => reject(reader.error || new Error("Failed to read image blob"));
        reader.readAsDataURL(blob);
      });

      const saved = await saveEditorEdits(image.folder, blocks, base64Image);
      setIsDirty(false);
      resetHistory();
      if (onSave) {
        onSave({
          ...image,
          result: blob,
          hasTextRegions: true,
          reviewStatus: saved.reviewStatus,
          reviewedAt: saved.reviewedAt ?? null,
        });
      }
    } catch (error) {
      console.error("Save failed:", error);
      alert(`Save failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setSaving(false);
    }
  };

  const handleExportJson = () => {
    const blob = new Blob([JSON.stringify(blocks, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${image.originalName}_typesetting.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return { saving, handleExportPng, handleSaveToStudio, handleExportJson };
}
