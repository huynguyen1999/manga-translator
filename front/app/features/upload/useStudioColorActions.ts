import { useCallback } from "react";
import type { Dispatch, SetStateAction } from "react";
import type { StudioFile, TranslationBatch } from "@/types";
import { detectIsImageColored } from "@/utils/colorDetector";
import { updateBatchItem } from "@/utils/serverBatches";

type StudioColorActionsOptions = {
  selectedFiles: Set<string>;
  translationBatches: TranslationBatch[];
  setExcludedColorFiles: Dispatch<SetStateAction<Set<string>>>;
  setAutoDetectedColorFiles: Dispatch<SetStateAction<Set<string>>>;
  setTranslationBatches: Dispatch<SetStateAction<TranslationBatch[]>>;
};

export const useStudioColorActions = ({
  selectedFiles,
  translationBatches,
  setExcludedColorFiles,
  setAutoDetectedColorFiles,
  setTranslationBatches,
}: StudioColorActionsOptions) => {
  const toggleExcludeColorFile = (fileName: string) => {
    setExcludedColorFiles((prev) => {
      const next = new Set(prev);
      if (next.has(fileName)) {
        next.delete(fileName);
      } else {
        next.add(fileName);
      }
      return next;
    });
  };

  const setExcludeColorForSelected = (exclude: boolean) => {
    setExcludedColorFiles((prev) => {
      const next = new Set(prev);
      selectedFiles.forEach((fileName) => {
        if (exclude) {
          next.add(fileName);
        } else {
          next.delete(fileName);
        }
      });
      return next;
    });
  };

  const toggleQueueItemColor = (batchId: string, itemId: string) => {
    const item = translationBatches
      .find((batch) => batch.id === batchId)
      ?.items.find((entry) => entry.id === itemId);
    if (!item) return;
    const excludeColor = !item.excludeColor;
    setTranslationBatches((prev) =>
      prev.map((batch) =>
        batch.id !== batchId
          ? batch
          : {
              ...batch,
              items: batch.items.map((item) =>
                item.id === itemId ? { ...item, excludeColor } : item
              ),
          }
      )
    );
    void updateBatchItem(batchId, itemId, excludeColor).catch((error) =>
      console.warn("Failed to update batch item:", error)
    );
  };

  const checkColorForFiles = useCallback(async (newFiles: StudioFile[]) => {
    const coloredNames: string[] = [];
    const concurrency = 4;
    for (let i = 0; i < newFiles.length; i += concurrency) {
      const chunk = newFiles.slice(i, i + concurrency);
      const results = await Promise.all(
        chunk.map(async (entry) => {
          try {
            const isColored = await detectIsImageColored(entry.file);
            return isColored ? entry.id : null;
          } catch {
            return null;
          }
        })
      );
      results.forEach((name) => {
        if (name) coloredNames.push(name);
      });
    }
    if (coloredNames.length > 0) {
      setAutoDetectedColorFiles((prev) => {
        const next = new Set(prev);
        coloredNames.forEach((name) => next.add(name));
        return next;
      });
      setExcludedColorFiles((prev) => {
        const next = new Set(prev);
        coloredNames.forEach((name) => next.add(name));
        return next;
      });
    }
  }, []);

  return {
    toggleExcludeColorFile,
    setExcludeColorForSelected,
    toggleQueueItemColor,
    checkColorForFiles,
  };
};
