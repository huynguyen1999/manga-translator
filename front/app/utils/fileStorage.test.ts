import assert from "node:assert/strict";
import type { StudioFile, TranslationBatch } from "@/types";

// In-memory mock of IndexedDB
class MockIDBDatabase {
  stores = new Map<string, Map<any, any>>();
  objectStoreNames = {
    contains: (name: string) => this.stores.has(name),
  };
  version = 4;

  createObjectStore(name: string, { keyPath }: { keyPath: string }) {
    this.stores.set(name, new Map());
    return { name, keyPath };
  }

  transaction(storeNames: string | string[], mode: "readonly" | "readwrite") {
    const name = Array.isArray(storeNames) ? storeNames[0] : storeNames;
    const storeMap = this.stores.get(name)!;

    const tx = {
      oncomplete: null as any,
      onerror: null as any,
      objectStore: (_sName: string) => ({
        clear: () => {
          storeMap.clear();
        },
        put: (record: any) => {
          const key = record.id ?? record.name;
          storeMap.set(key, record);
        },
        delete: (key: any) => {
          storeMap.delete(key);
        },
        getAll: () => {
          const req = {
            result: Array.from(storeMap.values()),
            onsuccess: null as any,
            onerror: null as any,
          };
          setTimeout(() => req.onsuccess?.({ target: req } as any), 0);
          return req;
        },
        getAllKeys: () => {
          const req = {
            result: Array.from(storeMap.keys()),
            onsuccess: null as any,
            onerror: null as any,
          };
          setTimeout(() => req.onsuccess?.({ target: req } as any), 0);
          return req;
        },
        get: (key: any) => {
          const req = {
            result: storeMap.get(key),
            onsuccess: null as any,
            onerror: null as any,
          };
          setTimeout(() => req.onsuccess?.({ target: req } as any), 0);
          return req;
        },
      }),
    };

    setTimeout(() => tx.oncomplete?.({ target: tx } as any), 0);
    return tx;
  }

  close() {}
}

const mockDB = new MockIDBDatabase();

const mockIndexedDB = {
  open: (_name: string, version: number) => {
    const req = {
      result: mockDB,
      onupgradeneeded: null as any,
      onsuccess: null as any,
      onerror: null as any,
    };
    setTimeout(() => {
      req.onupgradeneeded?.({ target: req } as any);
      req.onsuccess?.({ target: req } as any);
    }, 0);
    return req;
  },
};

// Set global window & indexedDB
(globalThis as any).window = {
  indexedDB: mockIndexedDB,
};
(globalThis as any).indexedDB = mockIndexedDB;

async function runTests() {
  const {
    saveStudioStateToIDB,
    loadStudioStateFromIDB,
    clearFilesFromIDB,
    saveQueueToIDB,
    loadQueueFromIDB,
    saveTranslationBatchesToIDB,
    loadTranslationBatchesFromIDB,
    loadTranslationBatchFromIDB,
    saveTranslationBatchToIDB,
    removeTranslationBatchFromIDB,
    clearQueueFromIDB,
    getBlobArrayBuffer,
  } = await import("./fileStorage");

  console.log("Running fileStorage unit tests...");

  // 1. Test getBlobArrayBuffer
  const testBlob = new Blob(["hello manga"], { type: "text/plain" });
  const buf1 = await getBlobArrayBuffer(testBlob);
  const buf2 = await getBlobArrayBuffer(testBlob);
  assert.equal(buf1, buf2, "Buffer should be cached by WeakMap");
  assert.equal(new TextDecoder().decode(buf1), "hello manga");

  // 2. Test saveStudioStateToIDB & loadStudioStateFromIDB
  const file1 = new File(["dummy raw image 1"], "page_01.png", { type: "image/png" });
  const file2 = new File(["dummy raw image 2"], "page_02.png", { type: "image/png" });

  const fileStatuses = new Map();
  fileStatuses.set("page_01.png", {
    status: "finished",
    progress: null,
    queuePos: null,
    result: "/result/folder_1/final.png",
    error: null,
    isAutoColorDetected: false,
  });
  fileStatuses.set("page_02.png", {
    status: "translating",
    progress: "50%",
    queuePos: "1",
    result: null,
    error: null,
    isAutoColorDetected: true,
  });

  const selectedFiles = new Set(["page_01.png", "page_02.png"]);
  const excludedColorFiles = new Set(["page_01.png"]);
  const autoDetectedColorFiles = new Set(["page_02.png"]);
  const folderMap = new Map([["page_01.png", "folder_1"]]);

  await saveStudioStateToIDB(
    [file1, file2],
    fileStatuses,
    selectedFiles,
    excludedColorFiles,
    autoDetectedColorFiles,
    folderMap
  );

  const restoredStudio = await loadStudioStateFromIDB();
  assert.equal(restoredStudio.files.length, 2, "Should restore 2 studio files");
  assert.equal(restoredStudio.files[0].name, "page_01.png");
  assert.equal(restoredStudio.files[1].name, "page_02.png");

  assert.equal(restoredStudio.fileStatuses.get("page_01.png")?.status, "finished");
  assert.equal(restoredStudio.fileStatuses.get("page_01.png")?.result, "/result/folder_1/final.png");
  assert.equal(restoredStudio.fileStatuses.get("page_02.png")?.status, "translating");
  assert.equal(restoredStudio.folderMap.get("page_01.png"), "folder_1");
  assert.ok(restoredStudio.selectedFiles.has("page_01.png"));
  assert.ok(restoredStudio.excludedColorFiles.has("page_01.png"));
  assert.ok(restoredStudio.autoDetectedColorFiles.has("page_02.png"));
  assert.ok(restoredStudio.resultUrls.has("page_01.png"));

  // 3. Test clearFilesFromIDB
  await clearFilesFromIDB();
  const clearedStudio = await loadStudioStateFromIDB();
  assert.equal(clearedStudio.files.length, 0, "Studio files should be cleared");

  const duplicateStudioFiles: StudioFile[] = [
    { id: "studio-a", file: new File(["a"], "page.png", { type: "image/png" }), sourcePath: "A/page.png", addedAt: 10, dropOrder: 0, archiveId: "archive-a", archiveName: "a.cbz", archivePageIndex: 0 },
    { id: "studio-b", file: new File(["b"], "page.png", { type: "image/png" }), sourcePath: "B/page.png", addedAt: 11, dropOrder: 1, archiveId: "archive-a", archiveName: "a.cbz", archivePageIndex: 1 },
  ];
  await saveStudioStateToIDB(
    duplicateStudioFiles,
    new Map(),
    new Set(["studio-b"]),
    new Set(),
    new Set(),
    new Map(),
  );
  const restoredDuplicates = await loadStudioStateFromIDB();
  assert.deepEqual(restoredDuplicates.studioFiles.map((entry) => entry.id), ["studio-a", "studio-b"]);
  assert.deepEqual(restoredDuplicates.studioFiles.map((entry) => entry.sourcePath), ["A/page.png", "B/page.png"]);
  assert.deepEqual(restoredDuplicates.studioFiles.map((entry) => entry.archivePageIndex), [0, 1]);
  assert.ok(restoredDuplicates.selectedFiles.has("studio-b"));
  assert.equal(restoredDuplicates.files[0].name, "page.png");
  assert.equal(restoredDuplicates.files[1].name, "page.png");

  // 4. Test saveQueueToIDB & loadQueueFromIDB
  const queueItem1 = {
    id: "q-1",
    file: file1,
    addedAt: new Date(1700000000000),
    status: "finished" as const,
    mangaTitle: "Naruto",
    step: undefined,
    result: "/result/folder_naruto/final.png",
    folder: "folder_naruto",
    excludeColor: false,
    isAutoColorDetected: false,
  };

  const queueItem2 = {
    id: "q-2",
    file: file2,
    addedAt: new Date(1700000005000),
    status: "processing" as const,
    mangaTitle: "Naruto",
    step: "inpainting",
    result: undefined,
    folder: undefined,
    excludeColor: true,
    isAutoColorDetected: true,
  };

  await saveQueueToIDB([queueItem1, queueItem2]);

  const restoredQueue = await loadQueueFromIDB();
  assert.equal(restoredQueue.length, 2, "Should restore 2 queued images");
  assert.equal(restoredQueue[0].id, "q-1");
  assert.equal(restoredQueue[0].file.name, "page_01.png");
  assert.equal(restoredQueue[0].status, "finished");
  assert.equal(restoredQueue[0].result, "/result/folder_naruto/final.png");
  assert.equal(restoredQueue[0].folder, "folder_naruto");
  assert.equal(restoredQueue[0].mangaTitle, "Naruto");

  assert.equal(restoredQueue[1].id, "q-2");
  assert.equal(restoredQueue[1].file.name, "page_02.png");
  assert.equal(restoredQueue[1].status, "processing");
  assert.equal(restoredQueue[1].step, "inpainting");
  assert.equal(restoredQueue[1].excludeColor, true);
  assert.equal(restoredQueue[1].isAutoColorDetected, true);

  // 5. Ordered translation batch persistence
  const batch: TranslationBatch = {
    id: "batch-1",
    addedAt: new Date(1700000010000),
    mangaTitle: "Naruto",
    settings: {} as TranslationBatch["settings"],
    totalItems: 2,
    completedCount: 1,
    status: "processing",
    items: [queueItem2],
  };
  await saveTranslationBatchesToIDB([batch]);
  const restoredBatches = await loadTranslationBatchesFromIDB();
  assert.equal(restoredBatches.length, 1, "Should restore one translation batch");
  assert.equal(restoredBatches[0].id, "batch-1");
  assert.equal(restoredBatches[0].mangaTitle, "Naruto");
  assert.equal(restoredBatches[0].totalItems, 2);
  assert.equal(restoredBatches[0].items[0].status, "processing");

  const uploadBatch = { ...batch, id: "upload-1", status: "uploading" as const };
  await saveTranslationBatchToIDB(uploadBatch);
  const uploadSummary = (await loadTranslationBatchesFromIDB()).find((entry) => entry.id === "upload-1");
  assert.equal(uploadSummary?.status, "uploading");
  assert.equal(uploadSummary?.items.length, 0, "Sidebar load should not restore upload pages");
  assert.equal((await loadTranslationBatchFromIDB("upload-1"))?.items[0].file.size, file2.size);
  await removeTranslationBatchFromIDB("upload-1");
  assert.equal((await loadTranslationBatchesFromIDB()).some((entry) => entry.id === "upload-1"), false);

  // 6. Test clearQueueFromIDB
  await clearQueueFromIDB();
  const clearedQueue = await loadQueueFromIDB();
  assert.equal(clearedQueue.length, 0, "Queue should be cleared");

  console.log("All fileStorage tests passed successfully!");
}

runTests().catch((err) => {
  console.error("Test failed:", err);
  process.exit(1);
});
