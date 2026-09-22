import React from 'react';
import { createRoot } from 'react-dom/client';
import PreviewImage from './app/components/PreviewImage';
import './app/app.css';
const sample = 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="1791"><rect width="1280" height="1791" fill="white"/><circle cx="640" cy="800" r="400" fill="royalblue"/><text x="100" y="150" font-size="80">Preview regression</text></svg>');
createRoot(document.getElementById('root')!).render(<div className="dark"><div className="relative w-full h-[480px] bg-zinc-950 flex items-center justify-center overflow-hidden" style={{width:596}}><PreviewImage file={sample} result="/preview-result.svg"/></div></div>);

// Run with: npm run test:preview, then open /preview-check.html.
// No translation service or user images are used.
async function check() {
  const until = async (predicate: () => boolean) => {
    const deadline = Date.now() + 10000;
    while (!predicate()) {
      if (Date.now() > deadline) throw new Error("Preview regression check timed out");
      await new Promise(resolve => setTimeout(resolve, 20));
    }
  };
  await until(() => document.body.textContent!.includes("Loading translated image"));
  await until(() => document.body.textContent!.includes("could not be loaded"));
  const retry = [...document.querySelectorAll("button")].find(b => b.textContent === "Retry image")!;
  const retryRect = retry.getBoundingClientRect();
  const hitTarget = document.elementFromPoint(retryRect.left + retryRect.width / 2, retryRect.top + retryRect.height / 2);
  if (!retry.contains(hitTarget)) throw new Error("Retry button center is covered by another element");
  retry.click();
  await until(() => [...document.images].some(img => img.src.includes("previewRetry=") && img.naturalWidth === 1280));
  await until(() => !document.querySelector('[role="status"]'));
  const image = document.querySelector("img")!;
  if (image.getBoundingClientRect().height < 100) throw new Error("Loaded preview is not visible");
  const result = document.createElement("p");
  result.textContent = "PASS: loading, failed request, retry and visible completed image";
  document.body.append(result);
}
check().catch(error => { document.body.append(String(error)); console.error(error); });
