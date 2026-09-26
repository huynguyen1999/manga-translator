import type { Dispatch, SetStateAction } from "react";
import type {
  FileStatus,
  MangaGroupSelection,
  QueuedImage,
  StoryPlan,
  StudioFile,
  TranslationBatch,
  TranslationSettings,
} from "@/types";

type TranslationSubmissionOptions = {
  files: StudioFile[];
  selectedFiles: Set<string>;
  pendingTranslationTargets: StudioFile[];
  excludedColorFiles: Set<string>;
  autoDetectedColorFiles: Set<string>;
  isConfirmingTranslationRef: { current: boolean };
  setFiles: Dispatch<SetStateAction<StudioFile[]>>;
  setSelectedFiles: Dispatch<SetStateAction<Set<string>>>;
  setPendingTranslationTargets: Dispatch<SetStateAction<StudioFile[]>>;
  setIsGroupModalOpen: Dispatch<SetStateAction<boolean>>;
  setTranslationBatchError: Dispatch<SetStateAction<string | null>>;
  setExcludedColorFiles: Dispatch<SetStateAction<Set<string>>>;
  setAutoDetectedColorFiles: Dispatch<SetStateAction<Set<string>>>;
  setFileStatuses: Dispatch<SetStateAction<Map<string, FileStatus>>>;
  setCurrentMangaTitle: Dispatch<SetStateAction<string>>;
  setTranslationBatches: Dispatch<SetStateAction<TranslationBatch[]>>;
  setIsJobsOpen: Dispatch<SetStateAction<boolean>>;
  getCurrentSettings: () => TranslationSettings;
  persistBatch: (batch: TranslationBatch) => Promise<void>;
  resumeUpload: (batch: TranslationBatch) => Promise<void>;
  failUpload: (batch: TranslationBatch, error: unknown) => Promise<void>;
};

export function createTranslationSubmissionActions({
  files,
  selectedFiles,
  pendingTranslationTargets,
  excludedColorFiles,
  autoDetectedColorFiles,
  isConfirmingTranslationRef,
  setFiles,
  setSelectedFiles,
  setPendingTranslationTargets,
  setIsGroupModalOpen,
  setTranslationBatchError,
  setExcludedColorFiles,
  setAutoDetectedColorFiles,
  setFileStatuses,
  setCurrentMangaTitle,
  setTranslationBatches,
  setIsJobsOpen,
  getCurrentSettings,
  persistBatch,
  resumeUpload,
  failUpload,
}: TranslationSubmissionOptions) {
  const createStudioTranslationBatch = (
    filesToUpload: StudioFile[],
    targetMangaTitle: string,
    settings: TranslationSettings,
    targetGroupId?: string | null,
    isNewGroup?: boolean,
  ): TranslationBatch => {
    const effectiveMangaTitle = targetMangaTitle.trim() || "Ungrouped";
    const batchId = `batch-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const addedAt = new Date();
    const items: QueuedImage[] = filesToUpload.map((entry, index) => ({
      id: `item-${addedAt.getTime()}-${index}-${Math.random().toString(36).slice(2)}`,
      mangaGroupId: targetGroupId || null,
      file: entry.file,
      addedAt,
      status: "queued" as const,
      mangaTitle: effectiveMangaTitle,
      sourcePath: entry.sourcePath,
      excludeColor: excludedColorFiles.has(entry.id),
      isAutoColorDetected: autoDetectedColorFiles.has(entry.id),
    }));
    return {
      id: batchId,
      kind: "translation",
      addedAt,
      mangaTitle: effectiveMangaTitle,
      mangaGroupId: targetGroupId || null,
      isNewGroup,
      settings,
      items,
      totalItems: items.length,
      completedCount: 0,
      uploadProgress: 0,
      status: "uploading",
    };
  };

  const handleSubmit = () => {
    const filesToTranslate = files.filter((entry) => selectedFiles.has(entry.id));
    if (filesToTranslate.length === 0) return;

    setTranslationBatchError(null);
    setPendingTranslationTargets(filesToTranslate);
    setIsGroupModalOpen(true);
  };

  const handleConfirmGroup = async (selectedGroup: MangaGroupSelection | string, storyPlan?: StoryPlan) => {
    if (isConfirmingTranslationRef.current) return;
    const rawTitle = typeof selectedGroup === "string" ? selectedGroup : selectedGroup.title;
    const cleanGroup = rawTitle.trim() || "Ungrouped";
    const groupId = typeof selectedGroup === "object" ? selectedGroup.groupId : undefined;
    const isNewGroup = typeof selectedGroup === "object" ? selectedGroup.isNewGroup : undefined;
    const targets = pendingTranslationTargets;
    if (targets.length === 0) return;

    isConfirmingTranslationRef.current = true;
    setTranslationBatchError(null);
    const batchSettings = {
      ...getCurrentSettings(),
      ...(storyPlan ? { storyPlan } : {}),
    };
    setCurrentMangaTitle(cleanGroup);
    const targetIds = new Set(targets.map((entry) => entry.id));
    setIsGroupModalOpen(false);
    setPendingTranslationTargets([]);

    // Hand off submitted pages immediately from Studio to the translation batch queue
    setFiles((prev) => prev.filter((entry) => !targetIds.has(entry.id)));
    setSelectedFiles((prev) => new Set([...prev].filter((id) => !targetIds.has(id))));
    setExcludedColorFiles((prev) => new Set([...prev].filter((id) => !targetIds.has(id))));
    setAutoDetectedColorFiles((prev) => new Set([...prev].filter((id) => !targetIds.has(id))));
    setFileStatuses((prev) => new Map([...prev].filter(([id]) => !targetIds.has(id))));

    const uploadBatch = createStudioTranslationBatch(targets, cleanGroup, batchSettings, groupId, isNewGroup);
    setTranslationBatches((prev) => [uploadBatch, ...prev]);
    setIsJobsOpen(true);
    isConfirmingTranslationRef.current = false;

    try {
      await persistBatch(uploadBatch);
      await resumeUpload(uploadBatch);
    } catch (error) {
      await failUpload(uploadBatch, error);
    }
  };

  const handleCloseGroupModal = () => {
    setIsGroupModalOpen(false);
    setPendingTranslationTargets([]);
    setTranslationBatchError(null);
  };

  return { handleSubmit, handleConfirmGroup, handleCloseGroupModal };
}
