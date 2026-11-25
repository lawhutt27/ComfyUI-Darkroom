import type { Layer, LayerParams, LayerType, Stack } from "./types";
import { PIXEL_TYPES } from "./types";

export const newId = () => "l_" + Math.random().toString(16).slice(2, 10).padEnd(8, "0");

export function makeLayer<T extends LayerType>(type: T, name: string, params: LayerParams[T]): Layer<T> {
  return { id: newId(), type, enabled: true, name, params };
}

/** Insert keeping pixel layers above grade layers (the worker renders them first). */
export function withLayer(stack: Stack, layer: Layer): Stack {
  const layers = [...stack.layers, layer];
  const pix = layers.filter((l) => PIXEL_TYPES.includes(l.type));
  const grade = layers.filter((l) => !PIXEL_TYPES.includes(l.type));
  return { ...stack, layers: [...pix, ...grade] };
}

export function replaceLayer(stack: Stack, id: string, fn: (l: Layer) => Layer): Stack {
  return { ...stack, layers: stack.layers.map((l) => (l.id === id ? fn(l) : l)) };
}

export function firstOf<T extends LayerType>(stack: Stack, type: T, name?: string): Layer<T> | undefined {
  return stack.layers.find((l) => l.type === type && (name === undefined || l.name === name)) as Layer<T> | undefined;
}

export const typeLabel: Record<LayerType, string> = {
  adjust: "Adjust", look: "Look", lut: "LUT", film: "Film", style_match: "Style match",
  red_eye: "Red-eye", teeth_whiten: "Teeth", ai_edit: "AI edit",
};

export const tabFor: Record<LayerType, Tab> = {
  look: "looks", lut: "looks", film: "looks", style_match: "match", adjust: "tone", red_eye: "tone",
  teeth_whiten: "tone", ai_edit: "ai",
};

export type Tab = "looks" | "match" | "tone" | "ai";
export type Tool = null | "ai-box" | "teeth-box" | "eye-click";
