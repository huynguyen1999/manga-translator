import { getBackendBaseUrl } from "./backend.server";

export async function proxyApiRequest(request: Request, splat: string | undefined): Promise<Response> {
  const backendBase = getBackendBaseUrl();
  const subpath = (splat || "").replace(/^\/+/, "");
  const search = new URL(request.url).search;
  const targetUrl = `${backendBase}/${subpath}${search}`;

  const headers = new Headers(request.headers);
  headers.delete("host");

  try {
    const isBodyAllowed = !["GET", "HEAD"].includes(request.method);
    const upstream = await fetch(targetUrl, {
      method: request.method,
      headers,
      body: isBodyAllowed ? request.body : undefined,
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
