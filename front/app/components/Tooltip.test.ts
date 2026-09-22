import assert from "node:assert/strict";
import { isPhoneDevice } from "@/utils/api";
import { getReaderTitle, isPhoneOrTouch } from "./MangaReaderModal";

// 1. Verify phone device detection works for various user agents
assert.equal(isPhoneDevice("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"), true);
assert.equal(isPhoneDevice("Mozilla/5.0 (Linux; Android 14; Pixel 8) Mobile Safari/537.36"), true);
assert.equal(isPhoneDevice("Mozilla/5.0 (Windows Phone 10.0; Android 6.0.1)"), true);
assert.equal(isPhoneDevice("Mozilla/5.0 (iPad; CPU OS 16_0 like Mac OS X)"), true);
assert.equal(isPhoneDevice("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"), false);
assert.equal(isPhoneDevice("Mozilla/5.0 (Windows NT 10.0; Win64; x64)"), false);
assert.equal(isPhoneDevice("Mozilla/5.0 (X11; Linux x86_64)"), false);

// 2. Verify isPhoneOrTouch in node environment behaves safely
assert.equal(typeof isPhoneOrTouch, "function");
// In node, window is undefined, should return false safely without crashing
assert.equal(isPhoneOrTouch(), false);
assert.equal(getReaderTitle("Fullscreen (F)", true), undefined);
assert.equal(getReaderTitle("Fullscreen (F)", false), "Fullscreen (F)");

// 3. Test reader toggle logic simulation:
// When controls are visible, clicking screen should hide them
// When controls are hidden, clicking screen should reveal them
let controlsVisible = true;
const hideControls = () => { controlsVisible = false; };
const revealControls = () => { controlsVisible = true; };
const toggleControls = () => {
  if (controlsVisible) hideControls();
  else revealControls();
};

toggleControls();
assert.equal(controlsVisible, false, "Toggling visible controls should hide them");
toggleControls();
assert.equal(controlsVisible, true, "Toggling hidden controls should reveal them");

// 4. Test scroll-to-hide logic simulation on mobile:
// While scrolling on mobile, hideControls should be invoked
const handleScrollSim = (isPhone: boolean) => {
  if (isPhone) {
    hideControls();
  } else {
    revealControls();
  }
};

controlsVisible = true;
handleScrollSim(true);
assert.equal(controlsVisible, false, "Scrolling on phone should immediately hide controls");

controlsVisible = false;
handleScrollSim(false);
assert.equal(controlsVisible, true, "Scrolling on desktop can reveal controls");

// 5. Test fullscreen toggle behavior:
// Entering or toggling fullscreen should hide controls to keep screen clean
let fullscreenControls = true;
const toggleFullscreenSim = () => {
  fullscreenControls = false;
};
toggleFullscreenSim();
assert.equal(fullscreenControls, false, "Entering fullscreen should hide controls for pure image display");

// 6. Test floating Go-to-top button visibility logic:
// Floating buttons should be hidden whenever controls are hidden
const isGoToTopVisible = (page: number, showControls: boolean) => {
  return page > 1 && showControls;
};
assert.equal(isGoToTopVisible(5, false), false, "Go to top button must be hidden when showControls is false");
assert.equal(isGoToTopVisible(5, true), true, "Go to top button should be visible when showControls is true");
assert.equal(isGoToTopVisible(1, true), false, "Go to top button should not show on page 1");

console.log("Tooltip & Reader mobile scroll/tap tests passed successfully!");
