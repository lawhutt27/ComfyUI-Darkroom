import { useEffect, useRef, useState } from "react";
import type { Box, Circle } from "../types";
import type { Tool } from "../util";

export type ViewMode = "split" | "before" | "after";

type Props = {
  beforeUrl: string;
  afterUrl: string | null;
  aspect: number; // width / height
  mode: ViewMode;
  tool: Tool;
  boxes: { box: Box; kind: "ai" | "teeth" }[];
  eyes: Circle[];
  maskUrl: string | null;
  rendering: boolean;
  onBox: (b: Box) => void;
  onEye: (c: Circle) => void;
};

const clamp = (v: number) => Math.min(1, Math.max(0, v));

export function Viewer({ beforeUrl, afterUrl, aspect, mode, tool, boxes, eyes, maskUrl, rendering, onBox, onEye }: Props) {
  const stageRef = useRef<HTMLDivElement>(null);
  const frameRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [split, setSplit] = useState(0.5);
  const [drag, setDrag] = useState<null | { x0: number; y0: number; x1: number; y1: number }>(null);
  const draggingSplit = useRef(false);

  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      const W = el.clientWidth - 32, H = el.clientHeight - 32;
      const w = Math.min(W, H * aspect);
      setSize({ w: Math.max(0, w), h: Math.max(0, w / aspect) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [aspect]);

  const norm = (e: React.PointerEvent) => {
    const r = frameRef.current!.getBoundingClientRect();
    return { x: clamp((e.clientX - r.left) / r.width), y: clamp((e.clientY - r.top) / r.height) };
  };

  const onDown = (e: React.PointerEvent) => {
    if (!tool) return;
    const p = norm(e);
    if (tool === "eye-click") {
      onEye({ x: p.x, y: p.y, r: 0.012 }); // r is a fraction of the long edge
      return;
    }
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    setDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y });
  };
  const onMove = (e: React.PointerEvent) => {
    if (draggingSplit.current) {
      setSplit(norm(e).x);
      return;
    }
    if (drag) {
      const p = norm(e);
      setDrag({ ...drag, x1: p.x, y1: p.y });
    }
  };
  const onUp = () => {
    draggingSplit.current = false;
    if (drag) {
      const b = { x: Math.min(drag.x0, drag.x1), y: Math.min(drag.y0, drag.y1), w: Math.abs(drag.x1 - drag.x0), h: Math.abs(drag.y1 - drag.y0) };
      setDrag(null);
      if (b.w > 0.005 && b.h > 0.005) onBox(b);
    }
  };

  const after = afterUrl ?? beforeUrl;
  const pct = (v: number) => `${v * 100}%`;
  const showSplit = mode === "split";
  return (
    <div className="stage" ref={stageRef}>
      <div
        className={`frame ${tool ? "drawing" : ""}`}
        ref={frameRef}
        style={{ width: size.w, height: size.h }}
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        data-testid="viewer"
      >
        <img className="layer-img" src={mode === "before" ? beforeUrl : after} alt="Edited photo preview" draggable={false} />
        {showSplit && (
          <img
            className="layer-img"
            src={beforeUrl}
            alt="Original"
            draggable={false}
            style={{ clipPath: `inset(0 ${pct(1 - split)} 0 0)` }}
          />
        )}
        {maskUrl && (
          <div className="layer-img mask" style={{ maskImage: `url("${maskUrl}")`, WebkitMaskImage: `url("${maskUrl}")` }} aria-hidden />
        )}
        {showSplit && (
          <div
            className="split-handle"
            style={{ left: pct(split), pointerEvents: tool ? "none" : undefined }}
            onPointerDown={(e) => {
              e.stopPropagation();
              draggingSplit.current = true;
              frameRef.current?.setPointerCapture(e.pointerId);
            }}
            role="slider"
            aria-label="Before/after split"
            aria-valuenow={Math.round(split * 100)}
          >
            <span className="split-tag left">Before</span>
            <span className="split-knob">⟷</span>
            <span className="split-tag right">After</span>
          </div>
        )}
        {boxes.map(({ box, kind }, i) => (
          <div key={i} className={`box box-${kind}`} style={{ left: pct(box.x), top: pct(box.y), width: pct(box.w), height: pct(box.h) }} />
        ))}
        {eyes.map((c, i) => {
          const rpx = c.r * Math.max(size.w, size.h);
          return (
            <div key={i} className="eye-ring" style={{ left: `calc(${pct(c.x)} - ${rpx}px)`, top: `calc(${pct(c.y)} - ${rpx}px)`, width: 2 * rpx, height: 2 * rpx }} />
          );
        })}
        {drag && (
          <div
            className="box box-drawing"
            style={{ left: pct(Math.min(drag.x0, drag.x1)), top: pct(Math.min(drag.y0, drag.y1)), width: pct(Math.abs(drag.x1 - drag.x0)), height: pct(Math.abs(drag.y1 - drag.y0)) }}
          />
        )}
        {rendering && <div className="rendering">Rendering…</div>}
      </div>
    </div>
  );
}
