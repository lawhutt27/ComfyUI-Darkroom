import type { Layer, Stack } from "../types";
import { PIXEL_TYPES } from "../types";
import { typeLabel } from "../util";

type Props = {
  stack: Stack;
  selected: string | null;
  onSelect: (l: Layer) => void;
  onChange: (s: Stack) => void;
};

export function LayersPanel({ stack, selected, onSelect, onChange }: Props) {
  const layers = stack.layers;
  const isPix = (l: Layer) => PIXEL_TYPES.includes(l.type);
  const move = (i: number, d: -1 | 1) => {
    const j = i + d;
    if (j < 0 || j >= layers.length || isPix(layers[i]) !== isPix(layers[j])) return;
    const next = [...layers];
    [next[i], next[j]] = [next[j], next[i]];
    onChange({ ...stack, layers: next });
  };
  return (
    <section className="panel layers">
      <h3>
        Edit stack <span className="muted">{layers.length ? "applied top → bottom" : ""}</span>
      </h3>
      {!layers.length && <p className="muted small">No edits yet. Everything you do appears here and can be toggled, re-tweaked or removed at any time.</p>}
      <ol>
        {layers.map((l, i) => {
          const firstGrade = !isPix(l) && (i === 0 || isPix(layers[i - 1]));
          return (
            <li key={l.id} className={`${l.id === selected ? "sel" : ""} ${l.enabled ? "" : "off"}`}>
              {firstGrade && layers.some(isPix) && <div className="divider">Grading (whole photo)</div>}
              {i === 0 && isPix(l) && <div className="divider">Content fixes (rendered first)</div>}
              <div className="layer-row">
                <input
                  type="checkbox"
                  checked={l.enabled}
                  aria-label={`Toggle ${l.name}`}
                  onChange={(e) => onChange({ ...stack, layers: layers.map((x) => (x.id === l.id ? { ...x, enabled: e.target.checked } : x)) })}
                />
                <button className="layer-name" onClick={() => onSelect(l)} title={l.name}>
                  <span className={`badge b-${l.type}`}>{typeLabel[l.type]}</span>
                  {l.name}
                </button>
                <span className="layer-actions">
                  <button className="icon" onClick={() => move(i, -1)} aria-label="Move up" disabled={i === 0 || isPix(layers[i - 1]) !== isPix(l)}>↑</button>
                  <button className="icon" onClick={() => move(i, 1)} aria-label="Move down" disabled={i === layers.length - 1 || isPix(layers[i + 1]) !== isPix(l)}>↓</button>
                  <button className="icon danger" onClick={() => onChange({ ...stack, layers: layers.filter((x) => x.id !== l.id) })} aria-label={`Delete ${l.name}`}>✕</button>
                </span>
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
