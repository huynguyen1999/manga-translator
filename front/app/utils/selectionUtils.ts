export interface SelectionAnchor<T = string> {
  id: T;
  wasSelected: boolean;
}

export interface RangeSelectionParams<T = string> {
  /** The current set of selected item IDs/names */
  selectedIds: Set<T>;
  /** The full ordered list of item IDs as displayed */
  allIds: T[];
  /** The ID of the item clicked */
  targetId: T;
  /** Whether the Shift key was held during the click */
  shiftKey?: boolean;
  /** The anchor from the previous click, if any */
  anchor?: SelectionAnchor<T> | null;
  /** Optional filter to check if an item can be selected/deselected (e.g. not actively translating) */
  isItemSelectable?: (id: T) => boolean;
}

export interface RangeSelectionResult<T = string> {
  nextSelectedIds: Set<T>;
  nextAnchor: SelectionAnchor<T>;
}

/**
 * Computes the new selection Set and next anchor when an item is clicked,
 * supporting Shift + Click multi-selection across a range.
 */
export function computeRangeSelection<T = string>({
  selectedIds,
  allIds,
  targetId,
  shiftKey = false,
  anchor = null,
  isItemSelectable,
}: RangeSelectionParams<T>): RangeSelectionResult<T> {
  const currentIndex = allIds.indexOf(targetId);
  if (currentIndex === -1) {
    return {
      nextSelectedIds: new Set(selectedIds),
      nextAnchor: anchor ?? { id: targetId, wasSelected: selectedIds.has(targetId) },
    };
  }

  // If shift key is held and we have a valid anchor in allIds
  if (shiftKey && anchor) {
    const lastIndex = allIds.indexOf(anchor.id);
    if (lastIndex !== -1) {
      const start = Math.min(lastIndex, currentIndex);
      const end = Math.max(lastIndex, currentIndex);
      const shouldSelect = anchor.wasSelected;

      const next = new Set(selectedIds);
      for (let i = start; i <= end; i++) {
        const id = allIds[i];
        if (isItemSelectable && !isItemSelectable(id)) {
          continue;
        }
        if (shouldSelect) {
          next.add(id);
        } else {
          next.delete(id);
        }
      }

      return {
        nextSelectedIds: next,
        nextAnchor: { id: targetId, wasSelected: shouldSelect },
      };
    }
  }

  // Single item toggle (or Shift+click with no prior anchor)
  const isCurrentlySelected = selectedIds.has(targetId);
  const willBeSelected = !isCurrentlySelected;
  const next = new Set(selectedIds);
  if (willBeSelected) {
    next.add(targetId);
  } else {
    next.delete(targetId);
  }

  return {
    nextSelectedIds: next,
    nextAnchor: { id: targetId, wasSelected: willBeSelected },
  };
}

/**
 * Safely removes any text selection ranges in the browser window
 * that can occur when Shift+clicking elements.
 */
export function clearBrowserTextSelection(): void {
  if (typeof window !== "undefined" && window.getSelection) {
    window.getSelection()?.removeAllRanges();
  }
}
