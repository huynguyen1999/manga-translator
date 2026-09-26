import assert from "node:assert/strict";
import routes from "../routes";

const topLevel = routes as unknown as Array<{
  path?: string;
  file?: string;
  children?: Array<{ path?: string; file?: string; id?: string }>;
}>;

assert.deepEqual(
  topLevel.filter((entry) => entry.path).map(({ path, file }) => [path, file]),
  [
    ["assets/*", "routes/stale-asset.ts"],
    ["result/*", "routes/result.ts"],
    ["api/*", "routes/api.ts"],
  ],
);

const workspace = topLevel.find((entry) => entry.file === "routes/workspace-layout.tsx");
assert.ok(workspace, "workspace routes remain under the workspace layout");
assert.deepEqual(
  workspace.children?.map(({ path, file, id }) => [path ?? "<index>", file, id]),
  [
    ["<index>", "routes/workspace-page.tsx", "workspace-index"],
    ["studio", "routes/workspace-page.tsx", "workspace-studio"],
    ["gallery", "routes/workspace-page.tsx", "workspace-gallery"],
    ["pipeline-lab", "routes/pipeline-lab.tsx", undefined],
    ["search-lab", "routes/workspace-page.tsx", "workspace-search"],
    ["read", "routes/workspace-page.tsx", "workspace-read"],
    ["gallery/pages/:folder", "routes/workspace-page.tsx", "workspace-page-view"],
    ["gallery/pages/:folder/edit", "routes/workspace-page.tsx", "workspace-page-edit"],
    ["gallery/manga/*", "routes/workspace-page.tsx", "workspace-manga-detail"],
    ["gallery/series/:seriesId", "routes/workspace-page.tsx", "workspace-series-detail"],
  ],
);

console.log("app route contracts passed");
