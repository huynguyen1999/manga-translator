import assert from 'node:assert/strict';
import type { FinishedImage, TranslationBatch } from '@/types';
import { resolveRerunModeForStage, retryFinishedPage } from './retryFinishedPage';

// 1. Stage to PipelineRerunMode mapping tests
assert.equal(resolveRerunModeForStage(), 'full');
assert.equal(resolveRerunModeForStage(undefined), 'full');
assert.equal(resolveRerunModeForStage('input'), 'full');
assert.equal(resolveRerunModeForStage('upscaling'), 'full');
assert.equal(resolveRerunModeForStage('rendering'), 'typesetting');
assert.equal(resolveRerunModeForStage('layout'), 'typesetting');
assert.equal(resolveRerunModeForStage('finalize'), 'typesetting');
assert.equal(resolveRerunModeForStage('translating'), 'translation_typesetting');
assert.equal(resolveRerunModeForStage('translation'), 'translation_typesetting');
assert.equal(resolveRerunModeForStage('translation_remap'), 'translation_typesetting');
assert.equal(resolveRerunModeForStage('story_analysis'), 'translation_typesetting');
assert.equal(resolveRerunModeForStage('ocr'), 'reprocess_text');
assert.equal(resolveRerunModeForStage('detection'), 'reprocess_text');
assert.equal(resolveRerunModeForStage('bubble_detection'), 'reprocess_text');
assert.equal(resolveRerunModeForStage('textline_merge'), 'reprocess_text');
assert.equal(resolveRerunModeForStage('inpainting'), 'reprocess_text');
assert.equal(resolveRerunModeForStage('mask_generation'), 'reprocess_text');

async function runTests() {
  const sampleImage: FinishedImage = {
    id: 'page-101',
    originalName: '01.png',
    folder: 'folder-101',
    result: '/result/folder-101/final.jpg',
    finishedAt: new Date(),
    settings: {},
  };

  // 2. Matching active batch item calls retryTranslationItem when no currentSettings provided
  let retriedBatchId = '';
  let retriedItemId = '';
  let retriedFromStage: string | undefined;

  const activeBatch: TranslationBatch = {
    id: 'batch-active-1',
    addedAt: new Date(),
    mangaTitle: 'Test Manga',
    status: 'completed',
    settings: {} as any,
    totalItems: 1,
    completedCount: 1,
    items: [
      {
        id: 'item-101',
        file: new File([], '01.png'),
        addedAt: new Date(),
        status: 'finished',
        folder: 'folder-101',
      },
    ],
  };

  let batchState: TranslationBatch[] = [activeBatch];
  const setTranslationBatches = (action: any) => {
    batchState = typeof action === 'function' ? action(batchState) : action;
  };

  await retryFinishedPage({
    image: sampleImage,
    fromStage: 'translating',
    translationBatches: [activeBatch],
    setTranslationBatches,
    retryTranslationItem: async (batchId, itemId, _keep, fromStage) => {
      retriedBatchId = batchId;
      retriedItemId = itemId;
      retriedFromStage = fromStage;
    },
  });

  assert.equal(retriedBatchId, 'batch-active-1');
  assert.equal(retriedItemId, 'item-101');
  assert.equal(retriedFromStage, 'translating');

  // 3. When not matched in translationBatches, falls back to rerunPipeline
  const originalFetch = globalThis.fetch;
  let rerunRequestBody: any = null;
  globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
    if (String(url).includes('/results/rerun')) {
      rerunRequestBody = JSON.parse(String(init?.body));
      return {
        ok: true,
        json: async () => ({
          id: 'batch-rerun-new',
          title: 'Test Manga',
          kind: 'pipeline-rerun',
          status: 'waiting',
          addedAt: new Date().toISOString(),
          totalItems: 1,
          items: [],
        }),
      } as Response;
    }
    throw new Error(`Unexpected fetch url: ${String(url)}`);
  }) as typeof fetch;

  try {
    const galleryImage: FinishedImage = {
      id: 'gallery-page-999',
      originalName: '09.png',
      folder: 'folder-999',
      result: '/result/folder-999/final.jpg',
      finishedAt: new Date(),
      settings: {},
    };

    await retryFinishedPage({
      image: galleryImage,
      fromStage: undefined,
      translationBatches: [],
      setTranslationBatches,
      retryTranslationItem: async () => {},
    });

    assert.deepEqual(rerunRequestBody, {
      pageIds: ['gallery-page-999'],
      mode: 'full',
    });
    assert.equal(batchState.length, 2);
    assert.equal(batchState[0].id, 'batch-rerun-new');

    // 4. Missing identifier throws
    const invalidImage = { ...galleryImage, id: '', folder: undefined };
    await assert.rejects(
      async () => {
        await retryFinishedPage({
          image: invalidImage,
          translationBatches: [],
          setTranslationBatches,
          retryTranslationItem: async () => {},
        });
      },
      /Cannot retry page: missing page identifier/,
    );

    // 5. Passing currentSettings uses rerunPipeline with settingsOverrides
    rerunRequestBody = null;
    await retryFinishedPage({
      image: sampleImage,
      fromStage: 'translating',
      translationBatches: [activeBatch],
      setTranslationBatches,
      retryTranslationItem: async () => {
        throw new Error('should not be called when currentSettings is provided');
      },
      currentSettings: {
        translator: 'gemini',
        targetLanguage: 'ENG',
        renderFont: 'wildwords',
      },
    });

    assert.deepEqual(rerunRequestBody, {
      pageIds: ['page-101'],
      mode: 'translation_typesetting',
      settingsOverrides: {
        translator: 'gemini',
        targetLanguage: 'ENG',
        renderFont: 'wildwords',
      },
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  console.log('retryFinishedPage tests passed successfully!');
}

runTests();
