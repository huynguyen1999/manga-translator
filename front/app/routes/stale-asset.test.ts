import assert from "node:assert/strict";
import vm from "node:vm";
import { loader } from "./stale-asset";

const request = new Request("http://localhost:6868/assets/old-build.js");
const response = loader({ request });

assert.equal(response.status, 200);
assert.equal(response.headers.get("Cache-Control"), "no-store");
assert.equal(response.headers.get("Content-Type"), "text/javascript; charset=utf-8");

const source = (await response.text()).replace("export {};", "");
const stored = new Map<string, string>();
let reloads = 0;
const page = () => ({
  location: { pathname: "/assets/old-build.js", reload: () => reloads++ },
  sessionStorage: {
    getItem: (key: string) => stored.get(key) ?? null,
    setItem: (key: string, value: string) => stored.set(key, value),
  },
});

vm.runInNewContext(source, page());
assert.equal(reloads, 1);
assert.throws(() => vm.runInNewContext(source, page()), /still missing after reloading/);

const missingStyle = loader({
  request: new Request("http://localhost:6868/assets/old-build.css"),
});
assert.equal(missingStyle.status, 404);

console.log("stale asset recovery test passed successfully");
