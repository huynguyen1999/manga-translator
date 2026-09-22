type ApiEnv = {
  DESKTOP_API_URL?: string;
  PHONE_API_URL?: string;
};

const phoneUserAgent = /Android|iPhone|iPad|iPod|Windows Phone|Mobile/i;

export const isPhoneDevice = (userAgent: string): boolean => phoneUserAgent.test(userAgent);

export const getApiBaseUrl = (
  env: ApiEnv = import.meta.env as ApiEnv,
  userAgent = typeof navigator === "undefined" ? "" : navigator.userAgent,
): string => {
  let baseUrl = isPhoneDevice(userAgent) ? env.PHONE_API_URL : env.DESKTOP_API_URL;
  if (typeof window !== "undefined" && window.location?.hostname && baseUrl) {
    try {
      const parsed = new URL(baseUrl);
      if (
        parsed.hostname !== window.location.hostname &&
        window.location.hostname !== "0.0.0.0" &&
        !parsed.hostname.includes("desktop") &&
        !parsed.hostname.includes("phone")
      ) {
        parsed.hostname = window.location.hostname;
        baseUrl = parsed.origin;
      }
    } catch {
      // ignore invalid URLs
    }
  }
  return baseUrl?.replace(/\/+$/, "") || "";
};

export const buildApiUrl = (path: string, baseUrl: string): string => {
  if (/^(?:[a-z][a-z\d+.-]*:)?\/\//i.test(path) || path.startsWith("blob:") || path.startsWith("data:")) {
    return path;
  }
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const backendPath = baseUrl && (normalizedPath === "/api" || normalizedPath.startsWith("/api/"))
    ? normalizedPath.slice(4) || "/"
    : normalizedPath;
  return `${baseUrl}${backendPath}`;
};

export const apiUrl = (path: string): string => buildApiUrl(path, getApiBaseUrl());
