import assert from "node:assert/strict";
import { buildApiUrl, getApiBaseUrl, isPhoneDevice } from "./api";

assert.equal(isPhoneDevice("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"), true);
assert.equal(isPhoneDevice("Mozilla/5.0 (X11; Linux x86_64)"), false);
assert.equal(
  getApiBaseUrl({ DESKTOP_API_URL: "http://desktop/", PHONE_API_URL: "http://phone/" }, "Android Mobile"),
  "http://phone",
);
assert.equal(
  getApiBaseUrl({ DESKTOP_API_URL: "http://desktop/", PHONE_API_URL: "http://phone/" }, "Mozilla/5.0"),
  "http://desktop",
);
assert.equal(buildApiUrl("/api/translate", "http://phone"), "http://phone/translate");
assert.equal(buildApiUrl("/result/folder/final.png", "http://phone"), "http://phone/result/folder/final.png");
assert.equal(buildApiUrl("/api/translate", ""), "/api/translate");

console.log("api routing checks passed");
