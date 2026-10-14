import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { UnderlyingView, MtfRow } from '../../server/services/wb/analysis';
import type { ContractView, ContractChart, ExplorerView, SearchResult } from '../../server/services/wb/contracts';
import type { ContextView } from '../../server/services/wb/misc';
import type { EventsView } from '../../server/services/wb/events';
import { TF_LABEL, TIMEFRAMES, TF_MINUTES, type Timeframe } from '../../shared/candles';
import type { ContractValidation } from '../../shared/contract';
import { chartMarkers } from '../../shared/events';
import { fmtIst, fmtNum, fmtPct, fmtSigned } from '../../shared/format';
import { useGet, useSend, ApiError } from '../api';
import { setQuery, useLoc } from '../nav';
import { Async, Badge, Card, Empty, errMsg, Icon, PageHead, Segmented, Skeleton, Spinner, useToast } from '../ui';
import { Disclosure, Evidence, Lifecycle, NOT_ADVICE, Prov, StateBadge, Sv, openWorkbench, useDebounced } from '../wb/common';
import { LEVEL_GROUP_LABEL, ObservedChart, WbChart, type LevelGroup, type Overlays, type Panes } from '../wb/WbChart';

// ---- persisted chart preferences (per browser; never needed for correctness) ---------------------------------------------------------------

interface Prefs {
  overlays: Overlays;
  panes: Panes;
  levelGroups: Record<LevelGroup, boolean>;
  showSetup: boolean;
  showEvents: boolean;
  view: 'both' | 'underlying' | 'option';
}
const DEFAULT_PREFS: Prefs = { overlays: { vwap: true, ema20: true, ema50: true, bb: false }, panes: { rsi: false, macd: false, atr: false }, levelGroups: { swing: true, session: true, opening: true, rolling: false }, showSetup: true, showEvents: true, view: 'both' };

function usePrefs(): [Prefs, (p: Prefs) => void] {
  const [p, setP] = useState<Prefs>(() => {
    try {
      const raw = window.localStorage.getItem('desk.wb.prefs');
      return raw ? { ...DEFAULT_PREFS, ...(JSON.parse(raw) as Partial<Prefs>) } : DEFAULT_PREFS;
    } catch {
      return DEFAULT_PREFS;
    }
  });
  return [
    p,
    (n) => {
      setP(n);
      try {
        window.localStorage.setItem('desk.wb.prefs', JSON.stringify(n));
      } catch {
        /* preferences are a convenience only */
      }
    },
  ];
}

const tfSec = (tf: Timeframe): number => (tf === '1d' ? 86400 : TF_MINUTES[tf] * 60);

// ---- market context strip ----------------------------------------------------------------------------------------------------------------

function ContextStrip() {
  const q = useGet<ContextView>('/api/workbench/context', { poll: 60_000 });
  return (
    <Async q={q} skeleton={<Card><Skeleton h={48} /></Card>}>
      {(c) => (
        <div className="card" aria-label="Market context" data-testid="context-strip">
          <div className="card-b row" style={{ gap: 18 }}>
            <span className="row" style={{ gap: 6 }}><strong>{c.session.label}</strong></span>
            {[c.nifty, c.banknifty].map((i) => (
              <span key={i.id} className="row" style={{ gap: 6 }}>
                <span className="muted small">{i.id === 'NIFTY' ? 'NIFTY 50' : 'BANK NIFTY'}</span>
                <Sv f={i.price} digits={0} />
                <Sv f={i.changePct} digits={2} suffix="%" hideBadge />
              </span>
            ))}
            <span className="row" style={{ gap: 6 }}>
              <span className="muted small">India VIX</span>
              <Sv f={c.vix.value} />
              {c.vix.label && <span className="small muted">{c.vix.label}</span>}
            </span>
            <span className="row" style={{ gap: 6 }}>
              <span className="muted small">Breadth</span>
              {c.breadth.advances !== null ? <span className="mono">{c.breadth.advances}▲ {c.breadth.declines}▼</span> : <StateBadge state="UNKNOWN" />}
            </span>
            <span className="row" style={{ gap: 6 }}>
              <span className="muted small">NIFTY gap</span>
              <Sv f={c.niftyOpening.gapPct} suffix="%" />
              {c.niftyOpening.openingRange.high !== null && <span className="small muted">OR {fmtNum(c.niftyOpening.openingRange.low)}–{fmtNum(c.niftyOpening.openingRange.high)}</span>}
            </span>
            {c.sectors.leaders.length > 0 && (
              <span className="small muted hide-m">
                Strong: {c.sectors.leaders.map((s) => `${s.name} ${fmtSigned(s.changePct, 1)}%`).join(', ')} · Weak: {c.sectors.laggards.map((s) => `${s.name} ${fmtSigned(s.changePct, 1)}%`).join(', ')}
              </span>
            )}
          </div>
          <div className="card-b small muted" style={{ paddingTop: 0 }}>{c.regimeNote}</div>
        </div>
      )}
    </Async>
  );
}

// ---- unified search ----------------------------------------------------------------------------------------------------------------------------

function SearchBox({ onPick }: { onPick: (u: string, contractId?: string) => void }) {
  const [text, setText] = useState('');
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const dq = useDebounced(text.trim(), 250);
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const q = useGet<SearchResult>(dq.length >= 1 ? `/api/workbench/search?q=${encodeURIComponent(dq)}` : null, { cancel: true });

  const flat = useMemo(() => {
    const out: { key: string; label: string; sub: string; pick: () => void }[] = [];
    for (const g of q.data?.data.groups ?? []) for (const it of g.items) out.push({ key: `${g.type}:${it.id}`, label: it.id, sub: `${it.name}${it.fno ? (it.lotSize ? ` · lot ${it.lotSize}` : '') : ' · no listed options'}`, pick: () => onPick(it.id) });
    for (const c of q.data?.data.contracts ?? []) out.push({ key: `c:${c.id}`, label: c.label, sub: c.ltp !== null ? `LTP ${fmtNum(c.ltp)}` : 'no last price', pick: () => onPick(c.ref.underlying, c.id) });
    return out;
  }, [q.data, onPick]);

  useEffect(() => setActive(0), [flat.length]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (e.key === '/' && t && !/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) && !t.isContentEditable) {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const choose = (i: number) => {
    const it = flat[i];
    if (!it) return;
    it.pick();
    setText('');
    setOpen(false);
  };
  const searching = dq.length >= 1 && (q.isFetching || dq !== text.trim());

  return (
    <div style={{ position: 'relative', maxWidth: 640 }}>
      <label className="sr-only" htmlFor={`${listId}-in`}>Search stocks, indices or option contracts</label>
      <div className="row" style={{ flexWrap: 'nowrap' }}>
        <input
          id={`${listId}-in`}
          ref={inputRef}
          role="combobox"
          aria-expanded={open && flat.length > 0}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={open && flat[active] ? `${listId}-${active}` : undefined}
          placeholder="Search a symbol, company or contract: RELIANCE, nifty 25000 ce, tcs oct   ( / )"
          value={text}
          autoComplete="off"
          onChange={(e) => { setText(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(a + 1, flat.length - 1)); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
            else if (e.key === 'Enter') { e.preventDefault(); choose(active); }
            else if (e.key === 'Escape') { setOpen(false); }
          }}
          style={{ flex: 1 }}
        />
        {searching && <Spinner />}
      </div>
      {open && dq.length >= 1 && (
        <div className="card" style={{ position: 'absolute', zIndex: 20, left: 0, right: 0, top: '100%', marginTop: 4, maxHeight: 360, overflowY: 'auto' }}>
          {q.isError && <div className="card-b"><div className="banner neg small" role="alert">{errMsg(q.error)} Try again in a moment.</div></div>}
          {!q.isError && flat.length === 0 && !searching && <div className="card-b small muted" role="status">No match for “{dq}”. Try the NSE symbol (for example TATAMOTORS) or a company name.</div>}
          <ul role="listbox" id={listId} style={{ listStyle: 'none', margin: 0, padding: 4 }}>
            {(q.data?.data.groups ?? []).map((g) => (
              <li key={g.type} role="presentation">
                <div className="small muted" style={{ padding: '6px 10px' }}>{g.title}</div>
                <ul role="presentation" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                  {g.items.map((it) => {
                    const idx = flat.findIndex((f) => f.key === `${g.type}:${it.id}`);
                    return (
                      <li key={it.id} id={`${listId}-${idx}`} role="option" aria-selected={idx === active} onMouseDown={(e) => { e.preventDefault(); choose(idx); }} onMouseEnter={() => setActive(idx)} style={{ padding: '6px 10px', cursor: 'pointer', borderRadius: 6, background: idx === active ? 'var(--card-2)' : undefined }}>
                        <strong>{it.id}</strong> <span className="muted small">{it.name}{it.fno ? (it.lotSize ? ` · lot ${it.lotSize}` : '') : ' · no listed options'}</span>
                      </li>
                    );
                  })}
                </ul>
              </li>
            ))}
            {(q.data?.data.contracts.length ?? 0) > 0 && (
              <li role="presentation">
                <div className="small muted" style={{ padding: '6px 10px' }}>Contracts</div>
                <ul role="presentation" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                  {q.data!.data.contracts.map((c) => {
                    const idx = flat.findIndex((f) => f.key === `c:${c.id}`);
                    return (
                      <li key={c.id} id={`${listId}-${idx}`} role="option" aria-selected={idx === active} onMouseDown={(e) => { e.preventDefault(); choose(idx); }} onMouseEnter={() => setActive(idx)} style={{ padding: '6px 10px', cursor: 'pointer', borderRadius: 6, background: idx === active ? 'var(--card-2)' : undefined }}>
                        <strong>{c.label}</strong> <span className="muted small">{c.ltp !== null ? `LTP ${fmtNum(c.ltp)}` : 'no last price'}</span>
                      </li>
                    );
                  })}
                </ul>
              </li>
            )}
          </ul>
          {(q.data?.data.notes.length ?? 0) > 0 && <div className="card-b small muted">{q.data!.data.notes.join(' ')}</div>}
        </div>
      )}
    </div>
  );
}

// ---- timeframe selector -------------------------------------------------------------------------------------------------------------------------

function TfBar({ tf, onTf, mtf, current }: { tf: Timeframe; onTf: (t: Timeframe) => void; mtf: MtfRow[] | null; current: UnderlyingView['availability'] }) {
  const info = (t: Timeframe) => (t === tf ? current : mtf?.find((r) => r.tf === t)?.availability ?? null);
  const unavailable = TIMEFRAMES.filter((t) => info(t) && !info(t)!.available);
  return (
    <div>
      <div className="seg" role="group" aria-label="Timeframe">
        {TIMEFRAMES.map((t) => {
          const a = info(t);
          const off = a !== null && !a.available;
          return (
            <button key={t} type="button" className={t === tf ? 'on' : ''} aria-pressed={t === tf} disabled={off && t !== tf} title={off ? a!.reason ?? 'Not available' : `${TF_LABEL[t]}${a ? ` · ${a.bars} completed bars` : ''}`} onClick={() => onTf(t)}>
              {t}
            </button>
          );
        })}
      </div>
      {unavailable.length > 0 && (
        <div className="small muted" style={{ marginTop: 4 }} data-testid="tf-unavailable">
          Not offered: {unavailable.map((t) => `${t} (${info(t)!.reason})`).join(' · ')}
        </div>
      )}
    </div>
  );
}

// ---- chart options ---------------------------------------------------------------------------------------------------------------------------------

function Check({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="row small" style={{ gap: 6, cursor: 'pointer' }}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} style={{ width: 'auto' }} />
      {label}
    </label>
  );
}

function ChartOptions({ prefs, setPrefs, intraday }: { prefs: Prefs; setPrefs: (p: Prefs) => void; intraday: boolean }) {
  return (
    <details className="notes" data-testid="chart-options">
      <summary>Indicators and levels</summary>
      <div className="row" style={{ gap: 18, alignItems: 'flex-start', marginTop: 8 }}>
        <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="small muted">Overlays</legend>
          <Check label={intraday ? 'VWAP' : 'VWAP (intraday only)'} checked={prefs.overlays.vwap} onChange={(v) => setPrefs({ ...prefs, overlays: { ...prefs.overlays, vwap: v } })} />
          <Check label="EMA 20" checked={prefs.overlays.ema20} onChange={(v) => setPrefs({ ...prefs, overlays: { ...prefs.overlays, ema20: v } })} />
          <Check label="EMA 50" checked={prefs.overlays.ema50} onChange={(v) => setPrefs({ ...prefs, overlays: { ...prefs.overlays, ema50: v } })} />
          <Check label="Bollinger bands" checked={prefs.overlays.bb} onChange={(v) => setPrefs({ ...prefs, overlays: { ...prefs.overlays, bb: v } })} />
        </fieldset>
        <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="small muted">Panes</legend>
          <Check label="RSI 14" checked={prefs.panes.rsi} onChange={(v) => setPrefs({ ...prefs, panes: { ...prefs.panes, rsi: v } })} />
          <Check label="MACD" checked={prefs.panes.macd} onChange={(v) => setPrefs({ ...prefs, panes: { ...prefs.panes, macd: v } })} />
          <Check label="ATR 14" checked={prefs.panes.atr} onChange={(v) => setPrefs({ ...prefs, panes: { ...prefs.panes, atr: v } })} />
        </fieldset>
        <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="small muted">Levels</legend>
          {(Object.keys(LEVEL_GROUP_LABEL) as LevelGroup[]).map((g) => (
            <Check key={g} label={LEVEL_GROUP_LABEL[g]} checked={prefs.levelGroups[g]} onChange={(v) => setPrefs({ ...prefs, levelGroups: { ...prefs.levelGroups, [g]: v } })} />
          ))}
          <Check label="Breakout level and invalidation" checked={prefs.showSetup} onChange={(v) => setPrefs({ ...prefs, showSetup: v })} />
          <Check label="News / earnings markers" checked={prefs.showEvents} onChange={(v) => setPrefs({ ...prefs, showEvents: v })} />
        </fieldset>
      </div>
    </details>
  );
}

// ---- panels ---------------------------------------------------------------------------------------------------------------------------------------------

function Row({ k, children, title }: { k: string; children: React.ReactNode; title?: string }) {
  return (
    <>
      <dt title={title}>{k}</dt>
      <dd>{children}</dd>
    </>
  );
}

function BreakoutCard({ v }: { v: UnderlyingView }) {
  const s = v.structure.primary;
  return (
    <Card title="Breakout status" right={s ? <Lifecycle state={s.state} /> : <Badge>none</Badge>}>
      {!s ? (
        <p className="small muted">No level could be evaluated on this timeframe. {v.availability.reason ?? ''}</p>
      ) : (
        <div className="stack" data-testid="breakout-card">
          <div className="small">
            <strong>{s.direction === 'UP' ? 'Resistance' : 'Support'} {fmtNum(s.level.price)}</strong>{' '}
            <span className="muted">· {s.level.method.replace(/_/g, ' ').toLowerCase()} · {s.tf} · {s.level.touches} touch{s.level.touches === 1 ? '' : 'es'}</span>
          </div>
          <dl className="kv">
            <Row k="Price">{fmtNum(s.price)}</Row>
            <Row k="Distance to level">{s.distance !== null ? `${fmtNum(Math.abs(s.distance))} (${fmtNum(Math.abs(s.distanceAtr ?? 0))}×ATR)` : '-'}</Row>
            <Row k="ATR (14)">{s.atr !== null ? fmtNum(s.atr) : '-'}</Row>
            <Row k="Completed closes beyond">{s.closesBeyond}</Row>
            <Row k="Observed through">{s.observedThrough ? `${fmtIst(new Date(s.observedThrough * 1000).toISOString())} IST` : '-'}</Row>
          </dl>
          <div className="small"><strong>Confirmation:</strong> {s.confirmation}</div>
          <div className="small"><strong>Invalidation:</strong> {s.invalidation}</div>
          <Evidence title="Supports" items={s.supporting} tone="pos" />
          <Evidence title="Contradicts" items={s.contradicting} tone="neg" />
          <Evidence title="Missing or unknown" items={s.missing} tone="warn" />
          <Disclosure summary="Rules used">
            <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{s.rules.map((r, i) => <li key={i}>{r}</li>)}</ul>
          </Disclosure>
          {v.structure.setups.length > 1 && (
            <Disclosure summary={`Other levels in play (${v.structure.setups.length - 1})`}>
              <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
                {v.structure.setups.slice(1).map((o) => <li key={o.level.id}>{o.direction === 'UP' ? 'R' : 'S'} {fmtNum(o.level.price)} · <Lifecycle state={o.state} /> · {fmtNum(Math.abs(o.distanceAtr ?? 0))}×ATR away</li>)}
              </ul>
            </Disclosure>
          )}
        </div>
      )}
    </Card>
  );
}

function UnderlyingCard({ v }: { v: UnderlyingView }) {
  return (
    <Card title="Underlying validation" right={<StateBadge state={v.freshness} />}>
      <dl className="kv" data-testid="underlying-validation">
        <Row k="Last price"><Sv f={v.price} /></Row>
        <Row k="Change"><Sv f={v.change} hideBadge /> <Sv f={v.changePct} suffix="%" hideBadge /></Row>
        <Row k="Previous close"><Sv f={v.prevClose} hideBadge /></Row>
        <Row k="Session high / low"><Sv f={v.dayHigh} hideBadge /> / <Sv f={v.dayLow} hideBadge /></Row>
        <Row k="Session volume"><Sv f={v.volume} digits={0} hideBadge={v.volume.state === 'UNSUPPORTED'} /></Row>
        <Row k="Relative volume" title={v.rvol.note}>{v.rvol.value !== null ? `${v.rvol.value.toFixed(2)}×` : <span className="muted" title={v.rvol.note}>unavailable</span>}</Row>
        <Row k="VWAP / distance" title={v.vwap.note}>{v.vwap.vwap !== null ? `${fmtNum(v.vwap.vwap)} (${fmtSigned(v.vwap.distPct, 2)}%)${v.vwap.event ? ` · ${v.vwap.event.toLowerCase()}` : ''}` : <span className="muted" title={v.vwap.note}>unavailable</span>}</Row>
        <Row k="ATR (14)"><Sv f={v.atr} /></Row>
        <Row k="Trend">{v.trend.trend}{v.trend.structure !== 'UNKNOWN' ? ` · ${v.trend.structure.replace('_', '/')}` : ''}</Row>
        <Row k="Previous session">{v.prevSession ? `H ${fmtNum(v.prevSession.high)} · L ${fmtNum(v.prevSession.low)} · C ${fmtNum(v.prevSession.close)}` : '-'}</Row>
        <Row k="Gap" title={v.gap.note}>{v.gap.gapPct !== null ? `${fmtSigned(v.gap.gapPct, 2)}%${v.gap.kind ? ` (${v.gap.kind.replace('_', ' ').toLowerCase()})` : ''}` : '-'}</Row>
        <Row k="Range" title={v.compression.note}>{v.compression.compressed === null ? '-' : v.compression.compressed ? 'compressed' : 'normal'}</Row>
        <Row k={`Sector${v.sector.name !== 'Unknown' ? ` (${v.sector.name})` : ''}`}>{v.sector.changePct.value !== null ? `${fmtSigned(v.sector.changePct.value, 2)}% · rel ${fmtSigned(v.sector.relPct, 2)} pts` : <span className="muted" title={v.sector.changePct.note}>unavailable</span>}</Row>
        <Row k="vs NIFTY 50">{v.benchmark.relPct !== null ? `${fmtSigned(v.benchmark.relPct, 2)} pts (index ${fmtSigned(v.benchmark.changePct.value, 2)}%)` : <span className="muted">unavailable</span>}</Row>
      </dl>
      <div className="small muted" style={{ marginTop: 8 }}><Prov p={v.price.prov} /></div>
      {v.notes.length > 0 && <div className="small muted" style={{ marginTop: 6 }}>{v.notes.join(' ')}</div>}
    </Card>
  );
}

function MtfCard({ rows, agreement, loading }: { rows: MtfRow[] | null; agreement: { alignment: string; detail: string } | null; loading: boolean }) {
  return (
    <Card title="Timeframes" right={agreement ? <Badge kind={agreement.alignment === 'UP' ? 'pos' : agreement.alignment === 'DOWN' ? 'neg' : agreement.alignment === 'MIXED' ? 'warn' : undefined} title={agreement.detail}>{agreement.alignment}</Badge> : loading ? <Spinner /> : null}>
      {rows ? (
        <div className="tbl-wrap">
          <table data-testid="mtf-table">
            <thead><tr><th>TF</th><th>Trend</th><th>Structure</th><th>Level in play</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.tf}>
                  <td>{r.tf}</td>
                  <td>{r.availability.available ? r.trend.trend : <span className="muted" title={r.availability.reason ?? ''}>n/a</span>}</td>
                  <td>{r.availability.available ? r.trend.structure.replace('_', '/') : '-'}</td>
                  <td>{r.availability.available && r.state ? <>{r.direction === 'UP' ? 'R' : 'S'} {fmtNum(r.level)} <Lifecycle state={r.state} short /></> : '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Skeleton h={120} />
      )}
      {agreement && <div className="small muted" style={{ marginTop: 6 }}>{agreement.detail}</div>}
    </Card>
  );
}

function LevelsCard({ v }: { v: UnderlyingView }) {
  const px = v.price.value;
  const rows = [...v.levels].filter((l) => px === null || Math.abs(l.price - px) <= (v.atr.value ?? px * 0.02) * 6).sort((a, b) => b.price - a.price);
  return (
    <Card title="Why each line exists" right={<span className="small muted">{rows.length} within range</span>}>
      {rows.length === 0 ? <Empty>No levels near price on this timeframe.</Empty> : (
        <div className="tbl-wrap">
          <table data-testid="levels-table">
            <thead><tr><th>Level</th><th>Type</th><th>Method</th><th className="num">Away</th><th>Evidence</th></tr></thead>
            <tbody>
              {rows.map((l) => (
                <tr key={l.id}>
                  <td className="mono">{fmtNum(l.price)}</td>
                  <td>{l.kind === 'RESISTANCE' ? 'Resistance' : 'Support'}</td>
                  <td>{l.method.replace(/_/g, ' ').toLowerCase()}</td>
                  <td className="num">{px !== null ? `${fmtSigned(((l.price - px) / px) * 100, 2)}%` : '-'}</td>
                  <td className="small">{l.explain} <span className="muted">({l.tf}, {fmtIst(new Date(l.to * 1000).toISOString())})</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

// ---- contract builder -------------------------------------------------------------------------------------------------------------------------

function ContractBuilder({ u, selected, onSelect, isFno }: { u: string; selected: string | null; onSelect: (id: string | null) => void; isFno: boolean }) {
  const toast = useToast();
  const exp = useGet<{ expiries: string[]; lotSize: number | null; default: { expiry: string | null; reason: string } }>(isFno ? `/api/workbench/underlying/${u}/expiries` : null);
  const [expiry, setExpiry] = useState<string | null>(null);
  const [type, setType] = useState<'CE' | 'PE'>('CE');
  const chosenExpiry = expiry ?? exp.data?.data.default.expiry ?? null;
  const chain = useGet<ExplorerView>(chosenExpiry ? `/api/workbench/chain/${u}?expiry=${chosenExpiry}&each=12` : null, { poll: 30_000 });
  const add = useSend<{ id: string; note?: string }, { duplicate: boolean }>('POST', '/api/workbench/contracts', ['/api/workbench/contracts']);

  useEffect(() => { setExpiry(null); }, [u]);
  if (!isFno) return <Card title="Option contract"><p className="small muted">{u} is not in the F&O segment, so it has no listed option contracts. You can still analyse the underlying and add it to your equity watchlist.</p></Card>;
  if (exp.isError) return <Card title="Option contract"><div className="banner neg small" role="alert">{errMsg(exp.error)}</div></Card>;

  const selRef = selected?.split('|') ?? [];
  const rows = chain.data?.data.rows ?? [];
  const idFor = (strike: number) => `${u}|${chosenExpiry}|${type}|${strike}`;
  const doAdd = () => {
    if (!selected) return;
    add.mutate({ id: selected }, {
      onSuccess: (r) => toast(r.data.duplicate ? 'Already in your watchlist. Nothing changed.' : 'Contract added to your watchlist'),
      onError: (e) => toast(errMsg(e), true),
    });
  };

  return (
    <Card title="Option contract" right={chain.data ? <StateBadge state={chain.data.data.state} /> : chain.isFetching ? <Spinner /> : null}>
      <div className="stack" data-testid="contract-builder">
        <div className="row">
          <label className="small muted" htmlFor="wb-expiry">Expiry</label>
          <select id="wb-expiry" style={{ width: 'auto' }} value={chosenExpiry ?? ''} onChange={(e) => { setExpiry(e.target.value); onSelect(null); }} disabled={!exp.data}>
            {exp.data?.data.expiries.map((e) => <option key={e} value={e}>{e}</option>)}
          </select>
          <Segmented value={type} onChange={(t) => { setType(t); onSelect(null); }} label="Call or put" options={[{ value: 'CE', label: 'Call (CE)' }, { value: 'PE', label: 'Put (PE)' }]} />
        </div>
        {exp.data && !expiry && <div className="small muted">Default expiry: {exp.data.data.default.reason} Change it above.</div>}
        {chain.isError && <div className="banner neg small" role="alert">{errMsg(chain.error)}</div>}
        {chain.isPending && chosenExpiry && <Skeleton h={80} />}
        {chain.data && (
          <>
            <div className="small muted">Spot {chain.data.data.spot.value !== null ? fmtNum(chain.data.data.spot.value) : 'unknown'} · lot size {chain.data.data.lotSize.value ?? 'unknown'} · {rows.length} of {chain.data.data.totalStrikes} strikes around the money · {chain.data.data.provenance.source}, {chain.data.data.provenance.asOf ? `${fmtIst(chain.data.data.provenance.asOf)} IST` : 'no provider time'}</div>
            <div className="row">
              <label className="small muted" htmlFor="wb-strike">Strike</label>
              <select id="wb-strike" style={{ width: 'auto', minWidth: 220 }} value={selRef[3] && selRef[2] === type && selRef[1] === chosenExpiry ? selRef[3] : ''} onChange={(e) => onSelect(e.target.value ? idFor(Number(e.target.value)) : null)}>
                <option value="">Choose a strike…</option>
                {rows.map((r) => {
                  const c: ContractValidation = type === 'CE' ? r.ce : r.pe;
                  const m = type === 'CE' ? r.moneyness.ce : r.moneyness.pe;
                  return <option key={r.strike} value={r.strike}>{r.strike} · {m} · {c.ltp.value !== null ? `LTP ${fmtNum(c.ltp.value)}` : 'no quote'} · {c.liquidity.cls.toLowerCase().replace('_', ' ')}</option>;
                })}
              </select>
              {chain.data.data.atmStrike !== null && <button type="button" className="btn sm" onClick={() => onSelect(idFor(chain.data!.data.atmStrike!))} title="Picks the strike nearest the underlying price. You can change it.">Use ATM ({chain.data.data.atmStrike})</button>}
            </div>
          </>
        )}
        <div className="row">
          <button type="button" className="btn primary" disabled={!selected || add.isPending} onClick={doAdd}><Icon name="plus" />Add to watchlist</button>
          {!selected && <span className="small muted">Choose a strike first. Nothing is selected for you.</span>}
        </div>
      </div>
    </Card>
  );
}

// ---- contract panel ---------------------------------------------------------------------------------------------------------------------------

function ContractPanel({ id, underlying }: { id: string; underlying: UnderlyingView | null }) {
  const q = useGet<ContractView>(`/api/workbench/contract?id=${encodeURIComponent(id)}`, { poll: 30_000 });
  return (
    <Async q={q} skeleton={<Card><Skeleton h={220} /></Card>}>
      {(d) => {
        const v = d.validation;
        const inv = underlying?.structure.primary;
        return (
          <Card title={d.label} right={<StateBadge state={v.state} />}>
            <div className="stack" data-testid="contract-panel">
              {!v.quoteAvailable && <div className="banner warn small" role="note">{v.notes.find((n) => /No quote/.test(n)) ?? 'No quote for this contract.'} Prices below are unknown, not zero.</div>}
              <div className="grid g2 keep" style={{ gap: 12 }}>
                <dl className="kv">
                  <Row k="Underlying"><Sv f={d.underlying.spot} /></Row>
                  <Row k="Option LTP"><Sv f={v.ltp} /></Row>
                  <Row k="Bid / Ask"><Sv f={v.bid} hideBadge /> / <Sv f={v.ask} hideBadge /></Row>
                  <Row k="Bid / Ask qty (units)"><Sv f={v.bidQty} digits={0} hideBadge /> / <Sv f={v.askQty} digits={0} hideBadge /></Row>
                  <Row k="Spread"><Sv f={v.spreadAbs} hideBadge /> (<Sv f={v.spreadPct} suffix="%" hideBadge />)</Row>
                  <Row k="Lot size">{v.lotSize ?? <span className="muted">unknown</span>}</Row>
                  <Row k="Premium for one lot"><Sv f={v.oneLotPremium} digits={0} prefix="₹" /></Row>
                </dl>
                <dl className="kv">
                  <Row k="Traded volume (contracts)" title="Contracts traded today. Different from open interest."><Sv f={v.volume} digits={0} /></Row>
                  <Row k="Open interest (contracts)" title="Positions outstanding. Different from volume."><Sv f={v.oi} digits={0} /></Row>
                  <Row k="OI change vs previous day"><Sv f={v.oiChange} digits={0} /></Row>
                  <Row k="Implied volatility"><Sv f={v.iv} suffix="%" /></Row>
                  <Row k="Intrinsic / time value"><Sv f={v.intrinsic} hideBadge /> / <Sv f={v.timeValue} hideBadge /></Row>
                  <Row k="Expiry">{v.ref.expiry}{v.daysToExpiry !== null ? ` · ${v.daysToExpiry.toFixed(1)} days left` : ' · expired'}</Row>
                  <Row k="Liquidity"><Badge kind={v.liquidity.cls === 'LIQUID' ? 'pos' : v.liquidity.cls === 'ILLIQUID' ? 'neg' : v.liquidity.cls === 'MODERATE' ? 'info' : 'warn'} title="Describes how tradable the quote is. It says nothing about whether a trade is safe or sensible.">{v.liquidity.cls.replace('_', ' ')}</Badge></Row>
                </dl>
              </div>
              <div>
                <div className="small" style={{ fontWeight: 650 }}>Greeks {v.delta.prov?.note === 'calculated' ? <Badge title={v.delta.note}>calculated, not provider-supplied</Badge> : <Badge kind="pos">provider-supplied</Badge>}</div>
                <dl className="kv" style={{ marginTop: 6 }}>
                  <Row k="Delta"><Sv f={v.delta} digits={3} hideBadge /></Row>
                  <Row k="Gamma"><Sv f={v.gamma} digits={5} hideBadge /></Row>
                  <Row k="Theta (per day)"><Sv f={v.theta} digits={2} hideBadge /></Row>
                  <Row k="Vega (per 1 vol pt)"><Sv f={v.vega} digits={2} hideBadge /></Row>
                  <Row k="Premium move per 1% underlying" title={v.leverage.note}><Sv f={v.leverage} digits={1} suffix="%" hideBadge /></Row>
                </dl>
                {v.delta.note && v.delta.value !== null && <div className="small muted">{v.delta.note}</div>}
              </div>
              {inv && inv.invalidationPrice !== null && (
                <div className="small" data-testid="underlying-invalidation"><strong>Underlying invalidation:</strong> a completed {inv.tf} close {inv.direction === 'UP' ? 'below' : 'above'} {fmtNum(inv.invalidationPrice)} invalidates the {inv.direction === 'UP' ? 'upside' : 'downside'} setup on the underlying. The option can lose value earlier through time decay or falling volatility.</div>
              )}
              {d.underlying.crossCheck.state === 'CONFLICT' && <div className="banner neg small" role="alert">Underlying price conflict: {d.underlying.crossCheck.note}</div>}
              <Disclosure summary="Why this liquidity class">
                <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{v.liquidity.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
              </Disclosure>
              <div className="small muted"><Prov p={v.ltp.prov ?? v.bid.prov ?? v.oi.prov} state={v.state} /></div>
              {[...d.notes, ...v.notes].length > 0 && <div className="small muted">{[...new Set([...d.notes, ...v.notes])].join(' ')}</div>}
              <div className="small muted">{NOT_ADVICE}</div>
            </div>
          </Card>
        );
      }}
    </Async>
  );
}

function OptionChartPanel({ id, tf, syncKey }: { id: string; tf: Timeframe; syncKey: string }) {
  const q = useGet<ContractChart>(`/api/workbench/contract/chart?id=${encodeURIComponent(id)}&tf=${tf}`, { poll: 45_000 });
  return (
    <Async q={q} skeleton={<Skeleton h={220} />}>
      {(c) => (
        <div data-testid="option-chart-panel">
          <div className="row between" style={{ marginBottom: 6 }}>
            <strong className="small">Option premium · {TF_LABEL[tf]}</strong>
            <StateBadge state={c.state} label={c.kind === 'CANDLES' ? 'CANDLES' : c.kind === 'OBSERVED' ? 'SNAPSHOTS' : undefined} />
          </div>
          {c.kind === 'CANDLES' && c.candles.length > 0 ? (
            <WbChart candles={c.candles} tf={tf} overlays={{ vwap: false, ema20: false, ema50: false, bb: false }} panes={{ rsi: false, macd: false, atr: false }} levels={[]} levelGroups={{ swing: false, session: false, opening: false, rolling: false }} setup={null} showSetup={false} events={[]} showEvents={false} atr={null} syncKey={syncKey} height={220} label={`Option candlestick chart, ${tf}`} />
          ) : c.kind === 'OBSERVED' ? (
            <ObservedChart points={c.points} syncKey={syncKey} />
          ) : (
            <Empty>{c.note}</Empty>
          )}
          <div className="small muted" style={{ marginTop: 6 }}>{c.note}</div>
        </div>
      )}
    </Async>
  );
}

// ---- page ----------------------------------------------------------------------------------------------------------------------------------------------

export function Workbench() {
  const loc = useLoc();
  const toast = useToast();
  const u = (loc.query.get('u') ?? '').toUpperCase() || null;
  const tfRaw = loc.query.get('tf') ?? '15m';
  const tf = (TIMEFRAMES as readonly string[]).includes(tfRaw) ? (tfRaw as Timeframe) : '15m';
  const contractId = loc.query.get('c');
  const [prefs, setPrefs] = usePrefs();

  const pick = (sym: string, cid?: string) => openWorkbench(sym, cid, tf);
  const watchUnderlying = useSend<{ symbol: string }>('POST', '/api/watchlist', ['/api/watchlist']);

  const view = useGet<UnderlyingView>(u ? `/api/workbench/underlying/${encodeURIComponent(u)}?tf=${tf}` : null, { poll: 30_000 });
  const mtf = useGet<{ rows: MtfRow[]; agreement: { alignment: string; detail: string } }>(u && view.data ? `/api/workbench/underlying/${encodeURIComponent(u)}/mtf` : null, { poll: 90_000 });
  const events = useGet<EventsView>(u && view.data?.data.instrument.kind === 'STOCK' ? `/api/workbench/events?symbols=${encodeURIComponent(u)}` : null);

  const v = view.data?.data ?? null;
  const markers = useMemo(() => (v && events.data ? chartMarkers(events.data.data.events, v.candles, tfSec(tf)) : []), [v, events.data, tf]);
  const syncKey = `wb:${u}:${tf}`;

  return (
    <div className="page">
      <PageHead title="Options Workbench" sub="Underlying, option chain and contract validation in one place. Read-only research: no orders can be placed here." />
      <ContextStrip />
      <SearchBox onPick={pick} />

      {!u && (
        <Card title="Start here">
          <p className="small">Search for an Indian stock or index above, choose it, then pick an expiry, call or put, and a strike from the live option chain. Nothing is chosen for you.</p>
          <div className="row" style={{ marginTop: 8 }}>
            {['NIFTY', 'BANKNIFTY', 'RELIANCE', 'HDFCBANK', 'INFY'].map((s) => <button key={s} type="button" className="btn sm" onClick={() => pick(s)}>{s}</button>)}
          </div>
        </Card>
      )}

      {u && view.isPending && <Card><Skeleton h={380} /></Card>}
      {u && view.isError && (
        <div className="banner neg" role="alert">
          <div style={{ flex: 1 }}><strong>Could not load {u}.</strong><div className="small">{errMsg(view.error)}{view.error instanceof ApiError && view.error.status === 404 ? ' Check the symbol and try again.' : ''}</div></div>
          <button className="btn sm" onClick={() => void view.refetch()}>Retry</button>
        </div>
      )}

      {u && v && (
        <>
          <div className="row between" data-testid="underlying-header">
            <div>
              <h2 style={{ margin: 0 }}>{v.instrument.id} <span className="muted small">{v.instrument.name}</span></h2>
              <div className="row" style={{ gap: 10 }}>
                <span className="mono" style={{ fontSize: 20 }}>{v.price.value !== null ? fmtNum(v.price.value) : '-'}</span>
                <span className={v.changePct.value === null ? 'muted' : v.changePct.value >= 0 ? 'pos' : 'neg'}>{v.change.value !== null ? `${fmtSigned(v.change.value)} (${fmtPct(v.changePct.value)})` : ''}</span>
                <StateBadge state={v.freshness} />
                <span className="small muted">{v.price.prov ? `${v.price.prov.source} · ${v.price.prov.latency.toLowerCase()}${v.price.prov.asOf ? ` · ${fmtIst(v.price.prov.asOf)} IST` : ''}` : 'no quote'}</span>
              </div>
            </div>
            <div className="row">
              {v.instrument.kind === 'STOCK' && <button type="button" className="btn sm" onClick={() => watchUnderlying.mutate({ symbol: v.instrument.id }, { onSuccess: () => toast(`${v.instrument.id} added to the equity watchlist`), onError: (e) => toast(errMsg(e), true) })}><Icon name="plus" />Watch underlying</button>}
              <button type="button" className="btn sm" onClick={() => { void view.refetch(); void mtf.refetch(); }}><Icon name="refresh" />Refresh</button>
            </div>
          </div>

          <div className="grid g-main">
            <div className="stack">
              <Card
                title="Charts"
                right={<Segmented value={prefs.view} onChange={(x) => setPrefs({ ...prefs, view: x })} label="Chart view" options={[{ value: 'both', label: 'Both' }, { value: 'underlying', label: 'Underlying' }, { value: 'option', label: 'Option' }]} />}
              >
                <div className="stack">
                  <TfBar tf={tf} onTf={(t) => setQuery('tf', t)} mtf={mtf.data?.data.rows ?? null} current={v.availability} />
                  <ChartOptions prefs={prefs} setPrefs={setPrefs} intraday={tf !== '1d'} />
                  {(prefs.view !== 'option' || !contractId) && (
                    v.availability.available || v.candles.length > 0 ? (
                      <>
                        {!v.availability.available && <div className="banner warn small" role="note">{v.availability.reason} The chart is shown for reference only; no indicators or breakout state are computed.</div>}
                        <WbChart candles={v.candles} tf={tf} overlays={prefs.overlays} panes={prefs.panes} levels={v.levels} levelGroups={prefs.levelGroups} setup={v.structure.primary} showSetup={prefs.showSetup} events={markers} showEvents={prefs.showEvents} atr={v.atr.value} syncKey={syncKey} label={`${v.instrument.id} ${TF_LABEL[tf]} candlestick chart with volume`} />
                        {prefs.showEvents && markers.length > 0 && (
                          <div className="small" data-testid="event-markers">
                            <strong>Markers on the chart:</strong> {markers.map((m) => `${m.type === 'EARNINGS' ? 'E' : 'N'}: ${m.headline.slice(0, 70)}${m.afterHours ? ' (published after hours)' : ''}`).join(' · ')}
                          </div>
                        )}
                      </>
                    ) : (
                      <Empty>{v.availability.reason ?? 'No candles for this timeframe.'}</Empty>
                    )
                  )}
                  {contractId && prefs.view !== 'underlying' && <OptionChartPanel id={contractId} tf={tf} syncKey={syncKey} />}
                  {!contractId && prefs.view === 'option' && <Empty>Choose a contract below to see its chart.</Empty>}
                </div>
              </Card>
              <ContractBuilder u={v.instrument.id} selected={contractId} onSelect={(id) => setQuery('c', id)} isFno={v.instrument.fno} />
              {contractId && <ContractPanel id={contractId} underlying={v} />}
              <LevelsCard v={v} />
            </div>
            <div className="stack">
              <BreakoutCard v={v} />
              <UnderlyingCard v={v} />
              <MtfCard rows={mtf.data?.data.rows ?? null} agreement={mtf.data?.data.agreement ?? null} loading={mtf.isFetching} />
              {events.data && (
                <Card title="Latest events" right={<a href={`#/events?symbols=${v.instrument.id}`} className="small">All events</a>}>
                  {events.data.data.events.length === 0 ? <p className="small muted">No recent items found. This does not mean none exist.</p> : (
                    <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
                      {events.data.data.events.slice(0, 5).map((e) => <li key={e.id}>{e.headline.slice(0, 110)} <span className="muted">· {e.publisher}{e.publishedAt ? `, ${fmtIst(e.publishedAt)} IST` : e.scheduledFor ? `, scheduled ${e.scheduledFor}` : ''}</span></li>)}
                    </ul>
                  )}
                </Card>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

