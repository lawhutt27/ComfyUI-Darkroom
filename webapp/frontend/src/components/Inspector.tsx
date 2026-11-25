import { useRef, useState } from "react";
import { api } from "../api";
import type { Box, Circle, Health, Layer, ProjectInfo, ProjectState, Stack } from "../types";
import { firstOf, makeLayer, replaceLayer, withLayer } from "../util";
import type { Tab, Tool } from "../util";
import { Slider } from "./Slider";

type Common = {
  health: Health;
  project: ProjectInfo;
  stack: Stack;
  onStack: (s: Stack) => void;
  onState: (s: ProjectState) => void;
  run: <T>(label: string, fn: () => Promise<T>) => Promise<T | undefined>;
  tool: Tool;
  setTool: (t: Tool) => void;
  selected: Layer | null;
  setSelected: (id: string | null) => void;
};

type Props = Common & { tab: Tab; setTab: (t: Tab) => void; aiBox: Box | null; setAiBox: (b: Box | null) => void; showMask: boolean; setShowMask: (b: boolean) => void };

const TABS: { id: Tab; label: string }[] = [
  { id: "looks", label: "Looks & LUTs" },
  { id: "match", label: "Style Match" },
  { id: "tone", label: "Tone & Retouch" },
  { id: "ai", label: "AI Edit" },
];

export function Inspector(p: Props) {
  return (
    <aside className="inspector">
      <nav className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={p.tab === t.id} className={p.tab === t.id ? "on" : ""} onClick={() => { p.setTab(t.id); p.setTool(null); }}>
            {t.label}
          </button>
        ))}
      </nav>
      <div className="tab-body">
        {p.tab === "looks" && <LooksTab {...p} />}
        {p.tab === "match" && <MatchTab {...p} />}
        {p.tab === "tone" && <ToneTab {...p} />}
        {p.tab === "ai" && <AiTab {...p} />}
      </div>
    </aside>
  );
}

/* ------------------------------------------------------------------ Looks & LUTs */
function LooksTab({ health, stack, onStack, run, setSelected }: Common) {
  const look = firstOf(stack, "look");
  const lut = firstOf(stack, "lut");
  const film = firstOf(stack, "film");
  const [luts, setLuts] = useState(health.luts);
  const fileRef = useRef<HTMLInputElement>(null);
  const pickLook = (name: string) => {
    if (look && look.params.preset === name) return;
    const short = name.replace(/^(IG|Film) \/ /, "");
    if (look) onStack(replaceLayer(stack, look.id, (l) => ({ ...l, name: short, params: { ...(l.params as Layer<"look">["params"]), preset: name } })));
    else {
      const L = makeLayer("look", short, { preset: name, amount: 100 });
      onStack(withLayer(stack, L));
      setSelected(L.id);
    }
  };
  return (
    <div>
      <h4>Quick looks</h4>
      <div className="look-grid">
        {health.looks.map((l) => (
          <button key={l.name} className={`look ${look?.params.preset === l.name ? "on" : ""}`} onClick={() => pickLook(l.name)} title={l.description}>
            <span className="look-name">{l.name.replace(/^(IG|Film) \/ /, "")}</span>
            <span className="look-kind">{l.name.startsWith("IG") ? "Instagram" : "Film"}</span>
          </button>
        ))}
      </div>
      {look && (
        <div className="group">
          <Slider label={`${look.name} amount`} value={look.params.amount} min={0} max={100} unit="%" defaultValue={100}
            onChange={(v) => onStack(replaceLayer(stack, look.id, (l) => ({ ...l, params: { ...(l.params as Layer<"look">["params"]), amount: v } })))} />
          <button className="link" onClick={() => onStack({ ...stack, layers: stack.layers.filter((x) => x.id !== look.id) })}>Remove look</button>
        </div>
      )}

      <h4>.cube LUTs</h4>
      <div className="row">
        <select
          value={lut?.params.name ?? ""}
          onChange={(e) => {
            const name = e.target.value;
            if (!name) return onStack({ ...stack, layers: stack.layers.filter((x) => x.type !== "lut") });
            if (lut) onStack(replaceLayer(stack, lut.id, (l) => ({ ...l, name, params: { ...(l.params as Layer<"lut">["params"]), name } })));
            else onStack(withLayer(stack, makeLayer("lut", name, { name, strength: 100 })));
          }}
          aria-label="LUT"
        >
          <option value="">No LUT</option>
          {luts.map((n) => <option key={n}>{n}</option>)}
        </select>
        <button className="btn" onClick={() => fileRef.current?.click()}>Import .cube</button>
        <input ref={fileRef} type="file" accept=".cube" hidden onChange={async (e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (!f) return;
          const r = await run("Importing LUT", () => api.importLut(f));
          if (r) {
            setLuts(r.luts);
            const L = makeLayer("lut", r.name, { name: r.name, strength: 100 });
            onStack(lut ? replaceLayer(stack, lut.id, () => L) : withLayer(stack, L));
          }
        }} />
      </div>
      {lut && <Slider label="LUT strength" value={lut.params.strength} min={0} max={100} unit="%" defaultValue={100}
        onChange={(v) => onStack(replaceLayer(stack, lut.id, (l) => ({ ...l, params: { ...(l.params as Layer<"lut">["params"]), strength: v } })))} />}
      <p className="small muted">LUTs are applied with tetrahedral interpolation on the full-resolution image at export.</p>

      <h4>Film stock</h4>
      <select
        value={film?.params.stock ?? ""}
        aria-label="Film stock"
        onChange={(e) => {
          const stock = e.target.value;
          if (!stock) return onStack({ ...stack, layers: stack.layers.filter((x) => x.type !== "film") });
          const name = stock.replace(/^(Studio|Darkroom) \/ /, "");
          if (film) onStack(replaceLayer(stack, film.id, (l) => ({ ...l, name, params: { ...(l.params as Layer<"film">["params"]), stock } })));
          else onStack(withLayer(stack, makeLayer("film", name, { stock, strength: 100 })));
        }}
      >
        <option value="">None</option>
        {health.film_stocks.map((n) => <option key={n} value={n}>{n}</option>)}
      </select>
      {film && <Slider label="Film strength" value={film.params.strength} min={0} max={100} unit="%" defaultValue={100}
        onChange={(v) => onStack(replaceLayer(stack, film.id, (l) => ({ ...l, params: { ...(l.params as Layer<"film">["params"]), strength: v } })))} />}
    </div>
  );
}

/* ------------------------------------------------------------------ Style match */
function MatchTab({ project, stack, onStack, onState, run }: Common) {
  const layer = firstOf(stack, "style_match");
  const [method, setMethod] = useState("Colour + tone");
  const [refUrl, setRefUrl] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  return (
    <div>
      <p className="small">
        Upload a photo whose colour and mood you like. Only its colour statistics and tonal curve are transferred, so
        your photo's subject, sharpness and structure stay exactly as they are.
      </p>
      <label className="field">
        Transfer
        <select value={method} onChange={(e) => setMethod(e.target.value)}>
          <option>Colour + tone</option>
          <option>Colour only</option>
        </select>
      </label>
      <button className="btn primary wide" onClick={() => fileRef.current?.click()}>
        {layer ? "Match a different reference…" : "Choose reference photo…"}
      </button>
      <input ref={fileRef} type="file" accept="image/*,.nef,.cr2,.cr3,.dng,.arw,.tif,.tiff" hidden onChange={async (e) => {
        const f = e.target.files?.[0];
        e.target.value = "";
        if (!f) return;
        setRefUrl(URL.createObjectURL(f));
        const st = await run("Matching style", () => api.styleMatch(project.id, f, layer?.params.strength ?? 70, method));
        if (st) onState(st);
      }} />
      {refUrl && <img className="ref-thumb" src={refUrl} alt="Reference" />}
      {layer && (
        <div className="group">
          <p className="small">Matched to <b>{layer.params.reference_name}</b> ({layer.params.method}).</p>
          <Slider label="Match strength" value={layer.params.strength} min={0} max={100} unit="%" defaultValue={70}
            onChange={(v) => onStack(replaceLayer(stack, layer.id, (l) => ({ ...l, params: { ...(l.params as Layer<"style_match">["params"]), strength: v } })))} />
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ Tone & retouch */
const TONE: [string, string, number, number, number][] = [
  // group.key, label, min, max, step
  ["basic.exposure", "Exposure", -3, 3, 0.05],
  ["basic.contrast", "Contrast", -100, 100, 1],
  ["basic.highlights", "Highlights", -100, 100, 1],
  ["basic.shadows", "Shadows", -100, 100, 1],
  ["basic.whites", "Whites", -100, 100, 1],
  ["basic.blacks", "Blacks", -100, 100, 1],
  ["basic.temperature", "Warmth", -100, 100, 1],
  ["basic.tint", "Tint", -100, 100, 1],
  ["basic.vibrance", "Vibrance", -100, 100, 1],
  ["basic.saturation", "Saturation", -100, 100, 1],
  ["effects.clarity", "Clarity", -100, 100, 1],
  ["effects.fade", "Fade", 0, 100, 1],
  ["effects.grain_amount", "Grain", 0, 100, 1],
  ["effects.vignette_amount", "Vignette", -100, 100, 1],
];

function ToneTab({ project, stack, onStack, run, tool, setTool, setSelected }: Common) {
  const tone = firstOf(stack, "adjust", "Tone");
  const redEye = firstOf(stack, "red_eye");
  const teeth = firstOf(stack, "teeth_whiten");
  const [msg, setMsg] = useState("");
  const get = (path: string) => {
    const [g, k] = path.split(".");
    return (tone?.params as Record<string, Record<string, number>> | undefined)?.[g]?.[k] ?? 0;
  };
  const set = (path: string, v: number) => {
    const [g, k] = path.split(".");
    if (!tone) {
      const L = makeLayer("adjust", "Tone", { [g]: { [k]: v } });
      onStack(withLayer(stack, L));
      return;
    }
    onStack(replaceLayer(stack, tone.id, (l) => {
      const params = { ...(l.params as Record<string, Record<string, number>>) };
      params[g] = { ...(params[g] ?? {}), [k]: v };
      return { ...l, params };
    }));
  };
  const detectEyes = async () => {
    const r = await run("Finding eyes", () => api.detect(project.id, "eyes"));
    if (!r) return;
    const eyes = r.shapes as Circle[];
    setMsg(r.faces ? `Found ${r.faces} face${r.faces > 1 ? "s" : ""} and ${eyes.length} eyes.` : "No faces found. Click on each eye instead.");
    if (!eyes.length) return setTool("eye-click");
    if (redEye) onStack(replaceLayer(stack, redEye.id, (l) => ({ ...l, params: { ...(l.params as Layer<"red_eye">["params"]), eyes } })));
    else {
      const L = makeLayer("red_eye", "Red-eye fix", { strength: 100, eyes });
      onStack(withLayer(stack, L));
      setSelected(L.id);
    }
  };
  const detectMouth = async () => {
    const r = await run("Finding mouths", () => api.detect(project.id, "mouths"));
    if (!r) return;
    const regions = r.shapes as Box[];
    setMsg(r.faces ? `Found ${regions.length} mouth${regions.length > 1 ? "s" : ""}.` : "No faces found. Drag a box around the teeth instead.");
    if (!regions.length) return setTool("teeth-box");
    if (teeth) onStack(replaceLayer(stack, teeth.id, (l) => ({ ...l, params: { ...(l.params as Layer<"teeth_whiten">["params"]), regions } })));
    else {
      const L = makeLayer("teeth_whiten", "Whiten teeth", { strength: 35, regions });
      onStack(withLayer(stack, L));
      setSelected(L.id);
    }
  };
  return (
    <div>
      <h4>Tone</h4>
      {TONE.map(([path, label, min, max, step]) => (
        <Slider key={path} label={label} value={get(path)} min={min} max={max} step={step} defaultValue={0} onChange={(v) => set(path, v)} />
      ))}

      <h4>Red-eye</h4>
      <div className="row">
        <button className="btn" onClick={detectEyes}>Detect & fix</button>
        <button className={`btn ${tool === "eye-click" ? "active" : ""}`} onClick={() => setTool(tool === "eye-click" ? null : "eye-click")}>
          {tool === "eye-click" ? "Done adding" : "Click on eyes"}
        </button>
      </div>
      {redEye && (
        <>
          <Slider label="Red-eye strength" value={redEye.params.strength} min={0} max={100} unit="%" defaultValue={100}
            onChange={(v) => onStack(replaceLayer(stack, redEye.id, (l) => ({ ...l, params: { ...(l.params as Layer<"red_eye">["params"]), strength: v } })))} />
          <p className="small muted">{redEye.params.eyes.length} eye{redEye.params.eyes.length === 1 ? "" : "s"} · only strongly red pupil pixels are changed</p>
        </>
      )}

      <h4>Teeth whitening</h4>
      <div className="row">
        <button className="btn" onClick={detectMouth}>Detect mouth</button>
        <button className={`btn ${tool === "teeth-box" ? "active" : ""}`} onClick={() => setTool(tool === "teeth-box" ? null : "teeth-box")}>
          {tool === "teeth-box" ? "Drawing… (drag)" : "Draw box"}
        </button>
      </div>
      {teeth && (
        <>
          <Slider label="Whitening" value={teeth.params.strength} min={0} max={100} unit="%" defaultValue={35}
            hint="Removes yellow and lifts brightness slightly; texture, lips and skin are left alone."
            onChange={(v) => onStack(replaceLayer(stack, teeth.id, (l) => ({ ...l, params: { ...(l.params as Layer<"teeth_whiten">["params"]), strength: v } })))} />
          <p className="small muted">Subtle is best: 25–45% looks natural.</p>
        </>
      )}
      {msg && <p className="small">{msg}</p>}
    </div>
  );
}

/* ------------------------------------------------------------------ AI edit */
function AiTab({ health, project, stack, onStack, onState, run, tool, setTool, selected, setSelected, aiBox, setAiBox, showMask, setShowMask }: Props) {
  const [prompt, setPrompt] = useState("");
  const [provider, setProvider] = useState(health.default_provider);
  const [preserve, setPreserve] = useState(true);
  const [lastPrompt, setLastPrompt] = useState("");
  const prov = health.providers.find((x) => x.id === provider);
  const aiLayer = selected?.type === "ai_edit" ? (selected as Layer<"ai_edit">) : (stack.layers.filter((l) => l.type === "ai_edit").slice(-1)[0] as Layer<"ai_edit"> | undefined);
  const notes = (() => {
    try { return aiLayer ? JSON.parse(aiLayer.params.notes || "{}") : {}; } catch { return {}; }
  })();
  const go = async () => {
    const st = await run(`Asking ${prov?.label ?? provider}`, () =>
      api.aiEdit(project.id, { provider, prompt, preserve, box: aiBox, feather: 40, sensitivity: 50, strength: 100 }));
    if (st) {
      onState(st);
      setLastPrompt(st.sent_prompt ?? "");
      setSelected(st.layer_id ?? null);
      setAiBox(null);
      setTool(null);
    }
  };
  const setParam = (k: "strength" | "feather" | "sensitivity" | "preserve", v: number | boolean) =>
    aiLayer && onStack(replaceLayer(stack, aiLayer.id, (l) => ({ ...l, params: { ...(l.params as Layer<"ai_edit">["params"]), [k]: v } })));
  return (
    <div>
      <p className="small">
        Describe a <b>small, specific</b> change. The rest of the photo stays at full resolution: the AI's result is
        blended back only where it actually changed something.
      </p>
      <label className="field">
        Provider
        <select value={provider} onChange={(e) => setProvider(e.target.value)}>
          {health.providers.map((x) => (
            <option key={x.id} value={x.id} disabled={!x.available}>
              {x.label}{x.available ? "" : " (set API key)"}
            </option>
          ))}
        </select>
      </label>
      <textarea
        className="prompt"
        placeholder={prov?.needs_mask ? "e.g. remove the sign (drag a box over it first)" : "e.g. remove the stray person on the left, keep everything else identical"}
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        rows={3}
      />
      <div className="row">
        <button className={`btn ${tool === "ai-box" ? "active" : ""}`} onClick={() => setTool(tool === "ai-box" ? null : "ai-box")}>
          {aiBox ? "Redraw target box" : tool === "ai-box" ? "Drag on the photo…" : "Draw target box"}
        </button>
        {aiBox && <button className="link" onClick={() => setAiBox(null)}>Whole photo</button>}
      </div>
      <label className="check">
        <input type="checkbox" checked={preserve} onChange={(e) => setPreserve(e.target.checked)} /> Preserve original details
      </label>
      <details className="small">
        <summary>What gets sent</summary>
        <p>
          {prov?.sends_pixels_off_device
            ? `A copy of ${aiBox ? "the target area plus some context" : "the photo"}, at most ${health.provider_long_edge} px, is sent to ${prov.label}. The original file and its metadata are never sent.`
            : "Nothing leaves this computer. The offline tool fills the box from its surroundings and does not read the prompt."}
        </p>
        {preserve && <p className="muted">Added to your prompt: “{health.preservation_instructions}”</p>}
      </details>
      <button className="btn primary wide" disabled={!prompt.trim() || (prov?.needs_mask && !aiBox) || !prov?.available} onClick={go}>
        Run AI edit
      </button>

      {aiLayer && (
        <div className="group">
          <h4>{aiLayer.name}</h4>
          <Slider label="Strength" value={aiLayer.params.strength} min={0} max={100} unit="%" defaultValue={100} onChange={(v) => setParam("strength", v)} />
          <Slider label="Edge feather" value={aiLayer.params.feather} min={0} max={200} unit=" px" defaultValue={40} onChange={(v) => setParam("feather", v)}
            hint="Soft edge of the blend, in full-resolution pixels" />
          <label className="check">
            <input type="checkbox" checked={aiLayer.params.preserve} onChange={(e) => setParam("preserve", e.target.checked)} /> Only keep what changed
          </label>
          {aiLayer.params.preserve && (
            <Slider label="Change sensitivity" value={aiLayer.params.sensitivity} min={0} max={100} defaultValue={50} onChange={(v) => setParam("sensitivity", v)}
              hint="Higher picks up subtler changes; lower keeps more of the original" />
          )}
          <label className="check">
            <input type="checkbox" checked={showMask} onChange={(e) => setShowMask(e.target.checked)} /> Show blend mask
          </label>
          <p className="small muted">
            {notes.provider} · {notes.model} · sent {notes.sent_px?.join(" × ")} px · {notes.seconds}s
            {typeof notes.alignment?.aligned === "object" ? " · re-aligned" : ""}
          </p>
          {lastPrompt && (
            <details className="small">
              <summary>Prompt that was sent</summary>
              <p className="muted pre">{lastPrompt}</p>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
