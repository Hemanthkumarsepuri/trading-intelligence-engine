import { useEffect, useMemo, useRef, useState } from 'react';
import { createChart, type IChartApi, type ISeriesApi, type UTCTimestamp } from 'lightweight-charts';
import { fmtCompactINR, fmtNum, fmtQty } from '../shared/format';
import { istDateStr } from '../shared/india';
import { ema as emaFn, sma as smaFn } from '../shared/indicators';
import type { ChainRow } from '../shared/optionchain';
import type { Candle } from '../shared/types';

function useWidth(): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(600);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(Math.max(240, el.clientWidth)));
    ro.observe(el);
    setW(Math.max(240, el.clientWidth));
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

const css = (name: string): string => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888';

// ---- Sparkline -------------------------------------------------------------------------------

export function Sparkline({ values, up, width = 90, height = 28 }: { values: number[]; up: boolean | null; width?: number; height?: number }) {
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => `${((i / (values.length - 1)) * width).toFixed(1)},${(height - 2 - ((v - min) / span) * (height - 4)).toFixed(1)}`).join(' ');
  const color = up === null ? 'var(--muted)' : up ? 'var(--pos)' : 'var(--neg)';
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

// ---- Equity curve / area line ---------------------------------------------------------------------

const AREA_PAD = { l: 56, r: 12, t: 12, b: 24 } as const;

export interface LinePoint {
  label: string;
  value: number;
}

export function AreaChart({ points, height = 220, baseline = 0, money = true }: { points: LinePoint[]; height?: number; baseline?: number; money?: boolean }) {
  const [ref, w] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const pad = AREA_PAD;
  const calc = useMemo(() => {
    if (points.length === 0) return null;
    const vals = points.map((p) => p.value).concat(baseline);
    let min = Math.min(...vals);
    let max = Math.max(...vals);
    if (min === max) { min -= 1; max += 1; }
    const padY = (max - min) * 0.08;
    min -= padY; max += padY;
    const P = AREA_PAD;
    const x = (i: number) => P.l + (points.length === 1 ? (w - P.l - P.r) / 2 : (i / (points.length - 1)) * (w - P.l - P.r));
    const y = (v: number) => P.t + (1 - (v - min) / (max - min)) * (height - P.t - P.b);
    return { min, max, x, y };
  }, [points, w, height, baseline]);

  if (!calc || points.length === 0) return <div ref={ref} />;
  const { x, y, min, max } = calc;
  const line = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');
  const area = `${line} L${x(points.length - 1).toFixed(1)},${y(baseline).toFixed(1)} L${x(0).toFixed(1)},${y(baseline).toFixed(1)} Z`;
  const last = points[points.length - 1]!.value;
  const color = last >= baseline ? 'var(--pos)' : 'var(--neg)';
  const ticks = [min, (min + max) / 2, max];
  const fmt = (v: number) => (money ? (max - min < 1e5 ? `${v < 0 ? '-' : ''}₹${fmtNum(Math.abs(v), 0)}` : fmtCompactINR(v)) : fmtNum(v, 0));
  const h = hover !== null ? points[hover] : null;

  return (
    <div ref={ref} className="chart-box" style={{ position: 'relative' }}>
      <svg
        width={w}
        height={height}
        role="img"
        aria-label="Line chart"
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const rel = (e.clientX - r.left - pad.l) / (w - pad.l - pad.r);
          setHover(Math.max(0, Math.min(points.length - 1, Math.round(rel * (points.length - 1)))));
        }}
        onMouseLeave={() => setHover(null)}
      >
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={pad.l} x2={w - pad.r} y1={y(t)} y2={y(t)} stroke="var(--line)" strokeDasharray="3 4" />
            <text x={pad.l - 8} y={y(t) + 4} textAnchor="end" fontSize="11" fill="var(--muted)">{fmt(t)}</text>
          </g>
        ))}
        <line x1={pad.l} x2={w - pad.r} y1={y(baseline)} y2={y(baseline)} stroke="var(--faint)" strokeWidth="1" />
        <path d={area} fill={color} opacity="0.12" />
        <path d={line} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" />
        <text x={pad.l} y={height - 6} fontSize="11" fill="var(--muted)">{points[0]!.label}</text>
        <text x={w - pad.r} y={height - 6} fontSize="11" fill="var(--muted)" textAnchor="end">{points[points.length - 1]!.label}</text>
        {hover !== null && h && (
          <g>
            <line x1={x(hover)} x2={x(hover)} y1={pad.t} y2={height - pad.b} stroke="var(--faint)" />
            <circle cx={x(hover)} cy={y(h.value)} r="4" fill={color} />
          </g>
        )}
      </svg>
      {hover !== null && h && (
        <div className="toast" style={{ position: 'absolute', top: 4, left: Math.min(Math.max(x(hover) - 60, 0), w - 140), padding: '4px 10px', pointerEvents: 'none' }}>
          <div className="small muted">{h.label}</div>
          <strong>{fmt(h.value)}</strong>
        </div>
      )}
    </div>
  );
}

// ---- Bars (+/-) --------------------------------------------------------------------------------------

export function BarChart({ points, height = 160, money = true }: { points: LinePoint[]; height?: number; money?: boolean }) {
  const [ref, w] = useWidth();
  if (points.length === 0) return <div ref={ref} />;
  const pad = { l: 56, r: 8, t: 8, b: 22 };
  const max = Math.max(...points.map((p) => Math.abs(p.value)), 1);
  const zero = pad.t + (height - pad.t - pad.b) / 2;
  const half = (height - pad.t - pad.b) / 2;
  const slot = (w - pad.l - pad.r) / points.length;
  const bw = Math.max(2, Math.min(28, slot * 0.7));
  const fmt = (v: number) => (money ? fmtCompactINR(v) : fmtNum(v, 0));
  return (
    <div ref={ref} className="chart-box">
      <svg width={w} height={height} role="img" aria-label="Bar chart">
        <line x1={pad.l} x2={w - pad.r} y1={zero} y2={zero} stroke="var(--faint)" />
        <text x={pad.l - 8} y={pad.t + 10} textAnchor="end" fontSize="11" fill="var(--muted)">{fmt(max)}</text>
        <text x={pad.l - 8} y={height - pad.b} textAnchor="end" fontSize="11" fill="var(--muted)">{fmt(-max)}</text>
        {points.map((p, i) => {
          const hgt = (Math.abs(p.value) / max) * half;
          const cx = pad.l + slot * i + slot / 2;
          return (
            <rect key={i} x={cx - bw / 2} y={p.value >= 0 ? zero - hgt : zero} width={bw} height={Math.max(1, hgt)} rx="2" fill={p.value >= 0 ? 'var(--pos)' : 'var(--neg)'} opacity="0.9">
              <title>{`${p.label}: ${fmt(p.value)}`}</title>
            </rect>
          );
        })}
        <text x={pad.l} y={height - 5} fontSize="11" fill="var(--muted)">{points[0]!.label}</text>
        <text x={w - pad.r} y={height - 5} textAnchor="end" fontSize="11" fill="var(--muted)">{points[points.length - 1]!.label}</text>
      </svg>
    </div>
  );
}

// ---- Option-chain OI chart ---------------------------------------------------------------------------------

export function OiChart({ rows, spot, atm, maxPain }: { rows: ChainRow[]; spot: number | null; atm: number | null; maxPain: number | null }) {
  const [ref, w] = useWidth();
  const height = 260;
  const pad = { l: 8, r: 8, t: 14, b: 40 };
  if (rows.length === 0) return <div ref={ref} />;
  const max = Math.max(...rows.flatMap((r) => [r.ce.oi ?? 0, r.pe.oi ?? 0]), 1);
  const slot = (w - pad.l - pad.r) / rows.length;
  const bw = Math.max(2, Math.min(14, slot * 0.38));
  const base = height - pad.b;
  const hgt = (v: number | null) => ((v ?? 0) / max) * (base - pad.t);
  const step = Math.ceil(rows.length / Math.max(4, Math.floor(w / 56)));
  const idxOf = (s: number | null) => (s === null ? -1 : rows.reduce((best, r, i) => (Math.abs(r.strike - s) < Math.abs(rows[best]!.strike - s) ? i : best), 0));
  const si = idxOf(spot);
  return (
    <div ref={ref} className="chart-box">
      <svg width={w} height={height} role="img" aria-label="Open interest by strike">
        <line x1={pad.l} x2={w - pad.r} y1={base} y2={base} stroke="var(--faint)" />
        {rows.map((r, i) => {
          const cx = pad.l + slot * i + slot / 2;
          const isAtm = atm !== null && r.strike === atm;
          return (
            <g key={r.strike}>
              {isAtm && <rect x={cx - slot / 2} y={pad.t} width={slot} height={base - pad.t} fill="var(--accent)" opacity="0.08" />}
              <rect x={cx - bw - 0.5} y={base - hgt(r.pe.oi)} width={bw} height={hgt(r.pe.oi)} fill="var(--pos)" opacity="0.85" rx="1.5"><title>{`${r.strike} PE OI ${fmtQty(r.pe.oi)}`}</title></rect>
              <rect x={cx + 0.5} y={base - hgt(r.ce.oi)} width={bw} height={hgt(r.ce.oi)} fill="var(--neg)" opacity="0.85" rx="1.5"><title>{`${r.strike} CE OI ${fmtQty(r.ce.oi)}`}</title></rect>
              {i % step === 0 && (
                <text x={cx} y={base + 14} textAnchor="middle" fontSize="10.5" fill={isAtm ? 'var(--accent)' : 'var(--muted)'} fontWeight={isAtm ? 700 : 400}>{r.strike}</text>
              )}
            </g>
          );
        })}
        {si >= 0 && spot !== null && (
          <g>
            <line x1={pad.l + slot * si + slot / 2} x2={pad.l + slot * si + slot / 2} y1={pad.t} y2={base} stroke="var(--info)" strokeDasharray="4 3" />
            <text x={pad.l + slot * si + slot / 2} y={height - 6} textAnchor="middle" fontSize="11" fill="var(--info)">Spot {fmtNum(spot, 0)}</text>
          </g>
        )}
        {maxPain !== null && idxOf(maxPain) >= 0 && (
          <text x={pad.l + slot * idxOf(maxPain) + slot / 2} y={pad.t - 3} textAnchor="middle" fontSize="10.5" fill="var(--warn)">Max pain {maxPain}</text>
        )}
      </svg>
      <div className="legend">
        <span><i style={{ background: 'var(--pos)' }} />Put OI</span>
        <span><i style={{ background: 'var(--neg)' }} />Call OI</span>
      </div>
    </div>
  );
}

// ---- Candlestick (lightweight-charts) ---------------------------------------------------------------------------

export interface Level {
  price: number;
  label: string;
  color: string;
}

export function CandleChart({ candles, levels = [], height = 360 }: { candles: Candle[]; levels?: Level[]; height?: number }) {
  const host = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    const el = host.current;
    if (!el || candles.length === 0) return;
    const chart = createChart(el, {
      height,
      width: el.clientWidth,
      layout: { background: { color: 'transparent' }, textColor: css('--muted'), fontFamily: css('--sans'), fontSize: 11 },
      grid: { vertLines: { color: css('--line') + '55' }, horzLines: { color: css('--line') + '55' } },
      rightPriceScale: { borderColor: css('--line') },
      timeScale: { borderColor: css('--line'), timeVisible: false },
      crosshair: { mode: 1 },
      localization: { locale: 'en-IN' },
    });
    chartRef.current = chart;
    const toTime = (t: number) => istDateStr(new Date(t * 1000)) as unknown as UTCTimestamp;

    const series: ISeriesApi<'Candlestick'> = chart.addCandlestickSeries({
      upColor: css('--pos'), downColor: css('--neg'), wickUpColor: css('--pos'), wickDownColor: css('--neg'), borderVisible: false,
    });
    series.setData(candles.map((c) => ({ time: toTime(c.t), open: c.o, high: c.h, low: c.l, close: c.c })));

    const closes = candles.map((c) => c.c);
    const addMa = (vals: (number | null)[], color: string) => {
      const s = chart.addLineSeries({ color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      s.setData(vals.flatMap((v, i) => (v === null ? [] : [{ time: toTime(candles[i]!.t), value: v }])));
    };
    addMa(emaFn(closes, 20), '#60a5fa');
    addMa(smaFn(closes, 50), '#f59e0b');
    addMa(smaFn(closes, 200), '#a78bfa');

    const vol = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol', color: css('--faint') + '55' });
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    // unknown volume is left out of the histogram (not drawn as a zero-height bar)
    vol.setData(candles.filter((c) => c.v !== null).map((c) => ({ time: toTime(c.t), value: c.v as number, color: (c.c >= c.o ? css('--pos') : css('--neg')) + '55' })));

    for (const l of levels) series.createPriceLine({ price: l.price, color: l.color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: l.label });
    chart.timeScale().fitContent();
    if (candles.length > 130) chart.timeScale().setVisibleLogicalRange({ from: candles.length - 130, to: candles.length + 3 });

    const ro = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth }));
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [candles, levels, height]);

  if (candles.length === 0) return <div className="empty">No price history available.</div>;
  return (
    <div>
      <div ref={host} style={{ width: '100%', height }} />
      <div className="legend" style={{ marginTop: 6 }}>
        <span><i style={{ background: '#60a5fa' }} />EMA 20</span>
        <span><i style={{ background: '#f59e0b' }} />SMA 50</span>
        <span><i style={{ background: '#a78bfa' }} />SMA 200</span>
      </div>
    </div>
  );
}
