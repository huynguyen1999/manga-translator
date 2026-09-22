const recoveryModule = `const key = "stale-asset:" + location.pathname;
if (!sessionStorage.getItem(key)) {
  sessionStorage.setItem(key, "1");
  location.reload();
} else {
  throw new Error("Frontend asset is still missing after reloading");
}
export {};`;

export function loader({ request }: { request: Request }) {
  if (!new URL(request.url).pathname.endsWith(".js")) {
    return new Response(null, { status: 404 });
  }

  return new Response(recoveryModule, {
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": "text/javascript; charset=utf-8",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
