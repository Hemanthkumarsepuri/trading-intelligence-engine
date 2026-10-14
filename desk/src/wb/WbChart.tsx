// Multi-timeframe chart for the Options Workbench (TradingView Lightweight Charts, Apache-2.0).
// The library only draws; every number comes from the server or from the shared deterministic
// indicator functions. Crosshair and time range are synchronised across all charts that share a syncKey.

import { useEffect, useMemo, useRef } from 'react';
import { ColorType, createChart, LineStyle, type IChartApi, type ISeriesApi, type SeriesMarker, type Time, type UTCTimestamp } from 'lightweight-charts';
import type { BreakoutSetup } from '../../shared/breakout';
import type { TfCandle, Timeframe } from '../../shared/candles';
import { fmtIst, fmtNum } from '../../shared/format';
import { istDateStr } from '../../shared/india';
import { atr as atrFn, bollinger, ema as emaFn, macd as macdFn, rsi as rsiFn } from '../../shared/indicators';
import type { Level, LevelMethod } from '../../shared/levels';
import { sessionVwap } from '../../shared/levels';
import type { ChartMarker } from '../../shared/events';

const css = (name: string): string => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888';

export interface Overlays {
  vwap: boolean;
  ema20: boolean;
  ema50: boolean;
  bb: boolean;
}
export interface Panes {
  rsi: boolean;
  macd: boolean;
  atr: boolean;
}
export type LevelGroup = 'swing' | 'session' | 'opening' | 'rolling';
export const LEVEL_GROUP: Record<LevelMethod, LevelGroup> = {
  SWING_CLUSTER: 'swing',
  PREV_SESSION_HIGH: 'session',
  PREV_SESSION_LOW: 'session',
  OPENING_RANGE_HIGH: 'opening',
  OPENING_RANGE_LOW: 'opening',
  ROLLING_HIGH: 'rolling',
  ROLLING_LOW: 'rolling',
};
export const LEVEL_GROUP_LABEL: Record<LevelGroup, string> = { swing: 'Swing support/resistance', session: 'Previous session high/low', opening: 'Opening range', rolling: 'Rolling high/low' };

const SHORT: Record<LevelMethod, string> = { SWING_CLUSTER: 'swing', PREV_SESSION_HIGH: 'PDH', PREV_SESSION_LOW: 'PDL', OPENING_RANGE_HIGH: 'ORH', OPENING_RANGE_LOW: 'ORL', ROLLING_HIGH: '20H', ROLLING_LOW: '20L' };

// ---- crosshair / range sync across charts -------------------------------------------------------------------------

interface Member {
  chart: IChartApi;
  series: ISeriesApi<'Candlestick' | 'Line' | 'Histogram'>;
  values: Map<string, number>;
}
const groups = new Map<string, Set<Member>>();
let syncing = false;
const keyOf = (t: Time): string => (typeof t === 'object' ? `${t.year}-${t.month}-${t.day}` : String(t));

function join(key: string, m: Member): () => void {
  const set = groups.get(key) ?? new Set<Member>();
  set.add(m);
  groups.set(key, set);
  m.chart.subscribeCrosshairMove((p) => {
    if (syncing) return;
    syncing = true;
    try {
      for (const o of set) {
        if (o === m) continue;
        if (!p.time) { o.chart.clearCrosshairPosition(); continue; }
        const v = o.values.get(keyOf(p.time));
        if (v !== undefined) o.chart.setCrosshairPosition(v, p.time, o.series);
        else o.chart.clearCrosshairPosition();
      }
    } finally {
      syncing = false;
    }
  });
  m.chart.timeScale().subscribeVisibleLogicalRangeChange((r) => {
    if (syncing || !r) return;
    syncing = true;
    try {
      for (const o of set) if (o !== m) o.chart.timeScale().setVisibleLogicalRange(r);
    } finally {
      syncing = false;
    }
  });
  return () => {
    set.delete(m);
    if (set.size === 0) groups.delete(key);
  };
}

const fmtTick = new Intl.DateTimeFormat('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: false });
const fmtDay = new Intl.DateTimeFormat('en-IN', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short' });

function baseOptions(el: HTMLElement, height: number, tf: Timeframe) {
  return {
    height,
    width: el.clientWidth,
    layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: css('--muted'), fontFamily: css('--sans'), fontSize: 11 },
    grid: { vertLines: { color: css('--line') + '55' }, horzLines: { color: css('--line') + '55' } },
    rightPriceScale: { borderColor: css('--line') },
    timeScale: {
      borderColor: css('--line'),
      timeVisible: tf !== '1d',
      secondsVisible: false,
      tickMarkFormatter: tf === '1d' ? undefined : (time: Time, type: number) => (typeof time === 'number' ? (type >= 3 ? fmtTick.format(new Date(time * 1000)) : fmtDay.format(new Date(time * 1000))) : ''),
    },
    crosshair: { mode: 1 },
    localization: { locale: 'en-IN', timeFormatter: tf === '1d' ? undefined : (t: Time) => (typeof t === 'number' ? `${fmtIst(new Date(t * 1000).toISOString())}` : '') },
  };
}

export interface WbChartProps {
  candles: TfCandle[];
  tf: Timeframe;
  overlays: Overlays;
  panes: Panes;
  levels: Level[];
  levelGroups: Record<LevelGroup, boolean>;
  setup: BreakoutSetup | null;
  showSetup: boolean;
  events: ChartMarker[];
  showEvents: boolean;
  atr: number | null;
  syncKey: string;
  height?: number;
  label: string;
}

export function WbChart(p: WbChartProps) {
  const host = useRef<HTMLDivElement>(null);
  const rsiEl = useRef<HTMLDivElement>(null);
  const macdEl = useRef<HTMLDivElement>(null);
  const atrEl = useRef<HTMLDivElement>(null);
  const height = p.height ?? 380;

  const closes = useMemo(() => p.candles.map((c) => c.c), [p.candles]);

  useEffect(() => {
    const el = host.current;
    if (!el || p.candles.length === 0) return;
    const toTime = (t: number): Time => (p.tf === '1d' ? (istDateStr(new Date(t * 1000)) as unknown as Time) : (t as UTCTimestamp));
    const cleanups: (() => void)[] = [];
    const charts: IChartApi[] = [];

    const chart = createChart(el, baseOptions(el, height, p.tf));
    charts.push(chart);
    const series = chart.addCandlestickSeries({ upColor: css('--pos'), downColor: css('--neg'), wickUpColor: css('--pos'), wickDownColor: css('--neg'), borderVisible: false });
    const muted = css('--faint');
    series.setData(
      p.candles.map((c) => ({
        time: toTime(c.t),
        open: c.o, high: c.h, low: c.l, close: c.c,
        // the still-forming bar is drawn hollow-grey: it is shown, but never used as a closed candle
        ...(c.complete ? {} : { color: muted + '88', wickColor: muted, borderColor: muted }),
      })),
    );
    const values = new Map<string, number>(p.candles.map((c) => [keyOf(toTime(c.t)), c.c]));
    cleanups.push(join(p.syncKey, { chart, series, values }));

    const line = (vals: (number | null)[], color: string, width: 1 | 2 = 1, style: LineStyle = LineStyle.Solid) => {
      const s = chart.addLineSeries({ color, lineWidth: width, lineStyle: style, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      s.setData(vals.flatMap((v, i) => (v === null ? [] : [{ time: toTime(p.candles[i]!.t), value: v }])));
    };
    if (p.overlays.ema20) line(emaFn(closes, 20), '#60a5fa');
    if (p.overlays.ema50) line(emaFn(closes, 50), '#f59e0b');
    if (p.overlays.vwap && p.tf !== '1d') line(sessionVwap(p.candles), '#c084fc', 2);
    if (p.overlays.bb) {
      const bb = bollinger(closes, 20, 2);
      line(bb.map((b) => b?.upper ?? null), '#94a3b8', 1, LineStyle.Dotted);
      line(bb.map((b) => b?.lower ?? null), '#94a3b8', 1, LineStyle.Dotted);
    }

    // unknown volume is left out of the histogram (never drawn as a zero-height bar)
    const vol = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol', color: muted + '55' });
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    vol.setData(p.candles.filter((c) => c.v !== null).map((c) => ({ time: toTime(c.t), value: c.v as number, color: (c.c >= c.o ? css('--pos') : css('--neg')) + (c.complete ? '55' : '33') })));

    // levels near price, grouped so the user can switch each family off
    const last = p.candles[p.candles.length - 1]!.c;
    const span = p.atr ? p.atr * 4 : last * 0.02;
    const shown = p.levels.filter((l) => p.levelGroups[LEVEL_GROUP[l.method]] && Math.abs(l.price - last) <= span).sort((a, b) => Math.abs(a.price - last) - Math.abs(b.price - last)).slice(0, 8); // the nearest eight; the full list is in the table below
    for (const l of shown) {
      series.createPriceLine({
        price: l.price,
        color: l.kind === 'RESISTANCE' ? css('--neg') : css('--pos'),
        lineWidth: 1,
        lineStyle: l.method === 'SWING_CLUSTER' ? LineStyle.Dashed : LineStyle.Dotted,
        axisLabelVisible: true,
        title: `${l.kind === 'RESISTANCE' ? 'R' : 'S'} ${SHORT[l.method]}${l.touches > 1 ? `×${l.touches}` : ''}`,
      });
    }
    const s = p.setup;
    if (p.showSetup && s && s.state !== 'DATA_INSUFFICIENT' && s.state !== 'NOT_READY') {
      series.createPriceLine({ price: s.level.price, color: css('--info'), lineWidth: 2, lineStyle: LineStyle.Solid, axisLabelVisible: true, title: `level ${s.level.price}` });
      if (s.invalidationPrice !== null) series.createPriceLine({ price: s.invalidationPrice, color: css('--warn'), lineWidth: 1, lineStyle: LineStyle.LargeDashed, axisLabelVisible: true, title: 'invalidation' });
    }

    const markers: SeriesMarker<Time>[] = [];
    if (p.showSetup && s?.breakoutBarT) {
      const up = s.direction === 'UP';
      markers.push({ time: toTime(s.breakoutBarT), position: up ? 'belowBar' : 'aboveBar', color: css('--info'), shape: up ? 'arrowUp' : 'arrowDown', text: s.state === 'CONFIRMED' ? 'confirmed' : s.state === 'FAILED' ? 'failed' : 'break' });
    }
    if (p.showEvents) for (const e of p.events) markers.push({ time: toTime(e.t), position: 'aboveBar', color: css('--warn'), shape: 'circle', text: e.type === 'EARNINGS' ? 'E' : 'N' });
    markers.sort((a, b) => (keyOf(a.time) < keyOf(b.time) ? -1 : 1));
    if (markers.length) series.setMarkers(markers);

    chart.timeScale().fitContent();
    if (p.candles.length > 120) chart.timeScale().setVisibleLogicalRange({ from: p.candles.length - 120, to: p.candles.length + 3 });

    // indicator panes
    const pane = (target: HTMLDivElement | null, build: (c: IChartApi, tt: (t: number) => Time) => ISeriesApi<'Line' | 'Histogram'> | null, h = 110) => {
      if (!target) return;
      const c = createChart(target, { ...baseOptions(target, h, p.tf), timeScale: { ...baseOptions(target, h, p.tf).timeScale, visible: false } });
      charts.push(c);
      const sr = build(c, toTime);
      if (sr) cleanups.push(join(p.syncKey, { chart: c, series: sr, values: new Map<string, number>() }));
      c.timeScale().setVisibleLogicalRange(chart.timeScale().getVisibleLogicalRange() ?? { from: 0, to: p.candles.length });
    };
    const withTimes = (vals: (number | null)[]) => vals.flatMap((v, i) => (v === null ? [] : [{ time: toTime(p.candles[i]!.t), value: v }]));
    if (p.panes.rsi) pane(rsiEl.current, (c) => {
      const s2 = c.addLineSeries({ color: '#a78bfa', lineWidth: 1, priceLineVisible: false });
      s2.setData(withTimes(rsiFn(closes, 14)));
      s2.createPriceLine({ price: 70, color: css('--faint'), lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: '' });
      s2.createPriceLine({ price: 30, color: css('--faint'), lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: '' });
      return s2;
    });
    if (p.panes.macd) pane(macdEl.current, (c) => {
      const m = macdFn(closes);
      const hist = c.addHistogramSeries({ priceLineVisible: false });
      hist.setData(m.flatMap((x, i) => (x?.hist == null ? [] : [{ time: toTime(p.candles[i]!.t), value: x.hist, color: x.hist >= 0 ? css('--pos') + '88' : css('--neg') + '88' }])));
      const l1 = c.addLineSeries({ color: '#60a5fa', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      l1.setData(withTimes(m.map((x) => x?.macd ?? null)));
      const l2 = c.addLineSeries({ color: '#f59e0b', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      l2.setData(withTimes(m.map((x) => x?.signal ?? null)));
      return l1;
    });
    if (p.panes.atr) pane(atrEl.current, (c) => {
      const s2 = c.addLineSeries({ color: '#34d399', lineWidth: 1, priceLineVisible: false });
      s2.setData(withTimes(atrFn(p.candles, 14)));
      return s2;
    });

    const ro = new ResizeObserver(() => charts.forEach((c, i) => c.applyOptions({ width: (i === 0 ? el : [rsiEl.current, macdEl.current, atrEl.current].filter(Boolean)[i - 1] ?? el)!.clientWidth })));
    ro.observe(el);
    return () => {
      ro.disconnect();
      cleanups.forEach((f) => f());
      charts.forEach((c) => c.remove());
    };
  }, [p.candles, p.tf, p.overlays, p.panes, p.levels, p.levelGroups, p.setup, p.showSetup, p.events, p.showEvents, p.atr, p.syncKey, height, closes]);

  if (p.candles.length === 0) return <div className="empty">No candles available for this timeframe.</div>;
  const recent = p.candles.slice(-8).reverse();
  const forming = p.candles.some((c) => !c.complete);
  return (
    <div>
      <div role="img" aria-label={p.label} data-testid="wb-chart">
        <div ref={host} style={{ width: '100%', height }} />
        {p.panes.rsi && <div ref={rsiEl} style={{ width: '100%', height: 110 }} data-testid="pane-rsi" />}
        {p.panes.macd && <div ref={macdEl} style={{ width: '100%', height: 110 }} data-testid="pane-macd" />}
        {p.panes.atr && <div ref={atrEl} style={{ width: '100%', height: 110 }} data-testid="pane-atr" />}
      </div>
      <div className="legend" style={{ marginTop: 6 }}>
        {p.overlays.ema20 && <span><i style={{ background: '#60a5fa' }} />EMA 20</span>}
        {p.overlays.ema50 && <span><i style={{ background: '#f59e0b' }} />EMA 50</span>}
        {p.overlays.vwap && p.tf !== '1d' && <span><i style={{ background: '#c084fc' }} />VWAP (session)</span>}
        {p.overlays.bb && <span><i style={{ background: '#94a3b8' }} />Bollinger 20,2</span>}
        {forming && <span><i style={{ background: 'var(--faint)' }} />Forming bar (not a closed candle)</span>}
      </div>
      <details className="notes">
        <summary>Latest bars as a table</summary>
        <div className="tbl-wrap">
          <table>
            <thead><tr><th>Bar start (IST)</th><th className="num">Open</th><th className="num">High</th><th className="num">Low</th><th className="num">Close</th><th className="num">Volume</th><th>Status</th></tr></thead>
            <tbody>
              {recent.map((c) => (
                <tr key={c.t}>
                  <td>{fmtIst(new Date(c.t * 1000).toISOString())}</td>
                  <td className="num">{fmtNum(c.o)}</td><td className="num">{fmtNum(c.h)}</td><td className="num">{fmtNum(c.l)}</td><td className="num">{fmtNum(c.c)}</td>
                  <td className="num">{c.v === null ? '-' : fmtNum(c.v, 0)}</td>
                  <td>{c.complete ? 'complete' : `forming (${c.have}/${c.want})`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

/** Option-premium line from the snapshots this app observed. Not candles. */
export function ObservedChart({ points, syncKey, height = 200 }: { points: { t: number; ltp: number }[]; syncKey: string; height?: number }) {
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = host.current;
    if (!el || points.length === 0) return;
    const chart = createChart(el, baseOptions(el, height, '5m'));
    const s = chart.addLineSeries({ color: css('--accent'), lineWidth: 2, priceLineVisible: false });
    s.setData(points.map((x) => ({ time: x.t as UTCTimestamp, value: x.ltp })));
    // a line has no sparse-time guarantee: markers show the actual snapshot instants
    s.setMarkers(points.map((x) => ({ time: x.t as UTCTimestamp, position: 'inBar' as const, color: css('--accent'), shape: 'circle' as const })));
    const values = new Map<string, number>(points.map((x) => [String(x.t), x.ltp]));
    const off = join(syncKey, { chart, series: s, values });
    chart.timeScale().fitContent();
    const ro = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth }));
    ro.observe(el);
    return () => { ro.disconnect(); off(); chart.remove(); };
  }, [points, syncKey, height]);
  return <div ref={host} style={{ width: '100%', height }} role="img" aria-label="Option premium snapshots observed by this app" data-testid="option-observed-chart" />;
}
