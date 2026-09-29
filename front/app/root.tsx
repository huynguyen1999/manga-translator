import {
  isRouteErrorResponse,
  Links,
  Meta,
  Outlet,
  Scripts,
  ScrollRestoration,
} from "react-router";

import type { Route } from "./+types/root";
import "./app.css";
import { RenderPerformanceProbe } from "./utils/renderPerformance";

const staleStylesheetRecovery = `
window.addEventListener("error", (event) => {
  const link = event.target;
  if (!(link instanceof HTMLLinkElement) || link.rel !== "stylesheet" || !link.href.startsWith(location.origin + "/assets/")) return;
  const key = "stale-stylesheet-recovery";
  if (sessionStorage.getItem(key)) return;
  sessionStorage.setItem(key, "1");
  location.reload();
}, true);
window.addEventListener("load", (event) => {
  const link = event.target;
  if (link instanceof HTMLLinkElement && link.rel === "stylesheet" && link.href.startsWith(location.origin + "/assets/")) sessionStorage.removeItem("stale-stylesheet-recovery");
}, true);
`;

export const links: Route.LinksFunction = () => [
  { rel: "preconnect", href: "https://fonts.googleapis.com" },
  {
    rel: "preconnect",
    href: "https://fonts.gstatic.com",
    crossOrigin: "anonymous",
  },
  {
    rel: "stylesheet",
    href: "https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,100..900;1,14..32,100..900&display=swap",
  },
];

export const headers: Route.HeadersFunction = () => ({
  "Cache-Control": "no-store",
});

export function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
        <meta name="theme-color" content="#09090b" />
        <meta name="mobile-web-app-capable" content="yes" />
        <meta name="apple-mobile-web-app-capable" content="yes" />
        <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
        <meta name="apple-mobile-web-app-title" content="Manga Translator" />
        <link rel="manifest" href="/manifest.webmanifest" />
        <Meta />
        <script dangerouslySetInnerHTML={{ __html: staleStylesheetRecovery }} />
        <Links />
      </head>
      <body>
        {children}
        <div id="overlay-root" />
        <ScrollRestoration />
        <Scripts />
      </body>
    </html>
  );
}

export default function App() {
  return (
    <div id="app-root">
      <RenderPerformanceProbe />
      <Outlet />
    </div>
  );
}

export function ErrorBoundary({ error }: Route.ErrorBoundaryProps) {
  let message = "Oops!";
  let details = "An unexpected error occurred.";
  let stack: string | undefined;

  if (isRouteErrorResponse(error)) {
    message = error.status === 404 ? "404" : "Error";
    details =
      error.status === 404
        ? "The requested page could not be found."
        : error.statusText || details;
  } else if (import.meta.env.DEV && error && error instanceof Error) {
    details = error.message;
    stack = error.stack;
  }

  return (
    <main className="pt-16 p-4 container mx-auto">
      <h1>{message}</h1>
      <p>{details}</p>
      {stack && (
        <pre className="w-full p-4 overflow-x-auto">
          <code>{stack}</code>
        </pre>
      )}
    </main>
  );
}
