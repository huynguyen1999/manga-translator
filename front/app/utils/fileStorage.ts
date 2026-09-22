/**
 * IndexedDB-backed persistence for Manga Translation Studio.
 * Stores uploaded File objects, processing statuses, results, and queue items
 * so they survive page refreshes.
 */

import type {
  FileStatus,
  QueuedImage,
  StatusKey,
  TranslationBatch,
  TranslationBatchKind,
  TranslationBatchStatus,
  TranslationSettings,
  StudioFile,
} from "@/types";

const DB_NAME = "manga-studio-files";
const LEGACY_FILES_STORE_NAME = "files";
const FILES_STORE_NAME = "studio-files";
const QUEUE_STORE_NAME = "queue";
const QUEUE_METADATA_STORE_NAME = "queue-metadata";
const DB_VERSION = 4;

// In-memory ArrayBuffer cache to eliminate disk re-reads when updating status/step
const bufferCache = new WeakMap<Blob, ArrayBuffer>();

export async function getBlobArrayBuffer(blob: Blob): Promise<ArrayBuffer> {
  const cached = bufferCache.get(blob);
  if (cached) return cached;
  const buf = await blob.arrayBuffer();
  bufferCache.set(blob, buf);
  return buf;
}

function openDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof window === "undefined" || !("indexedDB" in window)) {
      return reject(new Error("IndexedDB is not available in this environment"));
    }
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = (e) => {
      const db = (e.target as IDBOpenDBRequest).result;
      const transaction = (e.target as IDBOpenDBRequest).transaction;
      if (!db.objectStoreNames.contains(FILES_STORE_NAME)) {
        const store = db.createObjectStore(FILES_STORE_NAME, { keyPath: "id" });
        if (db.objectStoreNames.contains(LEGACY_FILES_STORE_NAME) && transaction) {
          const legacy = transaction.objectStore(LEGACY_FILES_STORE_NAME);
          const request = legacy.getAll();
          request.onsuccess = () => {
            const records = (request.result || []) as StoredStudioFile[];
            records
              .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" }))
              .forEach((record, index) => store.put({
                ...record,
                id: record.id || `legacy-${index}-${record.name}`,
                sourcePath: record.sourcePath || record.name,
                addedAt: record.addedAt || index,
                dropOrder: record.dropOrder || index,
              }));
          };
        }
      }
      if (!db.objectStoreNames.contains(QUEUE_STORE_NAME)) {
        db.createObjectStore(QUEUE_STORE_NAME, { keyPath: "id" });
      }
      if (!db.objectStoreNames.contains(QUEUE_METADATA_STORE_NAME)) {
        db.createObjectStore(QUEUE_METADATA_STORE_NAME, { keyPath: "id" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export interface StoredStudioFile {
  id: string;
  name: string;
  sourcePath: string;
  addedAt: number;
  dropOrder: number;
  archiveId?: string;
  archiveName?: string;
  archivePageIndex?: number;
  type: string;
  lastModified: number;
  data: ArrayBuffer;
  status?: StatusKey | null;
  progress?: string | null;
  queuePos?: string | null;
  offlineModel?: string;
  resultUrl?: string | null;
  resultBlobData?: ArrayBuffer | null;
  resultBlobType?: string | null;
  folder?: string | null;
  error?: string | null;
  isSelected?: boolean;
  isExcludedColor?: boolean;
  isAutoColorDetected?: boolean;
}

export interface RestoredStudioState {
  files: File[];
  studioFiles: StudioFile[];
  fileStatuses: Map<string, FileStatus>;
  selectedFiles: Set<string>;
  excludedColorFiles: Set<string>;
  autoDetectedColorFiles: Set<string>;
  folderMap: Map<string, string>;
  resultUrls: Set<string>;
}

export interface StoredQueuedImage {
  id: string;
  mangaGroupId?: string | null;
  pageId?: string | null;
  pageOrder?: number | null;
  sourcePath?: string | null;
  name: string;
  type: string;
  lastModified: number;
  data: ArrayBuffer;
  addedAt: number;
  status: 'queued' | 'processing' | 'finished' | 'error';
  mangaTitle?: string;
  step?: string;
  offlineModel?: string;
  resultUrl?: string | null;
  resultBlobData?: ArrayBuffer | null;
  resultBlobType?: string | null;
  folder?: string | null;
  error?: string | null;
  excludeColor?: boolean;
  isAutoColorDetected?: boolean;
}

export interface StoredTranslationBatch {
  id: string;
  kind?: TranslationBatchKind;
  addedAt: number;
  mangaTitle: string;
  mangaGroupId?: string | null;
  isNewGroup?: boolean;
  settings: TranslationSettings;
  totalItems: number;
  completedCount: number;
  status: TranslationBatchStatus;
  priority?: boolean;
  dismissed?: boolean;
  items: StoredQueuedImage[];
}

type StoredTranslationBatchMetadata = Omit<StoredTranslationBatch, "items"> & { items: [] };

/** Persist Studio files along with their processing statuses and configuration */
export async function saveStudioStateToIDB(
  files: Array<File | StudioFile>,
  fileStatuses: Map<string, FileStatus>,
  selectedFiles: Set<string>,
  excludedColorFiles: Set<string>,
  autoDetectedColorFiles: Set<string>,
  folderMap: Map<string, string>
): Promise<void> {
  if (typeof window === "undefined" || !("indexedDB" in window)) return;
  if (files.length === 0) {
    return clearFilesFromIDB();
  }

  const records: StoredStudioFile[] = await Promise.all(
    files.map(async (entry, index) => {
      const studioFile: StudioFile = "file" in entry
        ? entry
        : {
            id: entry.name,
            file: entry,
            sourcePath: entry.name,
            addedAt: index,
            dropOrder: index,
          };
      const file = studioFile.file;
      const data = await getBlobArrayBuffer(file);
      const statusObj = fileStatuses.get(studioFile.id) || fileStatuses.get(file.name);
      let resultUrl: string | null = null;
      let resultBlobData: ArrayBuffer | null = null;
      let resultBlobType: string | null = null;

      if (typeof statusObj?.result === "string") {
        resultUrl = statusObj.result;
      } else if (statusObj?.result instanceof Blob) {
        resultBlobData = await getBlobArrayBuffer(statusObj.result);
        resultBlobType = statusObj.result.type || "image/png";
      }

      const folder = folderMap.get(studioFile.id) || folderMap.get(file.name) || undefined;

      return {
        id: studioFile.id,
        name: file.name,
        sourcePath: studioFile.sourcePath,
        addedAt: studioFile.addedAt,
        dropOrder: studioFile.dropOrder,
        archiveId: studioFile.archiveId,
        archiveName: studioFile.archiveName,
        archivePageIndex: studioFile.archivePageIndex,
        type: file.type,
        lastModified: file.lastModified,
        data,
        status: statusObj?.status || null,
        progress: statusObj?.progress || null,
        queuePos: statusObj?.queuePos || null,
        offlineModel: statusObj?.offlineModel,
        resultUrl,
        resultBlobData,
        resultBlobType,
        folder,
        error: statusObj?.error || null,
        isSelected: selectedFiles.has(studioFile.id) || selectedFiles.has(file.name),
        isExcludedColor: excludedColorFiles.has(studioFile.id) || excludedColorFiles.has(file.name),
        isAutoColorDetected: autoDetectedColorFiles.has(studioFile.id) || autoDetectedColorFiles.has(file.name),
      };
    })
  );

  const db = await openDB();
  const tx = db.transaction(FILES_STORE_NAME, "readwrite");
  const store = tx.objectStore(FILES_STORE_NAME);
  store.clear();
  for (const record of records) {
    store.put(record);
  }
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); reject(tx.error); };
  });
}

/** Load previously persisted Studio files and statuses from IndexedDB */
export async function loadStudioStateFromIDB(): Promise<RestoredStudioState> {
  const emptyState: RestoredStudioState = {
    files: [],
    studioFiles: [],
    fileStatuses: new Map(),
    selectedFiles: new Set(),
    excludedColorFiles: new Set(),
    autoDetectedColorFiles: new Set(),
    folderMap: new Map(),
    resultUrls: new Set(),
  };

  if (typeof window === "undefined" || !("indexedDB" in window)) {
    return emptyState;
  }

  const db = await openDB();
  if (!db.objectStoreNames.contains(FILES_STORE_NAME)) {
    db.close();
    return emptyState;
  }

  const tx = db.transaction(FILES_STORE_NAME, "readonly");
  const store = tx.objectStore(FILES_STORE_NAME);
  return new Promise((resolve, reject) => {
    const request = store.getAll();
    request.onsuccess = () => {
      db.close();
      const records: StoredStudioFile[] = request.result || [];
      const files: File[] = [];
      const studioFiles: StudioFile[] = [];
      const fileStatuses = new Map<string, FileStatus>();
      const selectedFiles = new Set<string>();
      const excludedColorFiles = new Set<string>();
      const autoDetectedColorFiles = new Set<string>();
      const folderMap = new Map<string, string>();
      const resultUrls = new Set<string>();

      records.sort((a, b) => (a.dropOrder ?? 0) - (b.dropOrder ?? 0));
      for (const [index, r] of records.entries()) {
        const id = r.id || `legacy-${index}-${r.name}`;
        const file = new File([r.data], r.name, {
          type: r.type,
          lastModified: r.lastModified,
        });
        bufferCache.set(file, r.data);
        files.push(file);
        studioFiles.push({
          id,
          file,
          sourcePath: r.sourcePath || r.name,
          addedAt: r.addedAt ?? index,
          dropOrder: r.dropOrder ?? index,
          archiveId: r.archiveId,
          archiveName: r.archiveName,
          archivePageIndex: r.archivePageIndex,
        });

        let result: Blob | string | null = null;
        if (r.resultUrl) {
          result = r.resultUrl;
          resultUrls.add(id);
        } else if (r.resultBlobData) {
          result = new Blob([r.resultBlobData], {
            type: r.resultBlobType || "image/png",
          });
          bufferCache.set(result, r.resultBlobData);
        }

        if (r.folder) {
          folderMap.set(id, r.folder);
        }

        if (r.isSelected !== false) {
          selectedFiles.add(id);
        }
        if (r.isExcludedColor) {
          excludedColorFiles.add(id);
        }
        if (r.isAutoColorDetected) {
          autoDetectedColorFiles.add(id);
        }

        fileStatuses.set(id, {
          status: r.status || null,
          progress: r.progress || null,
          queuePos: r.queuePos || null,
          offlineModel: r.offlineModel,
          result,
          error: r.error || null,
          isAutoColorDetected: r.isAutoColorDetected,
        });
      }

      resolve({
        files,
        studioFiles,
        fileStatuses,
        selectedFiles,
        excludedColorFiles,
        autoDetectedColorFiles,
        folderMap,
        resultUrls,
      });
    };
    request.onerror = () => {
      db.close();
      reject(request.error);
    };
  });
}

/** Persist queue items and their processing state to IndexedDB */
export async function saveQueueToIDB(queue: QueuedImage[]): Promise<void> {
  if (typeof window === "undefined" || !("indexedDB" in window)) return;
  if (queue.length === 0) {
    return clearQueueFromIDB();
  }

  const records: StoredQueuedImage[] = await Promise.all(
    queue.map(async (item) => {
      const data = await getBlobArrayBuffer(item.file);
      let resultUrl: string | null = null;
      let resultBlobData: ArrayBuffer | null = null;
      let resultBlobType: string | null = null;

      if (typeof item.result === "string") {
        resultUrl = item.result;
      } else if (item.result instanceof Blob) {
        resultBlobData = await getBlobArrayBuffer(item.result);
        resultBlobType = item.result.type || "image/png";
      }

      const addedAtTime =
        item.addedAt instanceof Date
          ? item.addedAt.getTime()
          : typeof item.addedAt === "number"
          ? item.addedAt
          : Date.now();

      return {
        id: item.id,
        mangaGroupId: item.mangaGroupId,
        pageId: item.pageId,
        pageOrder: item.pageOrder,
        sourcePath: item.sourcePath,
        name: item.file.name,
        type: item.file.type,
        lastModified: item.file.lastModified,
        data,
        addedAt: addedAtTime,
        status: item.status,
        mangaTitle: item.mangaTitle,
        step: item.step,
        offlineModel: item.offlineModel,
        resultUrl,
        resultBlobData,
        resultBlobType,
        folder: item.folder,
        error: item.error,
        excludeColor: item.excludeColor,
        isAutoColorDetected: item.isAutoColorDetected,
      };
    })
  );

  const db = await openDB();
  const tx = db.transaction(QUEUE_STORE_NAME, "readwrite");
  const store = tx.objectStore(QUEUE_STORE_NAME);
  store.clear();
  for (const record of records) {
    store.put(record);
  }
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); reject(tx.error); };
  });
}

async function serializeQueuedImage(item: QueuedImage): Promise<StoredQueuedImage> {
  const data = await getBlobArrayBuffer(item.file);
  let resultUrl: string | null = null;
  let resultBlobData: ArrayBuffer | null = null;
  let resultBlobType: string | null = null;

  if (typeof item.result === "string") {
    resultUrl = item.result;
  } else if (item.result instanceof Blob) {
    resultBlobData = await getBlobArrayBuffer(item.result);
    resultBlobType = item.result.type || "image/png";
  }

  const addedAtTime =
    item.addedAt instanceof Date
      ? item.addedAt.getTime()
      : typeof item.addedAt === "number"
      ? item.addedAt
      : Date.now();

  return {
    id: item.id,
    mangaGroupId: item.mangaGroupId,
    pageId: item.pageId,
    pageOrder: item.pageOrder,
    sourcePath: item.sourcePath,
    name: item.file.name,
    type: item.file.type,
    lastModified: item.file.lastModified,
    data,
    addedAt: addedAtTime,
    status: item.status,
    mangaTitle: item.mangaTitle,
    step: item.step,
    offlineModel: item.offlineModel,
    resultUrl,
    resultBlobData,
    resultBlobType,
    folder: item.folder,
    error: item.error,
    excludeColor: item.excludeColor,
    isAutoColorDetected: item.isAutoColorDetected,
  };
}

function deserializeQueuedImage(r: StoredQueuedImage): QueuedImage {
  const file = new File([r.data], r.name, {
    type: r.type,
    lastModified: r.lastModified,
  });
  bufferCache.set(file, r.data);

  let result: Blob | string | undefined;
  if (r.resultUrl) {
    result = r.resultUrl;
  } else if (r.resultBlobData) {
    result = new Blob([r.resultBlobData], {
      type: r.resultBlobType || "image/png",
    });
    bufferCache.set(result, r.resultBlobData);
  }

  return {
    id: r.id,
    mangaGroupId: r.mangaGroupId,
    pageId: r.pageId,
    pageOrder: r.pageOrder,
    sourcePath: r.sourcePath,
    file,
    addedAt: new Date(r.addedAt || Date.now()),
    status: r.status,
    mangaTitle: r.mangaTitle,
    step: r.step,
    offlineModel: r.offlineModel,
    result,
    folder: r.folder || undefined,
    error: r.error || undefined,
    excludeColor: r.excludeColor,
    isAutoColorDetected: r.isAutoColorDetected,
  };
}

function toBatchMetadata(record: StoredTranslationBatch): StoredTranslationBatchMetadata {
  return {
    ...record,
    items: [],
  };
}

function deserializeTranslationBatch(
  record: StoredTranslationBatch | StoredTranslationBatchMetadata,
  metadataOnly = false,
): TranslationBatch {
  return {
    id: record.id,
    kind: record.kind,
    addedAt: new Date(record.addedAt || Date.now()),
    mangaTitle: record.mangaTitle || "Ungrouped",
    mangaGroupId: record.mangaGroupId || null,
    isNewGroup: record.isNewGroup,
    settings: record.settings,
    totalItems: record.totalItems || record.items.length,
    completedCount: record.completedCount || 0,
    status: record.status || "waiting",
    priority: record.priority,
    dismissed: record.dismissed,
    items: metadataOnly ? [] : record.items.map((item) => deserializeQueuedImage(item as StoredQueuedImage)),
  };
}

function readStoreValues<T>(storeName: string): Promise<T[]> {
  return openDB().then((db) => {
    if (!db.objectStoreNames.contains(storeName)) {
      db.close();
      return [];
    }
    const request = db.transaction(storeName, "readonly").objectStore(storeName).getAll();
    return new Promise<T[]>((resolve, reject) => {
      request.onsuccess = () => {
        db.close();
        resolve((request.result || []) as T[]);
      };
      request.onerror = () => {
        db.close();
        reject(request.error);
      };
    });
  });
}

function readStoreKeys(storeName: string): Promise<IDBValidKey[]> {
  return openDB().then((db) => {
    if (!db.objectStoreNames.contains(storeName)) {
      db.close();
      return [];
    }
    const request = db.transaction(storeName, "readonly").objectStore(storeName).getAllKeys();
    return new Promise<IDBValidKey[]>((resolve, reject) => {
      request.onsuccess = () => {
        db.close();
        resolve(request.result || []);
      };
      request.onerror = () => {
        db.close();
        reject(request.error);
      };
    });
  });
}

function readStoreValue<T>(storeName: string, key: IDBValidKey): Promise<T | undefined> {
  return openDB().then((db) => {
    if (!db.objectStoreNames.contains(storeName)) {
      db.close();
      return undefined;
    }
    const request = db.transaction(storeName, "readonly").objectStore(storeName).get(key);
    return new Promise<T | undefined>((resolve, reject) => {
      request.onsuccess = () => {
        db.close();
        resolve(request.result as T | undefined);
      };
      request.onerror = () => {
        db.close();
        reject(request.error);
      };
    });
  });
}

async function saveBatchMetadata(records: StoredTranslationBatch[]): Promise<void> {
  const db = await openDB();
  const tx = db.transaction(QUEUE_METADATA_STORE_NAME, "readwrite");
  const store = tx.objectStore(QUEUE_METADATA_STORE_NAME);
  records.forEach((record) => store.put(toBatchMetadata(record)));
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); reject(tx.error); };
  });
}

/** Persist ordered translation batches and their resumable page state. */
export async function saveTranslationBatchesToIDB(
  batches: TranslationBatch[]
): Promise<void> {
  if (typeof window === "undefined" || !("indexedDB" in window)) return;
  if (batches.length === 0) {
    return clearQueueFromIDB();
  }

  const records: StoredTranslationBatch[] = await Promise.all(
    batches.map(async (batch) => ({
      id: batch.id,
      kind: batch.kind,
      addedAt:
        batch.addedAt instanceof Date ? batch.addedAt.getTime() : Date.now(),
      mangaTitle: batch.mangaTitle,
      mangaGroupId: batch.mangaGroupId || null,
      isNewGroup: batch.isNewGroup,
      settings: batch.settings,
      totalItems: batch.totalItems,
      completedCount: batch.completedCount,
      status: batch.status,
      priority: batch.priority,
      dismissed: batch.dismissed,
      items: await Promise.all(batch.items.map(serializeQueuedImage)),
    }))
  );

  const db = await openDB();
  const tx = db.transaction(QUEUE_STORE_NAME, "readwrite");
  const store = tx.objectStore(QUEUE_STORE_NAME);
  store.clear();
  records.forEach((record) => store.put(record));
  await new Promise<void>((resolve, reject) => {
    tx.oncomplete = () => {
      db.close();
      resolve();
    };
    tx.onerror = () => {
      db.close();
      reject(tx.error);
    };
  });
  const metadataDb = await openDB();
  const metadataTx = metadataDb.transaction(QUEUE_METADATA_STORE_NAME, "readwrite");
  const metadataStore = metadataTx.objectStore(QUEUE_METADATA_STORE_NAME);
  metadataStore.clear();
  records
    .filter((record) => record.status === "uploading")
    .forEach((record) => metadataStore.put(toBatchMetadata(record)));
  return new Promise((resolve, reject) => {
    metadataTx.oncomplete = () => { metadataDb.close(); resolve(); };
    metadataTx.onerror = () => { metadataDb.close(); reject(metadataTx.error); };
  });
}

/** Persist one in-flight upload without replacing other legacy queue records. */
export async function saveTranslationBatchToIDB(batch: TranslationBatch): Promise<void> {
  if (typeof window === "undefined" || !("indexedDB" in window)) return;
  const record: StoredTranslationBatch = {
    id: batch.id,
    kind: batch.kind,
    addedAt: batch.addedAt.getTime(),
    mangaTitle: batch.mangaTitle,
    mangaGroupId: batch.mangaGroupId || null,
    isNewGroup: batch.isNewGroup,
    settings: batch.settings,
    totalItems: batch.totalItems,
    completedCount: batch.completedCount,
    status: batch.status,
    priority: batch.priority,
    dismissed: batch.dismissed,
    items: await Promise.all(batch.items.map(serializeQueuedImage)),
  };
  const db = await openDB();
  const tx = db.transaction(QUEUE_STORE_NAME, "readwrite");
  tx.objectStore(QUEUE_STORE_NAME).put(record);
  await new Promise<void>((resolve, reject) => {
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); reject(tx.error); };
  });
  await saveBatchMetadata([record]);
}

export async function removeTranslationBatchFromIDB(batchId: string): Promise<void> {
  if (typeof window === "undefined" || !("indexedDB" in window)) return;
  const db = await openDB();
  const tx = db.transaction(QUEUE_STORE_NAME, "readwrite");
  tx.objectStore(QUEUE_STORE_NAME).delete(batchId);
  await new Promise<void>((resolve, reject) => {
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); reject(tx.error); };
  });
  const metadataDb = await openDB();
  const metadataTx = metadataDb.transaction(QUEUE_METADATA_STORE_NAME, "readwrite");
  metadataTx.objectStore(QUEUE_METADATA_STORE_NAME).delete(batchId);
  return new Promise((resolve, reject) => {
    metadataTx.oncomplete = () => { metadataDb.close(); resolve(); };
    metadataTx.onerror = () => { metadataDb.close(); reject(metadataTx.error); };
  });
}

/** Load ordered translation batches; legacy flat queue records are ignored here. */
export async function loadTranslationBatchesFromIDB(): Promise<TranslationBatch[]> {
  if (typeof window === "undefined" || !("indexedDB" in window)) return [];
  const metadataRecords = (await readStoreValues<StoredTranslationBatchMetadata>(QUEUE_METADATA_STORE_NAME))
    .filter((record) => Array.isArray(record.items));
  const metadataIds = new Set(metadataRecords.map((record) => record.id));
  const legacyIds = (await readStoreKeys(QUEUE_STORE_NAME)).filter(
    (id): id is string => typeof id === "string" && !metadataIds.has(id),
  );
  const legacyRecords = (await Promise.all(
    legacyIds.map((id) => readStoreValue<StoredTranslationBatch>(QUEUE_STORE_NAME, id)),
  )).filter((record): record is StoredTranslationBatch => Boolean(record && Array.isArray(record.items)));
  return [
    ...metadataRecords.map((record) => deserializeTranslationBatch(record, true)),
    ...legacyRecords.map((record) => deserializeTranslationBatch(record)),
  ];
}

/** Load the full files for one interrupted upload when it is ready to resume. */
export async function loadTranslationBatchFromIDB(batchId: string): Promise<TranslationBatch | null> {
  const record = await readStoreValue<StoredTranslationBatch>(QUEUE_STORE_NAME, batchId);
  return record && Array.isArray(record.items) ? deserializeTranslationBatch(record) : null;
}

/** Load previously persisted Queue items from IndexedDB */
export async function loadQueueFromIDB(): Promise<QueuedImage[]> {
  if (typeof window === "undefined" || !("indexedDB" in window)) return [];
  const db = await openDB();
  if (!db.objectStoreNames.contains(QUEUE_STORE_NAME)) {
    db.close();
    return [];
  }
  const tx = db.transaction(QUEUE_STORE_NAME, "readonly");
  const store = tx.objectStore(QUEUE_STORE_NAME);
  return new Promise((resolve, reject) => {
    const request = store.getAll();
    request.onsuccess = () => {
      db.close();
      const records: StoredQueuedImage[] = request.result || [];
      const queue: QueuedImage[] = records.map((r) => {
        const file = new File([r.data], r.name, {
          type: r.type,
          lastModified: r.lastModified,
        });
        bufferCache.set(file, r.data);

        let result: Blob | string | undefined = undefined;
        if (r.resultUrl) {
          result = r.resultUrl;
        } else if (r.resultBlobData) {
          result = new Blob([r.resultBlobData], {
            type: r.resultBlobType || "image/png",
          });
          bufferCache.set(result, r.resultBlobData);
        }

        return {
          id: r.id,
          mangaGroupId: r.mangaGroupId,
          pageId: r.pageId,
          pageOrder: r.pageOrder,
          sourcePath: r.sourcePath,
          file,
          addedAt: new Date(r.addedAt || Date.now()),
          status: r.status,
          mangaTitle: r.mangaTitle,
          step: r.step,
          offlineModel: r.offlineModel,
          result,
          folder: r.folder || undefined,
          error: r.error || undefined,
          excludeColor: r.excludeColor,
          isAutoColorDetected: r.isAutoColorDetected,
        };
      });
      resolve(queue);
    };
    request.onerror = () => {
      db.close();
      reject(request.error);
    };
  });
}

/** Clear all queue items from IndexedDB */
export async function clearQueueFromIDB(): Promise<void> {
  if (typeof window === "undefined" || !("indexedDB" in window)) return;
  const db = await openDB();
  if (!db.objectStoreNames.contains(QUEUE_STORE_NAME)) {
    db.close();
    return;
  }
  const tx = db.transaction(QUEUE_STORE_NAME, "readwrite");
  tx.objectStore(QUEUE_STORE_NAME).clear();
  await new Promise<void>((resolve, reject) => {
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); reject(tx.error); };
  });
  const metadataDb = await openDB();
  const metadataTx = metadataDb.transaction(QUEUE_METADATA_STORE_NAME, "readwrite");
  metadataTx.objectStore(QUEUE_METADATA_STORE_NAME).clear();
  return new Promise((resolve, reject) => {
    metadataTx.oncomplete = () => { metadataDb.close(); resolve(); };
    metadataTx.onerror = () => { metadataDb.close(); reject(metadataTx.error); };
  });
}

/** Legacy API compatibility: save only raw File list */
export async function saveFilesToIDB(files: File[]): Promise<void> {
  const emptyStatuses = new Map<string, FileStatus>();
  const allSelected = new Set(files.map((f) => f.name));
  return saveStudioStateToIDB(
    files,
    emptyStatuses,
    allSelected,
    new Set(),
    new Set(),
    new Map()
  );
}

/** Legacy API compatibility: load only File list */
export async function loadFilesFromIDB(): Promise<File[]> {
  const state = await loadStudioStateFromIDB();
  return state.files;
}

/** Remove all persisted Studio files from IndexedDB */
export async function clearFilesFromIDB(): Promise<void> {
  if (typeof window === "undefined" || !("indexedDB" in window)) return;
  const db = await openDB();
  const tx = db.transaction(FILES_STORE_NAME, "readwrite");
  tx.objectStore(FILES_STORE_NAME).clear();
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); reject(tx.error); };
  });
}
