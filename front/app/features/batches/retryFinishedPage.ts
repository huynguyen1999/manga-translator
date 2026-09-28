import type { Dispatch, SetStateAction } from 'react';
import type { FinishedImage, PipelineRerunMode, TranslationBatch, TranslationSettings } from '@/types';
import { rerunPipeline, toTranslationBatch } from '@/utils/serverBatches';

export const resolveRerunModeForStage = (stageId?: string): PipelineRerunMode => {
  if (!stageId) return 'full';
  const normalized = stageId.toLowerCase().trim();
  if (['rendering', 'layout', 'finalize'].includes(normalized)) {
    return 'typesetting';
  }
  if (
    [
      'translating',
      'translation',
      'translation_remap',
      'story_analysis',
      'analyzing-story',
    ].includes(normalized)
  ) {
    return 'translation_typesetting';
  }
  if (
    [
      'ocr',
      'detection',
      'bubble_detection',
      'textline_merge',
      'text_grouping',
      'mask_generation',
      'mask-generation',
      'inpainting',
    ].includes(normalized)
  ) {
    return 'reprocess_text';
  }
  return 'full';
};

export interface RetryFinishedPageOptions {
  image: FinishedImage;
  fromStage?: string;
  translationBatches: TranslationBatch[];
  setTranslationBatches: Dispatch<SetStateAction<TranslationBatch[]>>;
  retryTranslationItem?: (
    batchId: string,
    itemId: string,
    keepFailedPagesForEditing?: boolean,
    fromStage?: string,
  ) => Promise<void>;
  currentSettings?: Partial<TranslationSettings>;
}

export async function retryFinishedPage({
  image,
  fromStage,
  translationBatches,
  setTranslationBatches,
  retryTranslationItem,
  currentSettings,
}: RetryFinishedPageOptions): Promise<void> {
  const match = (!currentSettings && image.folder)
    ? translationBatches
        .flatMap((batch) => batch.items.map((item) => ({ batch, item })))
        .find(
          ({ item }) =>
            item.folder === image.folder ||
            (item.pageId && item.pageId === image.id),
        )
    : undefined;

  if (match && retryTranslationItem) {
    try {
      await retryTranslationItem(match.batch.id, match.item.id, false, fromStage);
      return;
    } catch (error) {
      console.warn('Retrying through existing batch item failed, falling back to pipeline rerun:', error);
    }
  }

  const pageId = image.id || image.folder;
  if (!pageId) {
    throw new Error('Cannot retry page: missing page identifier');
  }

  const mode = resolveRerunModeForStage(fromStage);
  const serverBatch = await rerunPipeline({
    pageIds: [pageId],
    mode,
    settingsOverrides: currentSettings,
  });

  setTranslationBatches((previous) => [
    toTranslationBatch(serverBatch),
    ...previous.filter((batch) => batch.id !== serverBatch.id),
  ]);
}
