import fs from "node:fs";
import path from "node:path";
import { Readable } from "node:stream";
import { getBackendBaseUrl } from "./backend.server";

export { getBackendBaseUrl } from "./backend.server";

const MIME_TYPES: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".bmp": "image/bmp",
  ".json": "application/json; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
};

export function getResultDirs(): string[] {
  const custom = process.env.MANGA_TRANSLATOR_RESULT_DIR;
  const cwd = process.cwd();
  return [
    custom,
    path.resolve(cwd, "..", "workspace", "results"),
    path.resolve(cwd, "workspace", "results"),
    path.resolve(cwd, "..", "result"),
    path.resolve(cwd, "result"),
  ].filter(Boolean) as string[];
}

export function findLocalResultFile(splat: string): { filePath: string; stat: fs.Stats } | null {
  const normalized = path.normalize(splat).replace(/^(\.\.[\/\\])+/, "");
  if (normalized.includes("..")) {
    return null;
  }

  for (const baseDir of getResultDirs()) {
    const resolvedBase = path.resolve(baseDir);
    const resolved = path.resolve(baseDir, normalized);
    if (!resolved.startsWith(resolvedBase + path.sep) && resolved !== resolvedBase) {
      continue;
    }
    if (fs.existsSync(resolved)) {
      try {
        const stat = fs.statSync(resolved);
        if (stat.isFile()) {
          return { filePath: resolved, stat };
        }
      } catch {
        // continue search
      }
    }
  }
  return null;
}

export async function handleResultGet(request: Request, splat: string | undefined): Promise<Response> {
  if (!splat) {
    return new Response("Not Found", { status: 404 });
  }

  // 1. Try serving directly from local result directory if available
  const local = findLocalResultFile(splat);
  if (local) {
    const ext = path.extname(local.filePath).toLowerCase();
    const contentType = MIME_TYPES[ext] || "application/octet-stream";
    const filename = path.basename(local.filePath);

    const headers: Record<string, string> = {
      "Content-Type": contentType,
      "Content-Length": String(local.stat.size),
      "Content-Disposition": `inline; filename="${filename}"`,
      "Cache-Control": "public, max-age=31536000, immutable",
    };

    if (request.method === "HEAD") {
      return new Response(null, { status: 200, headers });
    }

    const nodeStream = fs.createReadStream(local.filePath);
    const webStream = Readable.toWeb(nodeStream) as ReadableStream;
    return new Response(webStream, { status: 200, headers });
  }

  // 2. Fall back to proxying from backend server
  const backendBase = getBackendBaseUrl();
  const search = new URL(request.url).search;
  const targetUrl = `${backendBase}/result/${splat}${search}`;

  try {
    const upstream = await fetch(targetUrl, {
      method: request.method,
      headers: {
        accept: request.headers.get("accept") || "*/*",
      },
    });

    const headers = new Headers(upstream.headers);
    if (!headers.has("Cache-Control") && upstream.status === 200) {
      headers.set("Cache-Control", "public, max-age=31536000, immutable");
    }
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers,
    });
  } catch (err: any) {
    return new Response(
      JSON.stringify({ error: "Backend unavailable", detail: err.message }),
      {
        status: 502,
        headers: { "Content-Type": "application/json" },
      }
    );
  }
}

export async function handleResultAction(request: Request, splat: string | undefined): Promise<Response> {
  if (!splat) {
    return new Response("Not Found", { status: 404 });
  }

  const backendBase = getBackendBaseUrl();
  const search = new URL(request.url).search;
  const targetUrl = `${backendBase}/result/${splat}${search}`;

  try {
    const headers = new Headers(request.headers);
    headers.delete("host");

    const upstream = await fetch(targetUrl, {
      method: request.method,
      headers,
      body: request.body,
      // @ts-expect-error duplex required for streaming request bodies in node fetch
      duplex: "half",
    });

    const responseHeaders = new Headers(upstream.headers);
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  } catch (err: any) {
    return new Response(
      JSON.stringify({ error: "Backend unavailable", detail: err.message }),
      {
        status: 502,
        headers: { "Content-Type": "application/json" },
      }
    );
  }
}
