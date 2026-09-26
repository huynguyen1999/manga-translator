import type { Dispatch, SetStateAction } from "react";
import type { FileStatus, StudioFile } from "@/types";
import type { RestoredStudioState } from "@/utils/fileStorage";

type StudioStateSetter<T> = Dispatch<SetStateAction<T>>;

type RestoreTargets = {
  setFiles: StudioStateSetter<StudioFile[]>;
  setFileStatuses: StudioStateSetter<Map<string, FileStatus>>;
  setSelectedFiles: StudioStateSetter<Set<string>>;
  setExcludedColorFiles: StudioStateSetter<Set<string>>;
  setAutoDetectedColorFiles: StudioStateSetter<Set<string>>;
  setIsStudioHydrated: StudioStateSetter<boolean>;
  studioDropOrderRef: { current: number };
  folderMapRef: { current: Map<string, string> };
  resultUrlsRef: { current: Set<string> };
};

export function applyRestoredStudioState(
  restored: RestoredStudioState,
  targets: RestoreTargets,
): void {
  const restoredFiles = restored.studioFiles || restored.files.map((file, index) => ({
    id: file.name,
    file,
    sourcePath: file.name,
    addedAt: index,
    dropOrder: index,
  }));
  if (restoredFiles.length > 0) {
    targets.studioDropOrderRef.current = Math.max(
      targets.studioDropOrderRef.current,
      ...restoredFiles.map((entry) => entry.dropOrder),
    );
    targets.setFiles((current) => {
      const merged = new Map(current.map((entry) => [entry.id, entry]));
      restoredFiles.forEach((entry) => merged.set(entry.id, entry));
      return Array.from(merged.values()).sort((a, b) => a.dropOrder - b.dropOrder);
    });
    targets.setFileStatuses((current) => new Map([...restored.fileStatuses, ...current]));
    targets.setSelectedFiles((current) => new Set([...restored.selectedFiles, ...current]));
    targets.setExcludedColorFiles((current) => new Set([...restored.excludedColorFiles, ...current]));
    targets.setAutoDetectedColorFiles((current) => new Set([...restored.autoDetectedColorFiles, ...current]));
    restored.folderMap.forEach((value, key) => targets.folderMapRef.current.set(key, value));
    restored.resultUrls.forEach((url) => targets.resultUrlsRef.current.add(url));
  }
  targets.setIsStudioHydrated(true);
}
