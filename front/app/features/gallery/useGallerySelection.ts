import { useCallback, useRef, useState } from 'react';
import type { FinishedImage } from '@/types';
import {
  clearBrowserTextSelection,
  computeRangeSelection,
  type SelectionAnchor,
} from '@/utils/selectionUtils';

export function useGallerySelection() {
  const [selectedImageIds, setSelectedImageIds] = useState<Set<string>>(new Set());
  const lastSelectedGalleryIdRef = useRef<SelectionAnchor | null>(null);

  const toggleSelectImage = useCallback((id: string, groupImages?: FinishedImage[], shiftKey = false) => {
    const allIds = groupImages && groupImages.length > 0 ? groupImages.map((img) => img.id) : [id];

    setSelectedImageIds((prev) => {
      const res = computeRangeSelection({
        selectedIds: prev,
        allIds,
        targetId: id,
        shiftKey,
        anchor: lastSelectedGalleryIdRef.current,
      });
      lastSelectedGalleryIdRef.current = res.nextAnchor;
      return res.nextSelectedIds;
    });

    if (shiftKey) {
      clearBrowserTextSelection();
    }
  }, []);

  const toggleSelectAllInGroup = (images: FinishedImage[]) => {
    const allSelected = images.every((img) => selectedImageIds.has(img.id));
    setSelectedImageIds((prev) => {
      const next = new Set(prev);
      images.forEach((img) => {
        if (allSelected) {
          next.delete(img.id);
        } else {
          next.add(img.id);
        }
      });
      return next;
    });
    lastSelectedGalleryIdRef.current = null;
  };

  const clearSelectedImages = useCallback(() => {
    setSelectedImageIds(new Set());
    lastSelectedGalleryIdRef.current = null;
  }, []);

  return {
    selectedImageIds,
    toggleSelectImage,
    toggleSelectAllInGroup,
    clearSelectedImages,
  };
}
