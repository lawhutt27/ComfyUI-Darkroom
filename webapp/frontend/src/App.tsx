import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { ExportDialog } from "./components/ExportDialog";
import { Inspector } from "./components/Inspector";
import { LayersPanel } from "./components/LayersPanel";
import { Viewer } from "./components/Viewer";
import type { ViewMode } from "./components/Viewer";
import type { Box, Circle, Health, Layer, ProjectInfo, ProjectState, Stack } from "./types";
import { firstOf, makeLayer, replaceLayer, tabFor, withLayer } from "./util";
import type { Tab, Tool } from "./util";

const COMMIT_DELAY = 500;

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [project, setProject] = useState<ProjectInfo | null>(null);
  const [stack, setStack] = useState<Stack>({ layers: [] });
  const [after, setAfter] = useState<string | null>(null);
  const [renderMs, setRenderMs] = useState(0);
  const [rendering, setRendering] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [tab, setTab] = useState<Tab>("looks");
  const [tool, setTool] = useState<Tool>(null);
  const [mode, setMode] = useState<ViewMode>("split");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [aiBox, setAiBox] = useState<Box | null>(null);
  const [showMask, setShowMask] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  const renderCtl = useRef<AbortController | null>(null);
  const commitTimer = useRef<number | undefined>(undefined);
  const pendingStack = useRef<Stack | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.health().then(setHealth).catch((e) => setError(`Can't reach the worker: ${e.message}`));
  }, []);

  const run = useCallback(async <T,>(label: string, fn: () => Promise<T>): Promise<T | undefined> => {
    setBusy(label);
    setError("");
    try {
      return await fn();
    } catch (e) {
      setError((e as Error).message);
      return undefined;
    } finally {
      setBusy("");
    }
  }, []);

  // ---- rendering: latest stack wins, older requests are aborted ----
  const renderStack = useCallback(async (pid: string, s: Stack) => {
    renderCtl.current?.abort();
    const ctl = new AbortController();
    renderCtl.current = ctl;
    setRendering(true);
    try {
      const { blob, ms } = await api.render(pid, s, ctl.signal);
      const url = URL.createObjectURL(blob);
      setAfter((old) => {
        if (old) URL.revokeObjectURL(old);
        return url;
      });
      setRenderMs(ms);
    } catch (e) {
      if ((e as Error).name !== "AbortError") setError((e as Error).message);
    } finally {
      if (renderCtl.current === ctl) setRendering(false);
    }
  }, []);

  const applyState = useCallback((st: ProjectState, rerender = true) => {
    setProject(st.project);
    setStack(st.stack);
    setWarnings(st.warnings ?? []);
    if (rerender) renderStack(st.project.id, st.stack);
  }, [renderStack]);

  // ---- every edit: render now, commit to history once the user pauses ----
  const flushCommit = useCallback(async () => {
    window.clearTimeout(commitTimer.current);
    const s = pendingStack.current;
    if (!s || !project) return;
    pendingStack.current = null;
    try {
      const st = await api.putStack(project.id, s);
      setProject(st.project);
      setWarnings(st.warnings ?? []);
      if (!pendingStack.current) setStack(st.stack);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [project]);

  const onStack = useCallback((s: Stack) => {
    if (!project) return;
    setStack(s);
    renderStack(project.id, s);
    pendingStack.current = s;
    window.clearTimeout(commitTimer.current);
    commitTimer.current = window.setTimeout(flushCommit, COMMIT_DELAY);
  }, [project, renderStack, flushCommit]);

  const open = async (file: File) => {
    const st = await run(`Opening ${file.name}`, () => api.upload(file));
    if (st) {
      setAfter(null);
      setSelectedId(null);
      setAiBox(null);
      applyState(st);
    }
  };

  const openSample = async (name: string) => {
    const st = await run(`Opening sample`, () => api.openSample(name));
    if (st) applyState(st);
  };

  const undoRedo = async (which: "undo" | "redo") => {
    if (!project) return;
    await flushCommit();
    const st = await run(which === "undo" ? "Undo" : "Redo", () => api[which](project.id));
    if (st) applyState(st);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || (e.target as HTMLElement).tagName === "TEXTAREA") return;
      if (e.key.toLowerCase() === "z") {
        e.preventDefault();
        undoRedo(e.shiftKey ? "redo" : "undo");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const selected = stack.layers.find((l) => l.id === selectedId) ?? null;

  const onBox = (b: Box) => {
    if (tool === "ai-box") {
      setAiBox(b);
      setTool(null);
    } else if (tool === "teeth-box") {
      const t = firstOf(stack, "teeth_whiten");
      if (t) onStack(replaceLayer(stack, t.id, (l) => ({ ...l, params: { ...(l.params as Layer<"teeth_whiten">["params"]), regions: [...(l.params as Layer<"teeth_whiten">["params"]).regions, b] } })));
      else onStack(withLayer(stack, makeLayer("teeth_whiten", "Whiten teeth", { strength: 35, regions: [b] })));
      setTool(null);
    }
  };
  const onEye = (c: Circle) => {
    const r = firstOf(stack, "red_eye");
    if (r) onStack(replaceLayer(stack, r.id, (l) => ({ ...l, params: { ...(l.params as Layer<"red_eye">["params"]), eyes: [...(l.params as Layer<"red_eye">["params"]).eyes, c] } })));
    else onStack(withLayer(stack, makeLayer("red_eye", "Red-eye fix", { strength: 100, eyes: [c] })));
  };

  // shapes shown on the photo for the current tab
  const boxes: { box: Box; kind: "ai" | "teeth" }[] = [];
  let eyes: Circle[] = [];
  if (tab === "ai" && aiBox) boxes.push({ box: aiBox, kind: "ai" });
  if (tab === "tone") {
    firstOf(stack, "teeth_whiten")?.params.regions.forEach((b) => boxes.push({ box: b, kind: "teeth" }));
    eyes = firstOf(stack, "red_eye")?.params.eyes ?? [];
  }
  const aiSel = tab === "ai" ? (selected?.type === "ai_edit" ? selected : stack.layers.filter((l) => l.type === "ai_edit").slice(-1)[0]) : undefined;
  const maskUrl = showMask && project && aiSel ? api.maskUrl(project.id, aiSel.id, JSON.stringify(aiSel.params)) : null;

  if (!health) {
    return <div className="splash">{error ? <p className="error">{error}</p> : <p>Starting Darkroom…</p>}</div>;
  }

  return (
    <div
      className={`app ${dragOver ? "drag" : ""}`}
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        const f = e.dataTransfer.files?.[0];
        if (f) open(f);
      }}
    >
      <header className="topbar">
        <div className="brand">◐ Darkroom</div>
        {project && (
          <div className="file">
            <b>{project.name}</b>
            <span className="muted">{project.width} × {project.height} px · {project.megapixels} MP · {project.format} · {project.bit_depth}-bit</span>
          </div>
        )}
        <div className="spacer" />
        {project && (
          <>
            <div className="seg" role="group" aria-label="View">
              {(["split", "before", "after"] as ViewMode[]).map((m) => (
                <button key={m} className={mode === m ? "on" : ""} onClick={() => setMode(m)}>
                  {m === "split" ? "Before | After" : m === "before" ? "Original" : "Edited"}
                </button>
              ))}
            </div>
            <button className="btn" onClick={() => undoRedo("undo")} disabled={!project.can_undo} title="Undo (Ctrl+Z)">↶ Undo</button>
            <button className="btn" onClick={() => undoRedo("redo")} disabled={!project.can_redo} title="Redo (Ctrl+Shift+Z)">↷ Redo</button>
            <button className="btn" onClick={() => fileRef.current?.click()}>Open…</button>
            <button className="btn primary" onClick={async () => { await flushCommit(); setExporting(true); }}>
              Export Full Resolution · {project.width} × {project.height}
            </button>
          </>
        )}
        <input ref={fileRef} type="file" hidden accept={health.upload_types.join(",")} onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (f) open(f);
        }} />
      </header>

      {!project ? (
        <main className="empty">
          <div className="drop" onClick={() => fileRef.current?.click()} role="button" tabIndex={0}>
            <div className="drop-icon">⬆</div>
            <h1>Drop a photo to start</h1>
            <p>RAW (NEF, CR2, CR3, DNG, ARW…), TIFF, PNG or JPEG. The original is never modified, and exports keep its full resolution.</p>
            <button className="btn primary">Choose a photo</button>
          </div>
          {health.samples.length > 0 && (
            <p className="samples">
              or try a sample:{" "}
              {health.samples.map((s) => (
                <button key={s} className="link" onClick={() => openSample(s)}>{s}</button>
              ))}
            </p>
          )}
          {busy && <p className="busy-line">{busy}…</p>}
          {error && <p className="error">{error}</p>}
        </main>
      ) : (
        <main className="workspace">
          <div className="left">
            <LayersPanel
              stack={stack}
              selected={selectedId}
              onSelect={(l) => { setSelectedId(l.id); setTab(tabFor[l.type]); }}
              onChange={onStack}
            />
            <section className="panel info">
              <h3>Photo</h3>
              <dl>
                <dt>Size</dt><dd>{project.width} × {project.height} px</dd>
                <dt>Source</dt><dd>{project.format} ({project.kind}), {project.bit_depth}-bit</dd>
                <dt>Colour</dt><dd>{project.color_space}</dd>
                {Object.entries(project.camera).map(([k, v]) => (
                  <Fragment key={k}><dt>{k.replace(/_/g, " ")}</dt><dd>{String(v)}</dd></Fragment>
                ))}
              </dl>
            </section>
          </div>
          <div className="center">
            <Viewer
              beforeUrl={api.beforeUrl(project.id)}
              afterUrl={after}
              aspect={project.preview.width / project.preview.height}
              mode={mode}
              tool={tool}
              boxes={boxes}
              eyes={eyes}
              maskUrl={maskUrl}
              rendering={rendering}
              onBox={onBox}
              onEye={onEye}
            />
            <footer className="statusbar">
              <span>Preview {project.preview.width} × {project.preview.height} (display only)</span>
              <span>Export {project.width} × {project.height} · float32 pipeline</span>
              <span>{renderMs ? `render ${renderMs} ms` : ""}</span>
              {tool && <span className="hint">{tool === "eye-click" ? "Click the centre of each red eye" : "Drag a box on the photo"} · <button className="link" onClick={() => setTool(null)}>cancel</button></span>}
              {busy && <span className="busy">{busy}…</span>}
              {error && <span className="error" onClick={() => setError("")}>{error} ✕</span>}
              {!error && warnings.length > 0 && <span className="warn" title={warnings.join("\n")}>{warnings[0]}</span>}
            </footer>
          </div>
          <Inspector
            health={health}
            project={project}
            stack={stack}
            onStack={onStack}
            onState={applyState}
            run={run}
            tool={tool}
            setTool={setTool}
            selected={selected}
            setSelected={setSelectedId}
            tab={tab}
            setTab={setTab}
            aiBox={aiBox}
            setAiBox={setAiBox}
            showMask={showMask}
            setShowMask={setShowMask}
          />
        </main>
      )}
      {exporting && project && <ExportDialog project={project} health={health} onClose={() => setExporting(false)} />}
    </div>
  );
}
