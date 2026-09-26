import type { Dispatch, SetStateAction } from "react";
import { processingStatuses, type FileStatus, type StudioFile } from "@/types";
import {
  clearBrowserTextSelection,
  computeRangeSelection,
  type SelectionAnchor,
} from "@/utils/selectionUtils";

type StudioFileSelectionOptions = {
  files: StudioFile[];
  selectedFiles: Set<string>;
  fileStatuses: Map<string, FileStatus>;
  setFiles: Dispatch<SetStateAction<StudioFile[]>>;
  setSelectedFiles: Dispatch<SetStateAction<Set<string>>>;
  setExcludedColorFiles: Dispatch<SetStateAction<Set<string>>>;
  setAutoDetectedColorFiles: Dispatch<SetStateAction<Set<string>>>;
  setFileStatuses: Dispatch<SetStateAction<Map<string, FileStatus>>>;
  lastSelectedFileRef: { current: SelectionAnchor | null };
  folderMapRef: { current: Map<string, string> };
  resultUrlsRef: { current: Set<string> };
};

export const createStudioFileSelectionActions = ({
  files,
  selectedFiles,
  fileStatuses,
  setFiles,
  setSelectedFiles,
  setExcludedColorFiles,
  setAutoDetectedColorFiles,
  setFileStatuses,
  lastSelectedFileRef,
  folderMapRef,
  resultUrlsRef,
}: StudioFileSelectionOptions) => {
  const removeFile = (fileId: string) => {
    setFiles((prev) => prev.filter((entry) => entry.id !== fileId));
    setSelectedFiles((prev) => {
      const next = new Set(prev);
      next.delete(fileId);
      return next;
    });
    if (lastSelectedFileRef.current?.id === fileId) {
      lastSelectedFileRef.current = null;
    }
    setExcludedColorFiles((prev) => {
      const next = new Set(prev);
      next.delete(fileId);
      return next;
    });
    setAutoDetectedColorFiles((prev) => {
      const next = new Set(prev);
      next.delete(fileId);
      return next;
    });
    folderMapRef.current.delete(fileId);
    resultUrlsRef.current.delete(fileId);
    setFileStatuses((prev) => {
      const newStatuses = new Map(prev);
      newStatuses.delete(fileId);
      return newStatuses;
    });
  };

  const removeSelectedFiles = () => {
    if (selectedFiles.size === 0) return;
    const toRemove = new Set(selectedFiles);
    setFiles((prev) => prev.filter((entry) => !toRemove.has(entry.id)));
    setSelectedFiles(new Set());
    lastSelectedFileRef.current = null;
    setExcludedColorFiles((prev) => {
      const next = new Set(prev);
      for (const id of toRemove) next.delete(id);
      return next;
    });
    setAutoDetectedColorFiles((prev) => {
      const next = new Set(prev);
      for (const id of toRemove) next.delete(id);
      return next;
    });
    for (const id of toRemove) {
      folderMapRef.current.delete(id);
      resultUrlsRef.current.delete(id);
    }
    setFileStatuses((prev) => {
      const newStatuses = new Map(prev);
      for (const id of toRemove) newStatuses.delete(id);
      return newStatuses;
    });
  };

  const toggleFileSelection = (fileId: string, shiftKey = false) => {
    const allIds = files.map((entry) => entry.id);
    const isItemSelectable = (id: string) => {
      const status = fileStatuses.get(id);
      const isCurrentFileActive = Boolean(
        status?.status && processingStatuses.includes(status.status)
      );
      return !isCurrentFileActive;
    };

    setSelectedFiles((prev) => {
      const res = computeRangeSelection({
        selectedIds: prev,
        allIds,
        targetId: fileId,
        shiftKey,
        anchor: lastSelectedFileRef.current,
        isItemSelectable,
      });
      lastSelectedFileRef.current = res.nextAnchor;
      return res.nextSelectedIds;
    });

    if (shiftKey) {
      clearBrowserTextSelection();
    }
  };

  const selectAllFiles = () => {
    setSelectedFiles(new Set(files.map((entry) => entry.id)));
    lastSelectedFileRef.current = null;
  };

  const deselectAllFiles = () => {
    setSelectedFiles(new Set());
    lastSelectedFileRef.current = null;
  };

  return { removeFile, removeSelectedFiles, toggleFileSelection, selectAllFiles, deselectAllFiles };
};
