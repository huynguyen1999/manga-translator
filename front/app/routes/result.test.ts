import assert from "node:assert/strict";
import path from "node:path";
import fs from "node:fs";
import { findLocalResultFile, getBackendBaseUrl, handleResultGet } from "./result.server";
import { loader } from "./result";

// 1. Test getBackendBaseUrl default
const backendUrl = getBackendBaseUrl();
assert.ok(backendUrl.startsWith("http"));

// 2. Test directory traversal protection
assert.equal(findLocalResultFile("../../package.json"), null);
assert.equal(findLocalResultFile("../../../etc/passwd"), null);

// 3. Test finding existing result file
const knownFolder = "1789228749791-aac7bf9c-1536-ENG-gemini";
const knownFile = "final.png";
const resultCandidate = path.resolve(process.cwd(), "..", "result", knownFolder, knownFile);

if (fs.existsSync(resultCandidate)) {
  const found = findLocalResultFile(`${knownFolder}/${knownFile}`);
  assert.ok(found !== null, "Should find existing local result file");
  assert.equal(found.filePath, resultCandidate);
  assert.ok(found.stat.size > 0);

  // Test loader for GET via route entry
  const req = new Request(`http://localhost:6868/result/${knownFolder}/${knownFile}`, {
    method: "GET",
  });
  const res = await loader({
    request: req,
    params: { "*": `${knownFolder}/${knownFile}` },
  } as any);

  assert.equal(res.status, 200);
  assert.equal(res.headers.get("Content-Type"), "image/png");
  assert.ok(res.headers.get("Content-Disposition")?.includes(knownFile));
  assert.ok(res.body !== null, "GET response should have body");

  // Test loader for HEAD via handleResultGet
  const headReq = new Request(`http://localhost:6868/result/${knownFolder}/${knownFile}`, {
    method: "HEAD",
  });
  const headRes = await handleResultGet(headReq, `${knownFolder}/${knownFile}`);

  assert.equal(headRes.status, 200);
  assert.equal(headRes.headers.get("Content-Type"), "image/png");
  assert.ok(headRes.headers.get("Content-Length"));
  assert.equal(headRes.body, null, "HEAD response should not have body");
}

// 4. Test missing splat
const missingReq = new Request("http://localhost:6868/result/", { method: "GET" });
const missingRes = await loader({
  request: missingReq,
  params: { "*": "" },
} as any);
assert.equal(missingRes.status, 404);

console.log("result route tests passed successfully");
