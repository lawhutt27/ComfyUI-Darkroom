// ComfyUI-Darkroom -- stack several canvas controllers into one.
//
// darkroom_canvas_widget.js attaches ONE controller per node at the top of the
// widget stack (RULE 2). A node that wants more than one panel (HSL Selective's
// three strips, Tone Curve's curve + live histogram) stacks them here: sizes
// add up, each part draws at its own y offset, and a gesture belongs to the
// part it started in until it ends.
//
// parts: [{ title?: string, c: controller }]  (controller interface as in
// darkroom_canvas_widget.js: draw, mouse, computeSize, dragging, syncedWidgets)

const TITLE_H = 16;

export function createStack(parts) {
  let active = null;
  let layout = [];

  const height = (p, width) => (p.title ? TITLE_H : 0) + p.c.computeSize(width)[1];

  return {
    parts,
    dragging() { return active !== null && active.c.dragging(); },
    syncedWidgets() { return parts.flatMap((p) => p.c.syncedWidgets()); },
    computeSize(width) { return [width, parts.reduce((s, p) => s + height(p, width), 0)]; },
    draw(ctx, node, width, y) {
      layout = [];
      let yy = y;
      for (const p of parts) {
        const h = height(p, width);
        if (p.title) {
          ctx.save();
          ctx.fillStyle = "#9a9a9a";
          ctx.font = "11px sans-serif";
          ctx.textAlign = "left";
          ctx.fillText(p.title, 12, yy + 12);
          ctx.restore();
        }
        const top = yy + (p.title ? TITLE_H : 0);
        p.c.draw(ctx, node, width, top, h - (p.title ? TITLE_H : 0));
        layout.push({ y0: yy, y1: yy + h, part: p });
        yy += h;
      }
    },
    mouse(event, pos, node) {
      const t = event.type || "";
      if (t.endsWith("down")) {
        const hit = layout.find((l) => pos[1] >= l.y0 && pos[1] < l.y1);
        if (!hit || !hit.part.c.mouse(event, pos, node)) return false;
        active = hit.part;
        return true;
      }
      if (active) {
        const r = active.c.mouse(event, pos, node);
        if (t.endsWith("up")) active = null;
        return r;
      }
      return false;
    },
  };
}
