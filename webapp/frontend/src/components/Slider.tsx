type Props = {
  label: string; value: number; min: number; max: number; step?: number; unit?: string;
  onChange: (v: number) => void; disabled?: boolean; hint?: string; defaultValue?: number;
};

export function Slider({ label, value, min, max, step = 1, unit = "", onChange, disabled, hint, defaultValue }: Props) {
  const shown = step < 1 ? value.toFixed(2) : Math.round(value).toString();
  return (
    <label className="slider" title={hint}>
      <span className="slider-row">
        <span className="slider-label">{label}</span>
        <span
          className="slider-value"
          onDoubleClick={() => defaultValue !== undefined && onChange(defaultValue)}
          title={defaultValue !== undefined ? "Double-click to reset" : undefined}
        >
          {value > 0 && min < 0 ? "+" : ""}
          {shown}
          {unit}
        </span>
      </span>
      <input
        type="range" min={min} max={max} step={step} value={value} disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
        onDoubleClick={() => defaultValue !== undefined && onChange(defaultValue)}
      />
    </label>
  );
}
