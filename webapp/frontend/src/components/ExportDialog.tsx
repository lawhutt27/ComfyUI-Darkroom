import { useState } from "react";
import { api } from "../api";
import type { ExportResult, Health, ProjectInfo } from "../types";

type Props = { project: ProjectInfo; health: Health; onClose: () => void };

const SOCIAL_DIMS: Record<string, (w: number, h: number) => [number, number]> = {
  ig_portrait: () => [1080, 1350],
  ig_square: () => [1080, 1080],
  ig_landscape: () => [1080, 566],
  long_1080: (w, h) => (w >= h ? [1080, Math.round((h * 1080) / w)] : [Math.round((w * 1080) / h), 1080]),
};

export function ExportDialog({ project, health, onClose }: Props) {
  const [format, setFormat] = useState("tiff16");
  const [space, setSpace] = useState(health.output_spaces[0]);
  const [quality, setQuality] = useState(97);
  const [meta, setMeta] = useState(true);
  const [social, setSocial] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [res, setRes] = useState<ExportResult | null>(null);

  const [ow, oh] = social ? SOCIAL_DIMS[social](project.width, project.height) : [project.width, project.height];
  const fmt = social ? "jpeg" : format;
  const run = async () => {
    setBusy(true);
    setErr("");
    setRes(null);
    try {
      setRes(await api.export(project.id, { format: fmt, output_space: space, quality, keep_metadata: meta, social: social || null }));
    } catch (e) {
      setErr(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Export">
        <h2>Export</h2>
        <div className="dims">
          <span className="big">{ow.toLocaleString()} × {oh.toLocaleString()} px</span>
          <span className={social ? "warn-chip" : "ok-chip"}>{social ? "resized for social" : "100% original resolution"}</span>
        </div>
        <label className="field">
          Size
          <select value={social} onChange={(e) => setSocial(e.target.value)}>
            <option value="">Full resolution (master)</option>
            {Object.entries(health.social_presets).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </label>
        {!social && (
          <>
            <label className="field">
              Format
              <select value={format} onChange={(e) => setFormat(e.target.value)}>
                {Object.entries(health.export_formats).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
            </label>
            <label className="field">
              Colour space
              <select value={space} onChange={(e) => setSpace(e.target.value)}>
                {health.output_spaces.map((s) => <option key={s}>{s}</option>)}
              </select>
            </label>
          </>
        )}
        {fmt === "jpeg" && (
          <label className="field">
            JPEG quality {quality}
            <input type="range" min={95} max={100} value={quality} onChange={(e) => setQuality(Number(e.target.value))} />
          </label>
        )}
        <label className="check">
          <input type="checkbox" checked={meta} onChange={(e) => setMeta(e.target.checked)} /> Keep camera metadata (EXIF)
        </label>
        {fmt !== "tiff16" && !social && (
          <p className="note">This format stores 8 bits{fmt === "jpeg" ? " and is lossy" : ""} or less than the 16-bit master. Keep a TIFF master for re-editing.</p>
        )}
        {err && <p className="error">{err}</p>}
        {res && (
          <div className="result">
            <a className="btn primary" href={res.download} download>
              Download {res.file}
            </a>
            <p className="small">
              {res.width} × {res.height} px · {res.bits}-bit · {res.space} · rendered in {res.seconds}s
            </p>
            {res.warnings.map((w, i) => <p key={i} className="note">{w}</p>)}
          </div>
        )}
        <div className="modal-actions">
          <button className="btn" onClick={onClose}>Close</button>
          <button className="btn primary" onClick={run} disabled={busy}>
            {busy ? "Rendering full resolution…" : "Export"}
          </button>
        </div>
      </div>
    </div>
  );
}
