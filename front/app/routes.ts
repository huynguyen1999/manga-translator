import { type RouteConfig, index, layout, route } from "@react-router/dev/routes";

export default [
  route("assets/*", "routes/stale-asset.ts"),
  layout("routes/workspace-layout.tsx", [
    index("routes/workspace-page.tsx", { id: "workspace-index" }),
    route("studio", "routes/workspace-page.tsx", { id: "workspace-studio" }),
    route("gallery", "routes/workspace-page.tsx", { id: "workspace-gallery" }),
    route("pipeline-lab", "routes/pipeline-lab.tsx"),
    route("search-lab", "routes/workspace-page.tsx", { id: "workspace-search" }),
    route("read", "routes/workspace-page.tsx", { id: "workspace-read" }),
    route("gallery/pages/:folder", "routes/workspace-page.tsx", { id: "workspace-page-view" }),
    route("gallery/pages/:folder/edit", "routes/workspace-page.tsx", { id: "workspace-page-edit" }),
    route("gallery/manga/*", "routes/workspace-page.tsx", { id: "workspace-manga-detail" }),
    route("gallery/series/:seriesId", "routes/workspace-page.tsx", { id: "workspace-series-detail" }),
  ]),
  route("result/*", "routes/result.ts"),
  route("api/*", "routes/api.ts"),
] satisfies RouteConfig;
