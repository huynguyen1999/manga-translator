/**
 * Manga Image Cache & Prefetcher
 * 
 * Provides background prefetching and in-memory caching for manga reading.
 * - Fetches upcoming pages ahead of current reading position (sliding window).
 * - Caches decoded image blobs & aspect ratios to eliminate pop-in and layout shift.
 * - Bounded LRU cache size with automatic URL.revokeObjectURL to avoid memory leaks.
 * - Graceful CacheStorage fallback for persistent caching across reader sessions.
 */

export interface CacheEntry {
  url: string;
  blob?: Blob;
  blobUrl?: string;
  aspectRatio?: number;
  status: 'loading' | 'loaded' | 'error';
  error?: string;
}

type CacheListener = (entry: CacheEntry) => void;

const CACHE_STORAGE_NAME = 'manga-reader-images-v1';

export class MangaImageCache {
  private maxEntries: number;
  private entries: Map<string, CacheEntry> = new Map();
  private inFlightFetches: Map<string, Promise<CacheEntry | null>> = new Map();
  private listeners: Map<string, Set<CacheListener>> = new Map();
  private prefetchQueue: string[] = [];
  private activeFetches = 0;
  private concurrency: number;
  private abortControllers: Map<string, AbortController> = new Map();

  constructor(options: { maxEntries?: number; concurrency?: number } = {}) {
    this.maxEntries = options.maxEntries ?? 40;
    this.concurrency = options.concurrency ?? 2;
  }

  /**
   * Check whether an image URL is already fully loaded and cached in memory.
   */
  public isLoaded(url: string): boolean {
    if (!url) return false;
    const entry = this.entries.get(url);
    return entry?.status === 'loaded';
  }

  /**
   * Retrieve a cached entry and refresh its LRU position.
   */
  public get(url: string): CacheEntry | undefined {
    if (!url) return undefined;
    const entry = this.entries.get(url);
    if (entry) {
      // Refresh LRU order (delete and re-insert)
      this.entries.delete(url);
      this.entries.set(url, entry);
    }
    return entry;
  }

  /**
   * Subscribe to updates for a specific URL.
   */
  public subscribe(url: string, listener: CacheListener): () => void {
    if (!this.listeners.has(url)) {
      this.listeners.set(url, new Set());
    }
    this.listeners.get(url)!.add(listener);

    return () => {
      const set = this.listeners.get(url);
      if (set) {
        set.delete(listener);
        if (set.size === 0) {
          this.listeners.delete(url);
        }
      }
    };
  }

  /**
   * Notify all listeners subscribed to a URL.
   */
  private notify(url: string, entry: CacheEntry): void {
    const set = this.listeners.get(url);
    if (set) {
      for (const listener of set) {
        try {
          listener(entry);
        } catch (err) {
          console.error(`[MangaImageCache] Listener error for ${url}:`, err);
        }
      }
    }
  }

  /**
   * Enforce bounded LRU size by revoking and deleting oldest entries.
   */
  private enforceCapacity(): void {
    while (this.entries.size > this.maxEntries) {
      const oldestKey = this.entries.keys().next().value;
      if (!oldestKey) break;
      const oldestEntry = this.entries.get(oldestKey);
      if (oldestEntry?.blobUrl && typeof URL !== 'undefined' && URL.revokeObjectURL) {
        try {
          URL.revokeObjectURL(oldestEntry.blobUrl);
        } catch {
          // ignore revocation errors
        }
      }
      this.entries.delete(oldestKey);
    }
  }

  /**
   * Warm up image decoding and determine natural aspect ratio.
   */
  /**
   * Warm up image decoding and determine natural aspect ratio.
   * Uses a safety timeout and checks img.complete so it NEVER hangs on mobile browsers.
   */
  private async decodeImage(blobUrl: string): Promise<number | undefined> {
    if (typeof Image === 'undefined') return undefined;

    return new Promise((resolve) => {
      let settled = false;
      const done = (ratio?: number) => {
        if (!settled) {
          settled = true;
          clearTimeout(timeoutId);
          resolve(ratio);
        }
      };

      // Strict safety timeout so image decode errors or mobile resource constraints never stall queue
      const timeoutId = setTimeout(() => done(undefined), 2500);

      try {
        const img = new Image();
        img.decoding = 'async';

        const checkDimensions = () => {
          if (img.naturalWidth > 0 && img.naturalHeight > 0) {
            done(img.naturalWidth / img.naturalHeight);
          } else {
            done(undefined);
          }
        };

        img.onload = checkDimensions;
        img.onerror = () => done(undefined);
        img.src = blobUrl;

        // Immediate check if already complete in memory
        if (img.complete) {
          checkDimensions();
          return;
        }

        if ('decode' in img && typeof img.decode === 'function') {
          img
            .decode()
            .then(checkDimensions)
            .catch(() => {
              if (img.complete) {
                checkDimensions();
              }
              // onload / onerror / timeoutId will handle if not complete
            });
        }
      } catch {
        done(undefined);
      }
    });
  }

  /**
   * Fetch and cache a single image URL. Deduplicates in-flight requests.
   */
  public async fetchAndCache(url: string, signal?: AbortSignal): Promise<CacheEntry | null> {
    if (!url) return null;

    // Already cached and loaded
    const existing = this.entries.get(url);
    if (existing && existing.status === 'loaded') {
      return existing;
    }

    // Deduplicate in-flight fetch
    const inFlight = this.inFlightFetches.get(url);
    if (inFlight) {
      return inFlight;
    }

    const fetchPromise = (async (): Promise<CacheEntry | null> => {
      // Mark entry as loading
      const loadingEntry: CacheEntry = { url, status: 'loading' };
      this.entries.set(url, loadingEntry);
      this.notify(url, loadingEntry);

      // Setup abort controller with timeout
      const abortCtrl = new AbortController();
      this.abortControllers.set(url, abortCtrl);

      const timeoutId = setTimeout(() => {
        abortCtrl.abort();
      }, 15000); // 15s timeout for mobile networks

      // Handle external signal if provided
      const abortHandler = () => abortCtrl.abort();
      if (signal) {
        signal.addEventListener('abort', abortHandler, { once: true });
      }

      try {
        let blob: Blob | null = null;

        // Helper to attempt fetch with fallback to relative path on failure
        const doFetch = async (targetUrl: string): Promise<Blob | null> => {
          let res: Response | null = null;
          try {
            res = await fetch(targetUrl, { signal: abortCtrl.signal });
          } catch (err: any) {
            // If absolute URL fails on phone (e.g. port 8000 unreachable or network error), try relative path
            if (/^https?:\/\//i.test(targetUrl) && typeof window !== 'undefined') {
              try {
                const parsed = new URL(targetUrl);
                const relativeUrl = parsed.pathname + parsed.search;
                const fallbackRes = await fetch(relativeUrl, { signal: abortCtrl.signal });
                if (fallbackRes.ok) {
                  return await fallbackRes.blob();
                }
              } catch {
                // relative fallback failed too
              }
            }
            throw err;
          }

          if (!res.ok) {
            // Also try relative fallback if absolute gave an HTTP error (like 502/404/ECONNREFUSED)
            if (/^https?:\/\//i.test(targetUrl) && typeof window !== 'undefined') {
              try {
                const parsed = new URL(targetUrl);
                const relativeUrl = parsed.pathname + parsed.search;
                const fallbackRes = await fetch(relativeUrl, { signal: abortCtrl.signal });
                if (fallbackRes.ok) {
                  return await fallbackRes.blob();
                }
              } catch {
                // ignore
              }
            }
            throw new Error(`HTTP ${res.status} ${res.statusText}`);
          }

          // Save clone to CacheStorage asynchronously if available
          if (typeof caches !== 'undefined') {
            try {
              const cacheStorage = await caches.open(CACHE_STORAGE_NAME);
              await cacheStorage.put(targetUrl, res.clone());
            } catch {
              // Ignore CacheStorage errors
            }
          }

          return await res.blob();
        };

        // If it's already a blob: or data: URL, we don't need network fetch
        if (url.startsWith('blob:') || url.startsWith('data:')) {
          const res = await fetch(url, { signal: abortCtrl.signal });
          if (res.ok) {
            blob = await res.blob();
          }
        } else {
          // Check CacheStorage first if available
          if (typeof caches !== 'undefined') {
            try {
              const cacheStorage = await caches.open(CACHE_STORAGE_NAME);
              const cachedRes = await cacheStorage.match(url);
              if (cachedRes && cachedRes.ok) {
                blob = await cachedRes.blob();
              }
            } catch {
              // Ignore CacheStorage errors
            }
          }

          if (!blob) {
            try {
              blob = await doFetch(url);
            } catch {
              // Transient network retry once after 300ms delay (useful on mobile devices)
              await new Promise((r) => setTimeout(r, 300));
              blob = await doFetch(url);
            }
          }
        }

        if (!blob) {
          throw new Error('No image blob retrieved');
        }

        const blobUrl = typeof URL !== 'undefined' && URL.createObjectURL
          ? URL.createObjectURL(blob)
          : url;

        // Decode image to prime browser bitmap cache and get natural aspect ratio
        const aspectRatio = await this.decodeImage(blobUrl);

        const loadedEntry: CacheEntry = {
          url,
          blob,
          blobUrl,
          aspectRatio,
          status: 'loaded',
        };

        this.entries.set(url, loadedEntry);
        this.enforceCapacity();
        this.notify(url, loadedEntry);
        return loadedEntry;
      } catch (err: any) {
        if (abortCtrl.signal.aborted) {
          this.entries.delete(url);
          return null;
        }

        const errorEntry: CacheEntry = {
          url,
          status: 'error',
          error: err?.message || 'Failed to fetch image',
        };
        // Don't permanently lock error in entries map so future navigations or retries can re-attempt
        this.entries.delete(url);
        this.notify(url, errorEntry);
        return errorEntry;
      } finally {
        clearTimeout(timeoutId);
        this.abortControllers.delete(url);
        this.inFlightFetches.delete(url);
        if (signal) {
          signal.removeEventListener('abort', abortHandler);
        }
      }
    })();

    this.inFlightFetches.set(url, fetchPromise);
    return fetchPromise;
  }

  /**
   * Schedule prefetching for an ordered list of image URLs.
   * Processes sequentially with concurrency control.
   */
  public prefetchImages(urls: string[]): void {
    // Filter to URLs that are not yet loaded and not already queued
    const needed = urls.filter((url) => {
      if (!url) return false;
      const entry = this.entries.get(url);
      if (entry?.status === 'loaded') return false;
      if (this.inFlightFetches.has(url)) return false;
      if (this.prefetchQueue.includes(url)) return false;
      return true;
    });

    if (needed.length === 0) return;

    // Prepend newly requested images ahead of older queue items (priority for nearest pages)
    this.prefetchQueue = [...needed, ...this.prefetchQueue.filter((u) => !needed.includes(u))];
    this.drainQueue();
  }

  /**
   * Drain queue respecting concurrency limit.
   */
  private drainQueue(): void {
    while (this.activeFetches < this.concurrency && this.prefetchQueue.length > 0) {
      const url = this.prefetchQueue.shift();
      if (!url) break;

      if (this.isLoaded(url) || this.inFlightFetches.has(url)) {
        continue;
      }

      this.activeFetches++;
      this.fetchAndCache(url)
        .catch(() => {})
        .finally(() => {
          this.activeFetches--;
          this.drainQueue();
        });
    }
  }

  /**
   * Cancel pending and in-flight prefetch requests (e.g. on modal close or unmount).
   */
  public cancelPending(): void {
    this.prefetchQueue = [];
    for (const [url, controller] of this.abortControllers.entries()) {
      controller.abort();
    }
    this.abortControllers.clear();
    this.inFlightFetches.clear();
    this.activeFetches = 0;
  }

  /**
   * Clear the entire cache and revoke all object URLs.
   */
  public clear(): void {
    this.cancelPending();
    if (typeof URL !== 'undefined' && URL.revokeObjectURL) {
      for (const entry of this.entries.values()) {
        if (entry.blobUrl) {
          try {
            URL.revokeObjectURL(entry.blobUrl);
          } catch {
            // ignore
          }
        }
      }
    }
    this.entries.clear();
    this.listeners.clear();
  }
}

// Global default singleton instance for reader
export const mangaImageCache = new MangaImageCache();
