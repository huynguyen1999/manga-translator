export function getBackendBaseUrl(): string {
  return (
    process.env.VITE_BACKEND_URL ||
    process.env.DESKTOP_API_URL ||
    "http://127.0.0.1:8000"
  ).replace(/\/+$/, "");
}
