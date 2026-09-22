export const resultFolderFromUrl = (value: string | null | undefined): string | null => {
  if (!value || typeof value !== "string") return null;
  const match = value.match(/(?:\/api)?\/(?:result|pipeline-lab\/runs)\/([^/?#]+)(?:\/[^?#]*)?(?:[?#]|$)/i);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return match[1];
  }
};
