import { apiUrl } from "./api";

const INSTALLATION_ID_KEY = "manga-installation-id";

export interface RemoteReadingProgress {
  id?: string | null;
  groupId?: string | null;
  mangaTitle?: string | null;
  pageId?: string | null;
  page?: number | null;
  scrollTop?: number;
  complete?: boolean;
  updatedAt?: string | null;
}

export const getInstallationId = (): string | null => {
  if (typeof window === "undefined") return null;
  const existing = window.localStorage.getItem(INSTALLATION_ID_KEY);
  if (existing) return existing;
  const generated = globalThis.crypto?.randomUUID?.() || `install-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  window.localStorage.setItem(INSTALLATION_ID_KEY, generated);
  return generated;
};

export const fetchReadingProgress = async (groupId: string): Promise<RemoteReadingProgress | null> => {
  const installationId = getInstallationId();
  if (!installationId) return null;
  const response = await fetch(
    apiUrl(`/api/reading-progress?installationId=${encodeURIComponent(installationId)}&groupId=${encodeURIComponent(groupId)}`),
  );
  if (!response.ok) throw new Error(`Could not load reading progress (${response.status})`);
  return (await response.json()) as RemoteReadingProgress;
};

export const saveReadingProgress = async (
  groupId: string,
  progress: RemoteReadingProgress,
): Promise<void> => {
  const installationId = getInstallationId();
  if (!installationId) return;
  const response = await fetch(apiUrl("/api/reading-progress"), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      installationId,
      groupId,
      ...progress,
      updatedAt: progress.updatedAt || new Date().toISOString(),
    }),
  });
  if (!response.ok) throw new Error(`Could not save reading progress (${response.status})`);
};
