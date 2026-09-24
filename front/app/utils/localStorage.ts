import type { TranslationSettings, FinishedImage } from '@/types';

const SETTINGS_KEY = 'manga-translator-settings';
const REMEMBER_SETTINGS_KEY = 'manga-translator-remember-settings';
const FINISHED_IMAGES_KEY = 'manga-translator-finished-images';

const isLocalStorageAvailable = (): boolean => {
  try {
    return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined' && window.localStorage !== null;
  } catch {
    return false;
  }
};

export const loadSettings = (): Partial<TranslationSettings> => {
  if (!isLocalStorageAvailable()) return {};
  try {
    const stored = window.localStorage.getItem(SETTINGS_KEY);
    return stored ? JSON.parse(stored) : {};
  } catch (error) {
    console.warn('Failed to load settings from localStorage:', error);
    return {};
  }
};

export const saveSettings = (settings: TranslationSettings): void => {
  if (!isLocalStorageAvailable()) return;
  try {
    window.localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch (error) {
    console.warn('Failed to save settings to localStorage:', error);
  }
};

export const clearSettings = (): void => {
  if (!isLocalStorageAvailable()) return;
  try {
    window.localStorage.removeItem(SETTINGS_KEY);
  } catch (error) {
    console.warn('Failed to clear settings from localStorage:', error);
  }
};

export const loadRememberSettings = (): boolean => {
  if (!isLocalStorageAvailable()) return true;
  try {
    return window.localStorage.getItem(REMEMBER_SETTINGS_KEY) !== 'false';
  } catch {
    return true;
  }
};

export const saveRememberSettings = (remember: boolean): void => {
  if (!isLocalStorageAvailable()) return;
  try {
    window.localStorage.setItem(REMEMBER_SETTINGS_KEY, String(remember));
  } catch (error) {
    console.warn('Failed to save settings preference to localStorage:', error);
  }
};

export const loadFinishedImages = (): FinishedImage[] => {
  if (!isLocalStorageAvailable()) return [];
  try {
    const stored = window.localStorage.getItem(FINISHED_IMAGES_KEY);
    if (!stored) return [];
    // Clean up any corrupt legacy JSON blobs that stored empty objects {}
    window.localStorage.removeItem(FINISHED_IMAGES_KEY);
    return [];
  } catch (error) {
    console.warn('Failed to load finished images from localStorage:', error);
    return [];
  }
};

export const saveFinishedImages = (_images: FinishedImage[]): void => {
  // Blobs cannot be JSON-serialized into localStorage (they become empty objects {}).
  // Images are maintained in-memory in React state for the active session.
};

export const addFinishedImage = (_image: FinishedImage): void => {
  // No-op for localStorage to prevent quota exhaustion and Blob corruption
};
