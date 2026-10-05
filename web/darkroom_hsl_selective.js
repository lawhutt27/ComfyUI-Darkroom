// ComfyUI-Darkroom -- HSL Selective as three hue strips (Hue / Saturation /
// Luminance), replacing a column of 24 sliders.
//
// Each strip is a stock curve controller from darkroom_curve_core.js with eight
// control points at the node's HUE_CENTERS (nodes/hsl_selective.py), each bound
// to the existing FLOAT widget; darkroom_stack.js stacks the three. No Python
// or INPUT_TYPES change: the sliders stay the state (RULE 1).

import { registerCanvasNode } from "./darkroom_canvas_widget.js";
import { createCurveController } from "./darkroom_curve_core.js";
import { createStack } from "./darkroom_stack.js";

const HUES = [
  ["red", 0], ["orange", 30], ["yellow", 60], ["green", 120],
  ["aqua", 180], ["blue", 240], ["purple", 270], ["magenta", 330],
];
const LABEL = { red: "Red", orange: "Ora", yellow: "Yel", green: "Gre", aqua: "Aqu", blue: "Blu", purple: "Pur", magenta: "Mag" };

function stripSpec(suffix, range, unit, tag, preset) {
  return {
    tag, axis: "hue", range, unit, minWidth: 420,
    points: HUES.map(([name, deg]) => ({ x: deg / 360, widget: `${name}_${suffix}`, label: LABEL[name] })),
    ...(preset ? { preset: { widget: "preset", custom: "Custom (manual)",
                             caption: "preset active, strips show the manual offsets only" } } : {}),
  };
}

registerCanvasNode("DarkroomHSLSelective", "AKURATE.DarkroomHSLSelective",
  (node) => createStack([
    { title: "Hue shift", c: createCurveController(node, stripSpec("hue", 30, "°", "HSLHue", false)) },
    { title: "Saturation", c: createCurveController(node, stripSpec("saturation", 100, "", "HSLSat", false)) },
    { title: "Luminance", c: createCurveController(node, stripSpec("luminance", 100, "", "HSLLum", true)) },
  ]),
  { tag: "HSLSelective", minWidth: 420, requireWidget: "red_hue" });
