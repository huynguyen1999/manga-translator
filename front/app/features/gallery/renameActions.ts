import type { Dispatch, SetStateAction } from "react";
import type { FinishedImage } from "@/types";
import type { GalleryMangaGroup } from "@/utils/resultGallery";
import { mangaIdForTitle } from "@/utils/routeState";

type RenameActionsOptions = {
  mangaGroups: Array<Pick<GalleryMangaGroup, "id" | "title" | "images">>;
  renameInputValue: string;
  activeMangaFilter: string;
  onUpdateMangaTitle?: (
    pageIds: string[],
    newTitle: string,
    oldTitle?: string,
    groupId?: string,
    folders?: string[],
  ) => void;
  onOpenMangaDetail?: (mangaId: string) => void;
  setActiveMangaFilter: Dispatch<SetStateAction<string>>;
  setMangaImages: Dispatch<SetStateAction<Record<string, FinishedImage[]>>>;
  setExpandedGroups: Dispatch<SetStateAction<Record<string, boolean>>>;
  setSelectedMangaIds: Dispatch<SetStateAction<Map<string, string>>>;
  setRenamingManga: Dispatch<SetStateAction<string | null>>;
  setRenameInputValue: Dispatch<SetStateAction<string>>;
};

export const createGalleryRenameActions = ({
  mangaGroups,
  renameInputValue,
  activeMangaFilter,
  onUpdateMangaTitle,
  onOpenMangaDetail,
  setActiveMangaFilter,
  setMangaImages,
  setExpandedGroups,
  setSelectedMangaIds,
  setRenamingManga,
  setRenameInputValue,
}: RenameActionsOptions) => {
  const handleStartRename = (title: string) => {
    setRenamingManga(title);
    setRenameInputValue(title);
  };

  const handleSaveRename = (oldTitle: string) => {
    const newTitle = renameInputValue.trim();
    if (newTitle && newTitle !== oldTitle) {
      const group = mangaGroups.find((item) => item.title === oldTitle);
      const folders = group?.images
        ? group.images.map((image) => image.folder || image.id).filter(Boolean)
        : [];
      const pageIds = group?.images?.map((image) => image.id).filter(Boolean) || [];
      onUpdateMangaTitle?.(pageIds, newTitle, oldTitle, group?.id, folders);
      if (typeof window !== "undefined") {
        for (const suffix of ["pos", "page", "count"]) {
          const oldKey = `manga-read-${suffix}-${oldTitle}`;
          const newKey = `manga-read-${suffix}-${newTitle}`;
          const value = window.localStorage.getItem(oldKey);
          if (value !== null && window.localStorage.getItem(newKey) === null) {
            window.localStorage.setItem(newKey, value);
          }
          window.localStorage.removeItem(oldKey);
        }
      }
      if (activeMangaFilter === oldTitle) {
        if (onOpenMangaDetail) {
          onOpenMangaDetail(group?.id || mangaIdForTitle(newTitle));
        } else {
          setActiveMangaFilter(newTitle);
        }
      }
      setMangaImages((previous) => {
        if (previous[oldTitle]) {
          const next = { ...previous, [newTitle]: previous[oldTitle].map((image) => ({ ...image, mangaTitle: newTitle })) };
          delete next[oldTitle];
          return next;
        }
        return previous;
      });
      setExpandedGroups((previous) => {
        if (previous[oldTitle] !== undefined) {
          const next = { ...previous, [newTitle]: previous[oldTitle] };
          delete next[oldTitle];
          return next;
        }
        return previous;
      });
      if (group?.id) {
        setSelectedMangaIds((previous) => {
          if (!previous.has(group.id)) return previous;
          const next = new Map(previous);
          next.set(group.id, newTitle);
          return next;
        });
      }
    }
    setRenamingManga(null);
  };

  return { handleStartRename, handleSaveRename };
};
