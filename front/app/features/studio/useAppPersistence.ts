import { useEffect } from 'react';
import type { FileStatus, StudioFile } from '@/types';
import { saveStudioStateToIDB } from '@/utils/fileStorage';

interface AppPersistenceOptions {
  activeView: string;
  currentMangaTitle: string;
  isStudioHydrated: boolean;
  files: StudioFile[];
  fileStatuses: Map<string, FileStatus>;
  selectedFiles: Set<string>;
  excludedColorFiles: Set<string>;
  autoDetectedColorFiles: Set<string>;
  folderMapRef: { current: Map<string, string> };
  persistSettings: () => void;
}

export function useAppPersistence({
  activeView,
  currentMangaTitle,
  isStudioHydrated,
  files,
  fileStatuses,
  selectedFiles,
  excludedColorFiles,
  autoDetectedColorFiles,
  folderMapRef,
  persistSettings,
}: AppPersistenceOptions) {
  useEffect(() => {
    if (typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.setItem('manga-studio-active-view', activeView);
    }
  }, [activeView]);

  useEffect(() => {
    if (typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.setItem('manga-studio-current-title', currentMangaTitle);
    }
  }, [currentMangaTitle]);

  useEffect(() => {
    if (!isStudioHydrated) return;
    const timeoutId = setTimeout(() => {
      saveStudioStateToIDB(
        files,
        fileStatuses,
        selectedFiles,
        excludedColorFiles,
        autoDetectedColorFiles,
        folderMapRef.current,
      ).catch((error) => console.warn('Failed to save studio state to IDB:', error));
    }, 200);
    return () => clearTimeout(timeoutId);
  }, [isStudioHydrated, files, fileStatuses, selectedFiles, excludedColorFiles, autoDetectedColorFiles]);

  useEffect(() => {
    persistSettings();
  }, [persistSettings]);
}
