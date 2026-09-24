import React, { Profiler, useEffect } from "react";

type ProfileSample = { renders: number; actualMs: number; maxActualMs: number };
type LongFrameEntry = PerformanceEntry & { renderStart?: number; styleAndLayoutStart?: number };

const samples = new Map<string, ProfileSample>();
const longTasks: number[] = [];
const longFrames: { durationMs: number; layoutMs: number; paintMs: number }[] = [];
const paintEntries: { name: string; startTimeMs: number }[] = [];
const clickDurations: number[] = [];
const frameIntervals: number[] = [];
let frameCount = 0;
let droppedFrames = 0;
let frameTimeMs = 0;
let previousFrame = 0;

const enabled = () => import.meta.env.DEV && typeof window !== "undefined" &&
  new URLSearchParams(window.location.search).get("renderPerf") === "1";

const reset = () => {
  samples.clear();
  longTasks.length = 0;
  longFrames.length = 0;
  paintEntries.length = 0;
  clickDurations.length = 0;
  frameIntervals.length = 0;
  frameCount = 0;
  droppedFrames = 0;
  frameTimeMs = 0;
  previousFrame = performance.now();
};

const report = () => {
  const orderedFrameIntervals = [...frameIntervals].sort((a, b) => a - b);
  const p95FrameTimeMs = orderedFrameIntervals.length
    ? Math.round(orderedFrameIntervals[Math.ceil(orderedFrameIntervals.length * 0.95) - 1])
    : 0;
  const averageFps = frameTimeMs ? Math.round((frameCount * 1000) / frameTimeMs) : 0;
  const maxClickResponseMs = Math.round(Math.max(0, ...clickDurations));
  const maxLongTaskMs = Math.round(Math.max(0, ...longTasks));
  const acceptance = {
    frameRate: averageFps >= 45,
    clickResponse: maxClickResponseMs <= 100,
    mainThreadTasks: maxLongTaskMs <= 50,
  };
  const result = {
    frame: {
      samples: frameCount,
      averageFps,
      p95FrameTimeMs,
      droppedFrames,
    },
    clicks: {
      count: clickDurations.length,
      minDurationMs: 16,
      maxResponseMs: maxClickResponseMs,
    },
    longTasks: {
      count: longTasks.length,
      totalMs: Math.round(longTasks.reduce((sum, duration) => sum + duration, 0)),
      maxMs: Math.round(Math.max(0, ...longTasks)),
    },
    longFrames: {
      count: longFrames.length,
      maxMs: Math.round(Math.max(0, ...longFrames.map((frame) => frame.durationMs))),
      layoutMs: Math.round(longFrames.reduce((sum, frame) => sum + frame.layoutMs, 0)),
      paintMs: Math.round(longFrames.reduce((sum, frame) => sum + frame.paintMs, 0)),
    },
    react: Object.fromEntries(samples),
    paintEntries: [...paintEntries],
    acceptance: { ...acceptance, passed: Object.values(acceptance).every(Boolean) },
  };
  console.info("Studio render performance", result);
  return result;
};

declare global {
  interface Window {
    __renderPerf?: { reset: typeof reset; report: typeof report };
  }
}

function recordCommit(id: string, _phase: string, actualDuration: number) {
  const sample = samples.get(id) ?? { renders: 0, actualMs: 0, maxActualMs: 0 };
  sample.renders += 1;
  sample.actualMs += actualDuration;
  sample.maxActualMs = Math.max(sample.maxActualMs, actualDuration);
  samples.set(id, sample);
}

export const RenderProfiler: React.FC<{ id: string; children: React.ReactNode }> = ({ id, children }) =>
  enabled() ? <Profiler id={id} onRender={recordCommit}>{children}</Profiler> : <>{children}</>;

export const RenderPerformanceProbe: React.FC = () => {
  useEffect(() => {
    if (!enabled()) return;
    reset();
    window.__renderPerf = { reset, report };
    const observers: PerformanceObserver[] = [];
    const observe = (type: string, callback: (entry: PerformanceEntry) => void) => {
      if (!PerformanceObserver.supportedEntryTypes.includes(type)) return;
      const observer = new PerformanceObserver((list) => list.getEntries().forEach(callback));
      observer.observe({ type, buffered: true });
      observers.push(observer);
    };
    observe("longtask", (entry) => longTasks.push(entry.duration));
    observe("paint", (entry) => paintEntries.push({ name: entry.name, startTimeMs: entry.startTime }));
    if (PerformanceObserver.supportedEntryTypes.includes("event")) {
      const observer = new PerformanceObserver((list) => list.getEntries()
        .filter((entry) => entry.name === "click")
        .forEach((entry) => clickDurations.push(entry.duration)));
      observer.observe({ type: "event", buffered: true, durationThreshold: 16 } as PerformanceObserverInit & { durationThreshold: number });
      observers.push(observer);
    }
    observe("long-animation-frame", (entry) => {
      const frame = entry as LongFrameEntry;
      const layoutStart = frame.styleAndLayoutStart ?? frame.startTime;
      const renderStart = frame.renderStart ?? frame.startTime + frame.duration;
      longFrames.push({
        durationMs: frame.duration,
        layoutMs: Math.max(0, renderStart - layoutStart),
        paintMs: Math.max(0, frame.startTime + frame.duration - renderStart),
      });
    });

    let animationFrame = 0;
    const sampleFrame = (now: number) => {
      if (previousFrame) {
        const delta = now - previousFrame;
        frameCount += 1;
        frameTimeMs += delta;
        frameIntervals.push(delta);
        if (delta > 1000 / 50) droppedFrames += 1;
      }
      previousFrame = now;
      animationFrame = requestAnimationFrame(sampleFrame);
    };
    animationFrame = requestAnimationFrame(sampleFrame);

    return () => {
      cancelAnimationFrame(animationFrame);
      observers.forEach((observer) => observer.disconnect());
      delete window.__renderPerf;
    };
  }, []);

  return null;
};
