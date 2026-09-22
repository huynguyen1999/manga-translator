import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";
export default defineConfig({ resolve: { alias: { "@": new URL("./app", import.meta.url).pathname } }, plugins: [tailwindcss(), {
  name: "preview-regression-image",
  configureServer(server) {
    server.middlewares.use("/preview-result.svg", (req, res) => {
      setTimeout(() => {
        if (!req.url?.includes("previewRetry=")) {
          res.statusCode = 503;
          res.end("Temporarily unavailable");
          return;
        }
        res.setHeader("Content-Type", "image/svg+xml");
        res.end('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="1791"><rect width="1280" height="1791" fill="white"/><circle cx="640" cy="800" r="400" fill="royalblue"/></svg>');
      }, 500);
    });
  },
}], server: {host:"127.0.0.1",port:5174} });
