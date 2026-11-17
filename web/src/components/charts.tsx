// Small, dependency-free SVG charts in Zentral's visual language.
//
// Rules (from the data-visualisation guidance this follows):
// - one axis, recessive hairline grid, 2px lines, >= 8px markers with a surface ring;
// - bars at most 24px thick, 4px rounded data-end, square at the baseline, value at the tip;
// - a legend whenever there are two or more series; identity is never colour alone;
// - every value reachable without hovering (labels or the table view);
// - text uses text colours, never the series colour; labels are set with React text nodes.
// Geometry is SVG attributes, never inline styles, so the strict CSP holds.

import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry?.contentRect.width ?? 0)));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

/** A clean upper bound for an axis: 1, 2, 5 × 10^n. */
export function niceMax(value: number): number {
  if (value <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 5, 10].find((m) => m * power >= value) ?? 10;
  return step * power;
}

export function compact(value: number): string {
  if (value >= 10_000) return `${Math.round(value / 100) / 10}K`;
  return value.toLocaleString();
}

export interface Series {
  key: string;
  label: string;
  className: string; // series colour comes from CSS (light and dark steps)
  values: number[];
}

/** Two or more series over time, with a snapping crosshair and keyboard access. */
export function LineChart({ labels, series, height = 220, describe }: { labels: string[]; series: Series[]; height?: number; describe: string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [index, setIndex] = useState<number | null>(null);
  const pad = { top: 12, right: 14, bottom: 26, left: 36 };
  const plotW = Math.max(0, width - pad.left - pad.right);
  const plotH = height - pad.top - pad.bottom;
  const max = niceMax(Math.max(1, ...series.flatMap((s) => s.values)));
  const ticks = [0, max / 2, max];
  const x = (i: number) => pad.left + (labels.length > 1 ? (i / (labels.length - 1)) * plotW : plotW / 2);
  const y = (v: number) => pad.top + plotH - (v / max) * plotH;
  const every = Math.max(1, Math.ceil(labels.length / Math.max(2, Math.floor(plotW / 70))));

  function pick(event: PointerEvent<SVGSVGElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    const ratio = (event.clientX - box.left - pad.left) / Math.max(1, plotW);
    setIndex(Math.min(labels.length - 1, Math.max(0, Math.round(ratio * (labels.length - 1)))));
  }
  function key(event: KeyboardEvent<SVGSVGElement>) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const step = event.key === "ArrowLeft" ? -1 : 1;
    setIndex((current) => Math.min(labels.length - 1, Math.max(0, (current ?? labels.length - 1) + step)));
  }

  const tipLeft = index === null ? 0 : x(index) > width / 2;
  return (
    <div className="chart" ref={ref}>
      <ul className="legend" aria-label="Legend">
        {series.map((s) => (
          <li key={s.key}>
            <svg width="16" height="8" aria-hidden="true">
              <line x1="1" y1="4" x2="15" y2="4" className={`series-line ${s.className}`} />
            </svg>
            {s.label}
          </li>
        ))}
      </ul>
      {width > 0 && (
        <svg
          width={width}
          height={height}
          role="img"
          aria-label={describe}
          tabIndex={0}
          onPointerMove={pick}
          onPointerLeave={() => setIndex(null)}
          onFocus={() => setIndex((i) => i ?? labels.length - 1)}
          onBlur={() => setIndex(null)}
          onKeyDown={key}
          className="chart-svg"
        >
          {ticks.map((t) => (
            <g key={t}>
              <line x1={pad.left} x2={width - pad.right} y1={y(t)} y2={y(t)} className="grid" />
              <text x={pad.left - 8} y={y(t)} className="axis" textAnchor="end" dominantBaseline="middle">
                {compact(t)}
              </text>
            </g>
          ))}
          {labels.map((label, i) =>
            i % every === 0 || i === labels.length - 1 ? (
              <text key={label} x={x(i)} y={height - 6} className="axis" textAnchor={i === 0 ? "start" : i === labels.length - 1 ? "end" : "middle"}>
                {label}
              </text>
            ) : null,
          )}
          {series.map((s) => (
            <g key={s.key} className={s.className}>
              <path className="series-area" d={`M${x(0)},${y(0)} ${s.values.map((v, i) => `L${x(i)},${y(v)}`).join(" ")} L${x(s.values.length - 1)},${y(0)} Z`} />
              <path className="series-line" d={s.values.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join(" ")} />
            </g>
          ))}
          {index !== null && (
            <g>
              <line x1={x(index)} x2={x(index)} y1={pad.top} y2={pad.top + plotH} className="crosshair" />
              {series.map((s) => (
                <circle key={s.key} cx={x(index)} cy={y(s.values[index] ?? 0)} r="4.5" className={`series-dot ${s.className}`} />
              ))}
            </g>
          )}
        </svg>
      )}
      {index !== null && (
        <div className={`tooltip ${tipLeft ? "tooltip-left" : ""}`} role="status">
          <span className="tooltip-title">{labels[index]}</span>
          {series.map((s) => (
            <span key={s.key} className="tooltip-row">
              <svg width="14" height="8" aria-hidden="true">
                <line x1="1" y1="4" x2="13" y2="4" className={`series-line ${s.className}`} />
              </svg>
              <strong>{compact(s.values[index] ?? 0)}</strong> {s.label}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/** One series, ranked: horizontal bars with the value at the tip. */
export function BarList({ items, describe }: { items: { label: string; count: number }[]; describe: string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const row = 30;
  const labelW = Math.min(170, Math.max(96, width * 0.36));
  const valueW = 44;
  const barMax = Math.max(0, width - labelW - valueW);
  const max = Math.max(1, ...items.map((i) => i.count));
  if (items.length === 0) return <p className="muted small chart-empty">Nothing in this period.</p>;
  return (
    <div className="chart" ref={ref}>
      {width > 0 && (
        <svg width={width} height={items.length * row} role="img" aria-label={describe} className="chart-svg">
          {items.map((item, i) => {
            const w = Math.max(3, (item.count / max) * barMax);
            const top = i * row + (row - 16) / 2;
            const r = Math.min(4, w / 2);
            return (
              <g key={item.label} className="bar">
                <title>{`${item.label}: ${item.count}`}</title>
                <text x={0} y={i * row + row / 2} className="bar-label" dominantBaseline="middle">
                  {item.label.length > 24 ? `${item.label.slice(0, 23)}…` : item.label}
                </text>
                <path
                  className="bar-mark"
                  d={`M${labelW},${top} h${w - r} a${r},${r} 0 0 1 ${r},${r} v${16 - 2 * r} a${r},${r} 0 0 1 -${r},${r} h-${w - r} Z`}
                />
                <text x={labelW + w + 6} y={i * row + row / 2} className="bar-value" dominantBaseline="middle">
                  {compact(item.count)}
                </text>
              </g>
            );
          })}
        </svg>
      )}
    </div>
  );
}
