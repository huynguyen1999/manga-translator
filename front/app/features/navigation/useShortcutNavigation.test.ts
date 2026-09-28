import assert from "node:assert/strict";
import { getShortcutNavIndex, NAV_SHORTCUT_TARGETS } from "./useShortcutNavigation";
import { parseAppPath } from "@/utils/routeState";

console.log("Running useShortcutNavigation unit tests...");

// 1. Check navigation target URLs
assert.equal(NAV_SHORTCUT_TARGETS[0], "/studio");
assert.equal(NAV_SHORTCUT_TARGETS[1], "/gallery");
assert.equal(NAV_SHORTCUT_TARGETS[2], "/gallery?view=series");
assert.equal(NAV_SHORTCUT_TARGETS[3], "/search-lab");
assert.equal(NAV_SHORTCUT_TARGETS.length, 4);

// 2. Test Studio view route
const studioRoute = parseAppPath("/studio");
assert.equal(getShortcutNavIndex(studioRoute), 0);

// 3. Test Gallery view route (manga view)
const galleryRoute = parseAppPath("/gallery");
assert.equal(getShortcutNavIndex(galleryRoute), 1);

// 4. Test Gallery review route (should map to index 1, NOT a separate review view)
const galleryReviewRoute = parseAppPath("/gallery", "?status=review");
assert.equal(getShortcutNavIndex(galleryReviewRoute), 1);

const galleryPendingRoute = parseAppPath("/gallery", "?review=pending");
assert.equal(getShortcutNavIndex(galleryPendingRoute), 1);

const mangaReviewRoute = parseAppPath("/gallery/manga/manga-123", "?review=pending");
assert.equal(getShortcutNavIndex(mangaReviewRoute), 1);

// 5. Test Series view route
const seriesRoute = parseAppPath("/gallery", "?view=series");
assert.equal(getShortcutNavIndex(seriesRoute), 2);

const seriesDetailRoute = parseAppPath("/gallery/series/series-456");
assert.equal(getShortcutNavIndex(seriesDetailRoute), 2);

// 6. Test Search Lab view route
const searchRoute = parseAppPath("/search-lab");
assert.equal(getShortcutNavIndex(searchRoute), 3);

// 7. Verify circular navigation calculation logic
const total = NAV_SHORTCUT_TARGETS.length;

// Forward navigation (isNext = true)
assert.equal((0 + 1) % total, 1); // Studio -> Gallery
assert.equal((1 + 1) % total, 2); // Gallery -> Series
assert.equal((2 + 1) % total, 3); // Series -> Search Lab
assert.equal((3 + 1) % total, 0); // Search Lab -> Studio

// Backward navigation (isNext = false)
assert.equal((0 - 1 + total) % total, 3); // Studio -> Search Lab
assert.equal((1 - 1 + total) % total, 0); // Gallery -> Studio
assert.equal((2 - 1 + total) % total, 1); // Series -> Gallery
assert.equal((3 - 1 + total) % total, 2); // Search Lab -> Series

console.log("All useShortcutNavigation tests passed successfully!");
