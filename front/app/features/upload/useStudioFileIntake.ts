import { useCallback, useState } from "react";
import type { ChangeEvent, Dispatch, DragEvent, SetStateAction } from "react";
import type { PendingStudioFile, StudioFile } from "@/types";
import { imageMimeTypes } from "@/config";
import {
  extractArchiveImages,
  extractMangaTitleFromFilename,
  isArchiveFile,
  naturalCompare,
} from "@/utils/zipUtils";

type StudioFileIntakeOptions = {
  files: StudioFile[];
  setFiles: Dispatch<SetStateAction<StudioFile[]>>;
  setSelectedFiles: Dispatch<SetStateAction<Set<string>>>;
  setCurrentMangaTitle: Dispatch<SetStateAction<string>>;
  checkColorForFiles: (files: StudioFile[]) => Promise<void>;
  studioDropOrderRef: { current: number };
};

export const useStudioFileIntake = ({
  files,
  setFiles,
  setSelectedFiles,
  setCurrentMangaTitle,
  checkColorForFiles,
  studioDropOrderRef,
}: StudioFileIntakeOptions) => {
  const [pendingFiles, setPendingFiles] = useState<PendingStudioFile[]>([]);
  const [isExtractingArchive, setIsExtractingArchive] = useState(false);
  const [archiveError, setArchiveError] = useState<string | null>(null);

  const processIncomingFiles = useCallback(async (incoming: File[]) => {
    const dropOrder = ++studioDropOrderRef.current;
    const addedAt = Date.now();
    const pendingSources: PendingStudioFile[] = [];
    let archiveTitle = "";

    const hasArchives = incoming.some(isArchiveFile);
    if (hasArchives) {
      setIsExtractingArchive(true);
      setArchiveError(null);
    }

    try {
      for (const file of incoming) {
        if (isArchiveFile(file)) {
          try {
            const extracted = await extractArchiveImages(file);
            const sourceId = `source-${Date.now()}-${dropOrder}-${pendingSources.length}-${Math.random().toString(36).slice(2)}`;
            pendingSources.push({
              id: sourceId,
              file,
              pages: extracted.map((page, index) => ({
                id: `studio-${Date.now()}-${dropOrder}-${pendingSources.length}-${index}-${Math.random().toString(36).slice(2)}`,
                file: page,
                sourcePath: page.sourcePath,
                addedAt,
                dropOrder,
                archiveId: sourceId,
                archiveName: file.name,
                archivePageIndex: index,
              })),
            });
            if (!archiveTitle) {
              archiveTitle = extractMangaTitleFromFilename(file.name);
            }
          } catch (err) {
            console.warn(`Failed to extract images from ${file.name}:`, err);
            setArchiveError(
              err instanceof Error
                ? `${file.name}: ${err.message}`
                : `Failed to extract images from ${file.name}`
            );
          }
        } else if (
          imageMimeTypes.includes(file.type) ||
          /\.(png|jpe?g|bmp|webp)$/i.test(file.name)
        ) {
          const sourceId = `source-${Date.now()}-${dropOrder}-loose-${Math.random().toString(36).slice(2)}`;
          pendingSources.push({
            id: sourceId,
            file,
            pages: [{
              id: `studio-${Date.now()}-${dropOrder}-${pendingSources.length}-0-${Math.random().toString(36).slice(2)}`,
              file,
              sourcePath: file.webkitRelativePath || file.name,
              addedAt,
              dropOrder,
              archiveId: `loose-${dropOrder}`,
              archiveName: "Loose images",
              archivePageIndex: pendingSources.length,
            }],
          });
        }
      }
    } finally {
      if (hasArchives) {
        setIsExtractingArchive(false);
      }
    }

    if (pendingSources.length === 0) return;

    if (files.length === 0 && pendingFiles.length === 0) {
      const folderName = incoming[0]?.webkitRelativePath?.split(/[/\\]/)[0]?.trim();
      const fileName = incoming[0]?.name.replace(/\.[^.]+$/, "").trim();
      setCurrentMangaTitle((archiveTitle || folderName || fileName || "").trim());
    }

    pendingSources.sort((a, b) => naturalCompare(a.file.name, b.file.name));
    if (pendingFiles.length > 0 || (hasArchives && pendingSources.length > 1)) {
      setPendingFiles((prev) => [...prev, ...pendingSources]);
      return;
    }

    const startOrder = studioDropOrderRef.current + 1;
    const loadedPages = pendingSources.flatMap((source) => source.pages).map((page, index) => ({
      ...page,
      dropOrder: startOrder + index,
    }));
    studioDropOrderRef.current = startOrder + loadedPages.length - 1;
    setFiles((prev) => [...prev, ...loadedPages].sort((a, b) => a.dropOrder - b.dropOrder));
    setSelectedFiles((prev) => new Set([...prev, ...loadedPages.map((entry) => entry.id)]));
    void checkColorForFiles(loadedPages);
  }, [checkColorForFiles, files.length, pendingFiles.length]);

  const reorderPendingFiles = (sourceId: string, targetId: string) => {
    setPendingFiles((prev) => {
      const sourceIndex = prev.findIndex((entry) => entry.id === sourceId);
      const targetIndex = prev.findIndex((entry) => entry.id === targetId);
      if (sourceIndex < 0 || targetIndex < 0 || sourceIndex === targetIndex) return prev;
      const next = [...prev];
      const [moved] = next.splice(sourceIndex, 1);
      next.splice(targetIndex, 0, moved);
      return next;
    });
  };

  const commitPendingFiles = () => {
    if (pendingFiles.length === 0) return;
    const startOrder = studioDropOrderRef.current + 1;
    const orderedFiles = pendingFiles.flatMap((source) => source.pages).map((entry, index) => ({
      ...entry,
      dropOrder: startOrder + index,
    }));
    studioDropOrderRef.current = startOrder + orderedFiles.length - 1;
    setFiles((prev) => [...prev, ...orderedFiles].sort((a, b) => a.dropOrder - b.dropOrder));
    setSelectedFiles((prev) => new Set([...prev, ...orderedFiles.map((entry) => entry.id)]));
    setPendingFiles([]);
    void checkColorForFiles(orderedFiles);
  };

  const removePendingFile = (fileId: string) => {
    setPendingFiles((prev) => prev.filter((entry) => entry.id !== fileId));
  };

  const clearPendingFiles = () => {
    setPendingFiles([]);
    if (files.length === 0) setCurrentMangaTitle("");
  };

  const resetFileIntake = () => {
    setPendingFiles([]);
    setIsExtractingArchive(false);
    setArchiveError(null);
  };

  const clearArchiveError = () => setArchiveError(null);

  const handleDrop = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    const droppedFiles = Array.from(event.dataTransfer?.files || []);
    void processIncomingFiles(droppedFiles);
  };

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const pickedFiles = Array.from(event.target.files || []);
    event.target.value = "";
    void processIncomingFiles(pickedFiles);
  };

  return {
    pendingFiles,
    isExtractingArchive,
    archiveError,
    processIncomingFiles,
    reorderPendingFiles,
    commitPendingFiles,
    removePendingFile,
    clearPendingFiles,
    resetFileIntake,
    clearArchiveError,
    handleDrop,
    handleFileChange,
  };
};
