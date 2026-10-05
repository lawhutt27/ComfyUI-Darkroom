// ComfyUI-Darkroom -- live RGB histogram panel (pixel bridge, decisions.md 2026-10-05).
//
// After the graph runs, the node's INPUT proxy (256 px, uint8) is fetched from
// /darkroom/pixels. While the user drags, the panel asks /darkroom/lut for the
// node's exact per-channel table at the current widget values (harvested from
// the node's own execute(), so the scope is exact for separable nodes -- see
// pixel_bridge.py) and re-grades the proxy in the browser: no re-run, no wait.
// One LUT request in flight at a time; the latest values always win.

import { api } from "../../scripts/api.js";

const PANEL_H = 84, PAD = 10, BINS = 128;
const panels = new Set();

function refreshAll() { for (const p of panels) p.fetchProxy(); }
api.addEventListener("execution_success", refreshAll);
api.addEventListener("executing", (e) => { if (e.detail == null) refreshAll(); });

export function createLiveHistogram(node, opts) {
  const nodeType = opts.nodeType;
  const panel = {
    proxy: null, lut: null, lutKey: null, inflight: false, err: null,

    async fetchProxy() {
      try {
        const r = await api.fetchApi(`/darkroom/pixels?node_id=${encodeURIComponent(node.id)}&tap=in`);
        if (r.status !== 200) { this.proxy = null; node.setDirtyCanvas(true, true); return; }
        const buf = new Uint8Array(await r.arrayBuffer());
        this.proxy = { rgb: buf, n: buf.length / 3 };
        node.setDirtyCanvas(true, true);
      } catch (_e) { /* the panel just stays empty */ }
    },

    values() {
      const v = {};
      for (const w of node.widgets || []) {
        if (w.serialize === false || w.name == null) continue;
        if (["number", "combo", "toggle"].includes(w.type) || typeof w.value === "number") v[w.name] = w.value;
      }
      return v;
    },

    async requestLut() {
      const values = this.values();
      const key = JSON.stringify(values);
      if (key === this.lutKey || this.inflight) return;
      this.inflight = true;
      try {
        const r = await api.fetchApi("/darkroom/lut", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ node: nodeType, values }),
        });
        if (r.status === 200) {
          this.lut = new Uint8Array(await r.arrayBuffer());
          this.err = null;
        } else {
          this.err = "scope unavailable";
        }
        this.lutKey = key;
      } catch (_e) {
        this.err = "scope unavailable";
        this.lutKey = key;
      } finally {
        this.inflight = false;
        node.setDirtyCanvas(true, true);
        if (JSON.stringify(this.values()) !== this.lutKey) this.requestLut();   // latest wins
      }
    },

    // --- canvas controller interface ---
    dragging() { return false; },
    syncedWidgets() { return []; },
    mouse() { return false; },
    computeSize(width) { return [width, PANEL_H]; },
    draw(ctx, _node, width, y) {
      const x0 = PAD, w = Math.max(1, width - PAD * 2), h = PANEL_H - 8;
      ctx.save();
      ctx.fillStyle = "#121212";
      ctx.fillRect(x0, y, w, h);
      ctx.font = "11px sans-serif";
      ctx.textAlign = "center";
      if (!this.proxy) {
        ctx.fillStyle = "#777";
        ctx.fillText("run the graph once to see the live histogram", x0 + w / 2, y + h / 2 + 4);
        ctx.restore();
        return;
      }
      this.requestLut();
      const src = this.proxy.rgb, n = this.proxy.n, lut = this.lut;
      const before = [new Uint32Array(BINS), new Uint32Array(BINS), new Uint32Array(BINS)];
      const after = [new Uint32Array(BINS), new Uint32Array(BINS), new Uint32Array(BINS)];
      const shift = 256 / BINS;
      for (let i = 0; i < n; i++) {
        for (let c = 0; c < 3; c++) {
          const v = src[i * 3 + c];
          before[c][(v / shift) | 0]++;
          if (lut) after[c][(lut[v * 3 + c] / shift) | 0]++;
        }
      }
      let peak = 1;
      for (const set of lut ? [before, after] : [before])
        for (const ch of set) for (let b = 1; b < BINS - 1; b++) peak = Math.max(peak, ch[b]);
      const colours = ["rgba(255,80,80,", "rgba(80,220,80,", "rgba(90,140,255,"];
      const plot = (hist, alpha, fill) => {
        for (let c = 0; c < 3; c++) {
          ctx.beginPath();
          ctx.moveTo(x0, y + h);
          for (let b = 0; b < BINS; b++) {
            const px = x0 + (b + 0.5) / BINS * w;
            ctx.lineTo(px, y + h - Math.min(1, hist[c][b] / peak) * (h - 2));
          }
          ctx.lineTo(x0 + w, y + h);
          if (fill) { ctx.fillStyle = colours[c] + alpha + ")"; ctx.fill(); }
          else { ctx.strokeStyle = colours[c] + alpha + ")"; ctx.lineWidth = 1; ctx.stroke(); }
        }
      };
      plot(before, 0.35, false);              // input: thin outline
      if (lut) plot(after, 0.28, true);       // graded: filled
      ctx.fillStyle = "#8a8a8a";
      ctx.textAlign = "right";
      ctx.fillText(this.err || (lut ? "live, exact" : "..."), x0 + w - 4, y + 12);
      ctx.restore();
    },
  };
  panels.add(panel);
  const origRemoved = node.onRemoved;
  node.onRemoved = function () { panels.delete(panel); return origRemoved ? origRemoved.apply(this, arguments) : undefined; };
  panel.fetchProxy();          // a run from before this page load may still be cached
  return panel;
}
