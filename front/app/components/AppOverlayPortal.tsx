import React, { useEffect, useState } from "react";
import { createPortal } from "react-dom";

let activeOverlays = 0;
let previousBodyOverflow = "";
let previousAppInert = false;

function lockApplication(): () => void {
  if (activeOverlays === 0) {
    const appRoot = document.getElementById("app-root");
    previousBodyOverflow = document.body.style.overflow;
    previousAppInert = appRoot?.inert ?? false;
    document.body.style.overflow = "hidden";
    if (appRoot) appRoot.inert = true;
  }
  activeOverlays += 1;

  return () => {
    activeOverlays -= 1;
    if (activeOverlays !== 0) return;
    document.body.style.overflow = previousBodyOverflow;
    const appRoot = document.getElementById("app-root");
    if (appRoot) appRoot.inert = previousAppInert;
  };
}

export const AppOverlayPortal: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [target, setTarget] = useState<HTMLElement | null>(null);

  useEffect(() => {
    setTarget(document.getElementById("overlay-root"));
  }, []);

  useEffect(() => {
    if (!target) return;
    return lockApplication();
  }, [target]);

  return target ? createPortal(children, target) : null;
};
