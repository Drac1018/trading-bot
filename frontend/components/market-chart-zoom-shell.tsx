"use client";

import { type ReactNode, useEffect, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

type DragRange = {
  start: number;
  end: number;
};

type ChartMeta = {
  candleCount: number;
  visibleStart: number;
  visibleEnd: number;
  plotStartRatio: number;
  plotEndRatio: number;
};

const minDragDistanceRatio = 0.025;
const minZoomCandleCount = 12;

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function finiteNumber(value: string | undefined, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function normalizeRange(start: number, end: number): DragRange {
  return start <= end ? { start, end } : { start: end, end: start };
}

function expandIndexRange(start: number, end: number, maxIndex: number) {
  if (end - start + 1 >= minZoomCandleCount) {
    return { start, end };
  }
  const center = (start + end) / 2;
  const half = (minZoomCandleCount - 1) / 2;
  const expandedStart = Math.floor(center - half);
  const expandedEnd = expandedStart + minZoomCandleCount - 1;
  if (expandedStart < 0) {
    return { start: 0, end: Math.min(maxIndex, minZoomCandleCount - 1) };
  }
  if (expandedEnd > maxIndex) {
    return { start: Math.max(0, maxIndex - minZoomCandleCount + 1), end: maxIndex };
  }
  return { start: expandedStart, end: expandedEnd };
}

function chartMeta(root: HTMLDivElement | null): ChartMeta | null {
  const svg = root?.querySelector("svg");
  if (!root || !svg) {
    return null;
  }
  const candleCount = Math.max(0, Math.trunc(finiteNumber(svg.dataset.chartCandleCount, 0)));
  if (candleCount < minZoomCandleCount) {
    return null;
  }
  const viewBoxWidth = finiteNumber(svg.getAttribute("viewBox")?.split(/\s+/)[2], 860);
  const left = clamp(finiteNumber(svg.dataset.chartLeft, viewBoxWidth * 0.06), 0, viewBoxWidth * 0.4);
  const right = clamp(finiteNumber(svg.dataset.chartRight, viewBoxWidth * 0.02), 0, viewBoxWidth * 0.25);
  const visibleStart = clamp(Math.trunc(finiteNumber(svg.dataset.chartVisibleStartIndex, 0)), 0, candleCount - 1);
  const visibleEnd = clamp(
    Math.trunc(finiteNumber(svg.dataset.chartVisibleEndIndex, candleCount - 1)),
    visibleStart,
    candleCount - 1,
  );
  return {
    candleCount,
    visibleStart,
    visibleEnd,
    plotStartRatio: left / viewBoxWidth,
    plotEndRatio: (viewBoxWidth - right) / viewBoxWidth,
  };
}

function indexRangeFromDrag(range: DragRange, meta: ChartMeta) {
  const plotRatioWidth = Math.max(meta.plotEndRatio - meta.plotStartRatio, 0.01);
  const startRatio = clamp((range.start - meta.plotStartRatio) / plotRatioWidth, 0, 1);
  const endRatio = clamp((range.end - meta.plotStartRatio) / plotRatioWidth, 0, 1);
  const visibleSpan = Math.max(meta.visibleEnd - meta.visibleStart, 1);
  const start = meta.visibleStart + Math.floor(visibleSpan * startRatio);
  const end = meta.visibleStart + Math.ceil(visibleSpan * endRatio);
  const expanded = expandIndexRange(
    clamp(start, 0, meta.candleCount - 1),
    clamp(end, 0, meta.candleCount - 1),
    meta.candleCount - 1,
  );
  if (expanded.start <= 0 && expanded.end >= meta.candleCount - 1) {
    return null;
  }
  return expanded;
}

export function MarketChartZoomShell({ children }: { children: ReactNode }) {
  const rootRef = useRef<HTMLDivElement>(null);
  const activePointerRef = useRef<number | null>(null);
  const dragStartRef = useRef<number | null>(null);
  const [draftRange, setDraftRange] = useState<DragRange | null>(null);
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const zoomActive = searchParams.has("zoom");

  const relativeX = (clientX: number) => {
    const root = rootRef.current;
    const meta = chartMeta(root);
    if (!root || !meta) {
      return null;
    }
    const rect = root.getBoundingClientRect();
    if (rect.width <= 0) {
      return null;
    }
    return clamp((clientX - rect.left) / rect.width, meta.plotStartRatio, meta.plotEndRatio);
  };

  const navigateWithZoom = (range: DragRange | null) => {
    const params = new URLSearchParams(searchParams.toString());
    if (!range) {
      params.delete("zoom");
    } else {
      params.set("zoom", `${range.start}-${range.end}`);
    }
    const query = params.toString();
    router.push(query ? `${pathname}?${query}` : pathname, { scroll: false });
  };

  useEffect(() => {
    const root = rootRef.current;
    if (!root) {
      return;
    }

    const handlePointerDown = (event: globalThis.PointerEvent) => {
      if (event.button !== 0) {
        return;
      }
      const target = event.target;
      if (target instanceof Element && target.closest("button,a")) {
        return;
      }
      const start = relativeX(event.clientX);
      if (start === null) {
        return;
      }
      activePointerRef.current = event.pointerId;
      dragStartRef.current = start;
      setDraftRange(null);
      root.setPointerCapture?.(event.pointerId);
    };

    const handlePointerMove = (event: globalThis.PointerEvent) => {
      if (activePointerRef.current !== event.pointerId || dragStartRef.current === null) {
        return;
      }
      const current = relativeX(event.clientX);
      if (current === null) {
        return;
      }
      const distance = Math.abs(current - dragStartRef.current);
      if (distance >= minDragDistanceRatio) {
        event.preventDefault();
      }
      setDraftRange(distance >= minDragDistanceRatio ? normalizeRange(dragStartRef.current, current) : null);
    };

    const finishDrag = (event: globalThis.PointerEvent) => {
      if (activePointerRef.current !== event.pointerId || dragStartRef.current === null) {
        return;
      }
      const current = relativeX(event.clientX);
      const start = dragStartRef.current;
      activePointerRef.current = null;
      dragStartRef.current = null;
      setDraftRange(null);
      if (root.hasPointerCapture?.(event.pointerId)) {
        root.releasePointerCapture(event.pointerId);
      }
      if (current === null || Math.abs(current - start) < minDragDistanceRatio) {
        return;
      }
      event.preventDefault();
      const meta = chartMeta(root);
      const indexRange = meta ? indexRangeFromDrag(normalizeRange(start, current), meta) : null;
      navigateWithZoom(indexRange);
    };

    root.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", finishDrag);
    window.addEventListener("pointercancel", finishDrag);

    return () => {
      root.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", finishDrag);
      window.removeEventListener("pointercancel", finishDrag);
    };
  });

  return (
    <div ref={rootRef} data-market-chart-zoom-shell="true" className="relative select-none">
      {children}
      {draftRange ? (
        <div
          className="pointer-events-none absolute inset-y-0 rounded-md border border-blue-500 bg-blue-500/10"
          style={{
            left: `${draftRange.start * 100}%`,
            width: `${Math.max((draftRange.end - draftRange.start) * 100, 0.5)}%`,
          }}
        />
      ) : null}
      {zoomActive ? (
        <button
          type="button"
          onClick={() => navigateWithZoom(null)}
          className="absolute right-3 top-3 rounded-md border border-slate-200 bg-white/95 px-3 py-1 text-xs font-semibold text-slate-700 shadow-sm hover:border-blue-300 hover:text-blue-700"
        >
          전체 보기
        </button>
      ) : null}
    </div>
  );
}
