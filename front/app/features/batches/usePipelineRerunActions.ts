import { useCallback, useState, type Dispatch, type SetStateAction } from 'react';
import type {
  FinishedImage,
  PipelineRerunMode,
  TranslationBatch,
  TranslationSettings,
} from '@/types';
import { rerunPipeline, toTranslationBatch } from '@/utils/serverBatches';

interface PipelineRerunState {
  images: FinishedImage[];
  mangaTitle?: string;
}

interface PipelineRerunOptions {
  setTranslationBatches: Dispatch<SetStateAction<TranslationBatch[]>>;
}

export function usePipelineRerunActions({ setTranslationBatches }: PipelineRerunOptions) {
  const [pipelineRerunState, setPipelineRerunState] = useState<PipelineRerunState | null>(null);

  const handleOpenPipelineRerun = useCallback((images: FinishedImage[], mangaTitle?: string) => {
    const eligible = images.filter((image) => image.id);
    if (!eligible.length) {
      window.alert('No pages were selected for pipeline rerun.');
      return;
    }
    setPipelineRerunState({ images: eligible, mangaTitle });
  }, []);

  const handleExecutePipelineRerun = useCallback(async ({
    mode,
    settingsOverrides,
  }: {
    mode: PipelineRerunMode;
    settingsOverrides?: Partial<TranslationSettings>;
  }) => {
    if (!pipelineRerunState) return;
    const pageIds = pipelineRerunState.images.map((image) => image.id);
    const serverBatch = await rerunPipeline({
      pageIds,
      mode,
      settingsOverrides,
    });
    setTranslationBatches((previous) => [
      toTranslationBatch(serverBatch),
      ...previous.filter((batch) => batch.id !== serverBatch.id),
    ]);
  }, [pipelineRerunState, setTranslationBatches]);

  const rerenderImages = useCallback(async (images: FinishedImage[]) => {
    handleOpenPipelineRerun(images);
  }, [handleOpenPipelineRerun]);

  return {
    pipelineRerunState,
    setPipelineRerunState,
    handleOpenPipelineRerun,
    handleExecutePipelineRerun,
    rerenderImages,
  };
}
