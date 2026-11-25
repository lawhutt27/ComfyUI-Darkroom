// Mirrors backend/darkroom_web/stack.py. See ARCHITECTURE.md §3.

export type Box = { x: number; y: number; w: number; h: number };
export type Circle = { x: number; y: number; r: number };

export type RecipePartial = Record<string, Record<string, number>>;

export type LayerParams = {
  adjust: RecipePartial;
  look: { preset: string; amount: number };
  lut: { name: string; strength: number };
  film: { stock: string; strength: number };
  style_match: { strength: number; method: string; reference_name: string; data: unknown };
  red_eye: { strength: number; eyes: Circle[] };
  teeth_whiten: { strength: number; regions: Box[] };
  ai_edit: {
    prompt: string; provider: string; model: string; preserve: boolean; box: Box | null; crop: Box;
    feather: number; strength: number; sensitivity: number; input_asset: string; output_asset: string; notes: string;
  };
};

export type LayerType = keyof LayerParams;
export type Layer<T extends LayerType = LayerType> = {
  id: string; type: T; enabled: boolean; name: string; params: LayerParams[T];
};
export type Stack = { version?: number; layers: Layer[] };

export const PIXEL_TYPES: LayerType[] = ["red_eye", "teeth_whiten", "ai_edit"];

export type ProjectInfo = {
  id: string; name: string; width: number; height: number; megapixels: number; bit_depth: number;
  kind: string; format: string; color_space: string; preview: { width: number; height: number };
  camera: Record<string, string | number>; notes: string[]; can_undo: boolean; can_redo: boolean;
};

export type ProjectState = { project: ProjectInfo; stack: Stack; warnings: string[]; sent_prompt?: string; layer_id?: string };

export type Provider = {
  id: string; label: string; available: boolean; sends_pixels_off_device: boolean; needs_mask: boolean;
  default_model: string;
};

export type Health = {
  version: string; optional: Record<string, boolean>; providers: Provider[]; default_provider: string;
  looks: { name: string; description: string }[]; film_stocks: string[]; luts: string[];
  export_formats: Record<string, string>; output_spaces: string[]; social_presets: Record<string, string>;
  preservation_instructions: string; upload_types: string[]; provider_long_edge: number; samples: string[];
};

export type ExportResult = {
  file: string; download: string; width: number; height: number; bits: number; warnings: string[];
  full_resolution: boolean; seconds: number; space: string;
};
