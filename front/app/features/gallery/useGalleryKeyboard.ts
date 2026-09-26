import { useEffect, type Dispatch, type SetStateAction } from "react";
import type { MangaSummaryModalState } from "./MangaSummaryModal";

type EscapeState = {
  summaryOpen: boolean;
  createSeriesOpen: boolean;
  moveMangaOpen: boolean;
  deleteMangaOpen: boolean;
  deletePagesOpen: boolean;
  deleteMangasOpen: boolean;
};

export function getGalleryEscapeAction(state: EscapeState) {
  if (state.summaryOpen) return "summary";
  if (state.createSeriesOpen) return "create-series";
  if (state.moveMangaOpen) return "move-manga";
  if (state.deletePagesOpen) return "delete-pages";
  if (state.deleteMangasOpen) return "delete-mangas";
  if (state.deleteMangaOpen) return "delete-manga";
  return null;
}

export function shouldClearMangaSelectionOnEscape(selectedCount: number, state: EscapeState) {
  return selectedCount > 0 && getGalleryEscapeAction(state) === null;
}

type GalleryKeyboardOptions = EscapeState & {
  selectedMangaCount: number;
  setSummaryState: Dispatch<SetStateAction<MangaSummaryModalState | null>>;
  setCreateSeriesOpen: Dispatch<SetStateAction<boolean>>;
  setMoveMangaOpen: Dispatch<SetStateAction<boolean>>;
  setDeleteManga: Dispatch<SetStateAction<string | null>>;
  setDeletePagesOpen: Dispatch<SetStateAction<boolean>>;
  setDeleteMangasOpen: Dispatch<SetStateAction<boolean>>;
  setSelectedMangaIds: Dispatch<SetStateAction<Map<string, string>>>;
};

export function useGalleryKeyboard(options: GalleryKeyboardOptions) {
  const {
    summaryOpen,
    createSeriesOpen,
    moveMangaOpen,
    deleteMangaOpen,
    deletePagesOpen,
    deleteMangasOpen,
    selectedMangaCount,
    setSummaryState,
    setCreateSeriesOpen,
    setMoveMangaOpen,
    setDeleteManga,
    setDeletePagesOpen,
    setDeleteMangasOpen,
    setSelectedMangaIds,
  } = options;

  useEffect(() => {
    const state = {
      summaryOpen,
      createSeriesOpen,
      moveMangaOpen,
      deleteMangaOpen,
      deletePagesOpen,
      deleteMangasOpen,
    };
    if (getGalleryEscapeAction(state) === null) return;

    const handleModalKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      switch (getGalleryEscapeAction(state)) {
        case "summary":
          setSummaryState(null);
          break;
        case "create-series":
          setCreateSeriesOpen(false);
          break;
        case "move-manga":
          setMoveMangaOpen(false);
          break;
        case "delete-pages":
          setDeletePagesOpen(false);
          break;
        case "delete-mangas":
          setDeleteMangasOpen(false);
          break;
        case "delete-manga":
          setDeleteManga(null);
          break;
      }
    };
    window.addEventListener("keydown", handleModalKeyDown);
    return () => window.removeEventListener("keydown", handleModalKeyDown);
  }, [
    summaryOpen,
    createSeriesOpen,
    moveMangaOpen,
    deleteMangaOpen,
    deletePagesOpen,
    deleteMangasOpen,
    setSummaryState,
    setCreateSeriesOpen,
    setMoveMangaOpen,
    setDeleteManga,
    setDeletePagesOpen,
    setDeleteMangasOpen,
  ]);

  useEffect(() => {
    const state = {
      summaryOpen,
      createSeriesOpen,
      moveMangaOpen,
      deleteMangaOpen,
      deletePagesOpen,
      deleteMangasOpen,
    };
    if (!shouldClearMangaSelectionOnEscape(selectedMangaCount, state)) return;

    const clearSelection = (event: KeyboardEvent) => {
      if (event.key === "Escape" && shouldClearMangaSelectionOnEscape(selectedMangaCount, state)) {
        setSelectedMangaIds(new Map());
      }
    };
    window.addEventListener("keydown", clearSelection);
    return () => window.removeEventListener("keydown", clearSelection);
  }, [
    selectedMangaCount,
    summaryOpen,
    createSeriesOpen,
    moveMangaOpen,
    deleteMangaOpen,
    deletePagesOpen,
    deleteMangasOpen,
    setSelectedMangaIds,
  ]);
}
