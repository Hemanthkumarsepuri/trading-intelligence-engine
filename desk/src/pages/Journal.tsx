import { useMemo, useState } from 'react';
import type { JournalData, TradeRow } from '../../server/services/journalView';
import { tradeEconomics } from '../../shared/charges';
import { fmtIst, fmtNum, fmtPct } from '../../shared/format';
import { VIOLATION_LABEL, type GroupStat } from '../../shared/journal';
import { DEFAULT_SETTINGS, type Segment, type Settings, type Side, type Trade } from '../../shared/types';
import { setQuery, useLoc } from '../nav';
import { useGet, useSend } from '../api';
import { AreaChart, BarChart } from '../charts';
import { Async, Badge, Card, Empty, errMsg, Field, Money, NumInput, PageHead, Segmented, Skeleton, Stat, useConfirm, useToast } from '../ui';
import { TradeForm } from './TradeForm';

type Tab = 'overview' | 'trades' | 'discipline' | 'tax' | 'tools';
const SEG_LABEL: Record<string, string> = { EQ_DELIVERY: 'Delivery', EQ_INTRADAY: 'Intraday', FUT: 'Futures', OPT: 'Options' };

function GroupTable({ title, rows }: { title: string; rows: GroupStat[] }) {
  return (
    <Card title={title} flush>
      {rows.length === 0 ? <Empty>No closed trades yet.</Empty> : (
        <table>
          <thead><tr><th>{title.replace('By ', '')}</th><th className="num">Trades</th><th className="num">Win %</th><th className="num">Avg net</th><th className="num">Net</th></tr></thead>
          <tbody>
            {rows.slice(0, 8).map((r) => (
              <tr key={r.key}><td>{SEG_LABEL[r.key] ?? r.key}</td><td className="num">{r.trades}</td><td className="num">{(r.winRate * 100).toFixed(0)}%</td><td className="num"><Money v={r.avgNet} signed /></td><td className="num"><Money v={r.net} signed /></td></tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function Overview({ j }: { j: JournalData }) {
  const s = j.stats;
  if (s.trades === 0) return <Card><Empty>No closed trades yet. Log a trade with an exit price to see statistics.</Empty></Card>;
  return (
    <>
      <div className="grid g4">
        <Card><Stat label="Net P&L (after charges)" value={<Money v={s.netPnl} signed />} sub={`${s.trades} trades · gross ${fmtNum(s.grossPnl, 0)}`} /></Card>
        <Card><Stat label="Win rate" value={s.winRate !== null ? `${(s.winRate * 100).toFixed(0)}%` : '-'} sub={`${s.wins}W / ${s.losses}L`} /></Card>
        <Card><Stat label="Expectancy / trade" value={<Money v={s.expectancy} signed />} sub={s.avgR !== null ? `Avg ${s.avgR.toFixed(2)}R` : 'Set stop losses to see R'} /></Card>
        <Card><Stat label="Profit factor" value={s.profitFactor !== null ? s.profitFactor.toFixed(2) : '-'} sub={s.payoffRatio !== null ? `Payoff ${s.payoffRatio.toFixed(2)}` : undefined} /></Card>
      </div>
      <div className="grid g4">
        <Card><Stat small label="Max drawdown" value={<Money v={-s.maxDrawdown} />} sub={s.maxDrawdownPct !== null ? fmtPct(-s.maxDrawdownPct, 1) + ' of capital' : undefined} /></Card>
        <Card><Stat small label="Charges paid" value={<Money v={s.totalCharges} />} sub={s.chargesPctOfGrossProfit !== null ? `${s.chargesPctOfGrossProfit.toFixed(0)}% of gross profit` : undefined} /></Card>
        <Card><Stat small label="Streaks" value={`${s.longestWinStreak}W / ${s.longestLossStreak}L`} sub={s.currentStreak.kind ? `Current ${s.currentStreak.n}${s.currentStreak.kind}` : undefined} /></Card>
        <Card><Stat small label="Best / worst" value={<><Money v={s.best} signed /> / <Money v={s.worst} signed /></>} /></Card>
      </div>
      <div className="grid g-main">
        <Card title="Equity curve (realised, after charges)"><AreaChart points={j.equity.map((p) => ({ label: p.date, value: p.equity }))} baseline={j.capital} /></Card>
        <Card title="Daily P&L"><BarChart points={j.equity.slice(-40).map((p) => ({ label: p.date.slice(5), value: p.pnl }))} /></Card>
      </div>
      <div className="grid g2">
        <GroupTable title="By setup" rows={j.groups.setup} />
        <GroupTable title="By segment" rows={j.groups.segment} />
        <GroupTable title="By weekday" rows={j.groups.weekday} />
        <GroupTable title="By emotion (1 calm – 5 heated)" rows={j.groups.emotion} />
      </div>
    </>
  );
}

function Trades({ j }: { j: JournalData }) {
  const toast = useToast();
  const confirm = useConfirm();
  const [editing, setEditing] = useState<Trade | 'new' | null>(null);
  const [filter, setFilter] = useState<'all' | 'open' | 'closed'>('all');
  const [search, setSearch] = useState('');
  const del = useSend<string>('DELETE', (id) => `/api/trades/${id}`, ['/api/journal', '/api/risk']);
  const setups = useMemo(() => [...new Set(j.rows.map((r) => r.trade.setup).filter(Boolean))], [j.rows]);
  const rows = j.rows.filter((r) => (filter === 'all' || (filter === 'open') === (r.trade.exitPrice === null)) && (!search || r.trade.symbol.includes(search.toUpperCase())));

  return (
    <>
      <div className="row between">
        <div className="row">
          <Segmented value={filter} onChange={setFilter} label="Filter" options={[{ value: 'all', label: `All (${j.rows.length})` }, { value: 'open', label: 'Open' }, { value: 'closed', label: 'Closed' }]} />
          <input placeholder="Filter symbol" value={search} onChange={(e) => setSearch(e.target.value)} style={{ width: 150 }} aria-label="Filter by symbol" />
        </div>
        <button className="btn primary" onClick={() => setEditing('new')}>Log trade</button>
      </div>
      <Card flush>
        {rows.length === 0 ? <Empty>No trades match.</Empty> : (
          <div className="tbl-wrap">
            <table>
              <thead><tr><th>Symbol</th><th>Side</th><th>Entry</th><th className="num">Qty</th><th className="num">In → Out</th><th className="num">Net P&L</th><th className="num">R</th><th>Flags</th><th /></tr></thead>
              <tbody>
                {rows.map((r: TradeRow) => (
                  <tr key={r.trade.id}>
                    <td><strong>{r.trade.symbol}</strong><div className="small muted">{SEG_LABEL[r.trade.segment]}{r.trade.setup ? ` · ${r.trade.setup}` : ''}</div></td>
                    <td><Badge kind={r.trade.side === 'LONG' ? 'pos' : 'neg'}>{r.trade.side === 'LONG' ? 'Long' : 'Short'}</Badge></td>
                    <td className="small">{fmtIst(r.trade.entryAt)}</td>
                    <td className="num">{fmtNum(r.trade.qty, 0)}</td>
                    <td className="num">{fmtNum(r.trade.entryPrice)} → {r.trade.exitPrice !== null ? fmtNum(r.trade.exitPrice) : <Badge kind="info">open</Badge>}</td>
                    <td className="num">{r.net !== null ? <Money v={r.net} digits={0} signed /> : '-'}</td>
                    <td className="num">{r.rMultiple !== null ? `${r.rMultiple.toFixed(2)}R` : '-'}</td>
                    <td className="wrap" style={{ maxWidth: 220 }}>{r.violations.map((v) => <span key={v} className="chip" style={{ color: 'var(--warn)' }}>{VIOLATION_LABEL[v]}</span>)}</td>
                    <td className="right">
                      <button className="btn sm" onClick={() => setEditing(r.trade)}>{r.trade.exitPrice === null ? 'Close / edit' : 'Edit'}</button>{' '}
                      <button className="btn sm danger" onClick={() => { if (confirm(`Delete this ${r.trade.symbol} trade?`)) del.mutate(r.trade.id, { onSuccess: () => toast('Trade deleted'), onError: (e) => toast(errMsg(e), true) }); }}>Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {editing && <TradeForm trade={editing === 'new' ? undefined : editing} setups={setups} onClose={() => setEditing(null)} />}
    </>
  );
}

function Discipline({ j }: { j: JournalData }) {
  const d = j.discipline;
  const r = j.risk;
  return (
    <div className="grid g2">
      <Card title="Today's risk guard" right={<Badge kind={r.state === 'BREACHED' ? 'neg' : r.state === 'WARN' ? 'warn' : 'pos'}>{r.state === 'OK' ? 'Within limits' : r.state === 'WARN' ? 'Caution' : 'Limit reached'}</Badge>}>
        <div className="stack">
          <div>
            <div className="row between small"><span>Daily loss limit used</span><span>{r.lossLimitUsedPct.toFixed(0)}% of <Money v={r.dailyLossLimit} /></span></div>
            <div className={`bar ${r.lossLimitUsedPct >= 100 ? 'neg' : r.lossLimitUsedPct >= 70 ? 'warn' : ''}`}><i style={{ width: `${Math.min(100, r.lossLimitUsedPct)}%` }} /></div>
          </div>
          <dl className="kv">
            <dt>Realised today</dt><dd><Money v={r.realizedToday} signed /></dd>
            <dt>Trades today</dt><dd>{r.tradesToday} of {r.maxTrades}</dd>
            <dt>Open positions</dt><dd>{r.openPositions} of {r.maxOpenPositions}</dd>
          </dl>
          <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>{r.messages.map((m) => <li key={m}>{m}</li>)}</ul>
          <p className="small faint">Limits come from Settings. This guard informs you; it cannot stop an order at your broker.</p>
        </div>
      </Card>
      <Card title={`Discipline, last ${d.windowDays} days`}>
        {d.trades === 0 ? <Empty>No trades in this window.</Empty> : (
          <div className="stack">
            <div className="grid g2 keep">
              <Stat label="Rule-following score" value={d.score !== null ? `${d.score.toFixed(0)}%` : '-'} sub={`${d.cleanTrades} of ${d.trades} trades with no violation`} />
              <Stat label="Clean vs rule-breaking P&L" value={<><Money v={d.cleanNet} signed /> / <Money v={d.violatedNet} signed /></>} sub="Net of trades that followed vs broke the rules" />
            </div>
            {d.byViolation.length > 0 ? (
              <table><tbody>{d.byViolation.map((v) => <tr key={v.code}><td>{v.label}</td><td className="num">{v.count}</td></tr>)}</tbody></table>
            ) : <p className="small pos">No violations in this window.</p>}
          </div>
        )}
      </Card>
    </div>
  );
}

function Tax({ j }: { j: JournalData }) {
  const t = j.tax;
  return (
    <>
      <div className="row">
        <Field label="Financial year">{(id) => <select id={id} value={j.fy} onChange={(e) => setQuery('fy', e.target.value)} style={{ width: 'auto' }}>{j.fys.map((f) => <option key={f} value={f}>FY {f}</option>)}</select>}</Field>
      </div>
      <div className="banner info small">Estimates from your logged trades only, for planning. Not tax advice; confirm with a chartered accountant and your broker's tax P&L before filing.</div>
      <div className="grid g2">
        <Card title="Capital gains: equity delivery">
          <dl className="kv">
            <dt>Short-term gain (net of set-off)</dt><dd><Money v={t.stcgNet} signed /></dd>
            <dt>Long-term gain (net of set-off)</dt><dd><Money v={t.ltcgNet} signed /></dd>
            <dt>LTCG exemption used</dt><dd><Money v={t.ltcgExemptionUsed} /></dd>
            <dt>Taxable STCG @ 20%</dt><dd><Money v={t.stcgTaxable} /></dd>
            <dt>Taxable LTCG @ 12.5%</dt><dd><Money v={t.ltcgTaxable} /></dd>
            <dt><strong>Estimated tax incl. 4% cess</strong></dt><dd><strong><Money v={t.capitalGainsTax} /></strong></dd>
            {(t.stclCarryForward > 0 || t.ltclCarryForward > 0) && (<><dt>Loss to carry forward (ST / LT)</dt><dd><Money v={t.stclCarryForward} /> / <Money v={t.ltclCarryForward} /></dd></>)}
          </dl>
        </Card>
        <Card title="Business income: intraday and F&O">
          <dl className="kv">
            <dt>Intraday equity (speculative) net</dt><dd><Money v={t.speculative.net} signed /> <span className="small muted">({t.speculative.trades} trades)</span></dd>
            <dt>Futures and options (non-speculative) net</dt><dd><Money v={t.nonSpeculative.net} signed /> <span className="small muted">({t.nonSpeculative.trades} trades)</span></dd>
            <dt>F&O turnover (ICAI method)</dt><dd><Money v={t.nonSpeculative.turnover} /></dd>
            <dt>Estimated tax at your slab rate</dt><dd><Money v={t.businessIncomeTaxEstimate} /></dd>
          </dl>
          <p className="small faint" style={{ marginTop: 10 }}>Turnover matters for tax-audit thresholds. Check current limits with your CA.</p>
        </Card>
      </div>
      {t.notes.length > 0 && <Card title="Assumptions and notes"><ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>{t.notes.map((n) => <li key={n}>{n}</li>)}</ul></Card>}
    </>
  );
}

function Tools() {
  const settings = useGet<Settings>('/api/settings');
  const s = settings.data?.data ?? DEFAULT_SETTINGS;
  const [entry, setEntry] = useState<number | null>(null);
  const [stop, setStop] = useState<number | null>(null);
  const [lot, setLot] = useState<number | null>(null);
  const [riskPct, setRiskPct] = useState<number | null>(null);
  const size = useGet<{ riskAmount: number; perUnitRisk: number; qty: number; lots: number | null; capitalRequired: number; capitalPct: number | null; warnings: string[] }>(
    entry && stop && entry !== stop ? `/api/sizing?entry=${entry}&stop=${stop}${lot ? `&lotSize=${lot}` : ''}${riskPct ? `&riskPct=${riskPct}` : ''}` : null,
  );

  const [seg, setSeg] = useState<Segment>('EQ_INTRADAY');
  const [side, setSide] = useState<Side>('LONG');
  const [qty, setQty] = useState<number | null>(100);
  const [buy, setBuy] = useState<number | null>(null);
  const [sell, setSell] = useState<number | null>(null);
  const eco = useMemo(() => {
    if (!qty || !buy || !sell) return null;
    const entryPrice = side === 'LONG' ? buy : sell;
    const exitPrice = side === 'LONG' ? sell : buy;
    const t: Trade = { id: 'calc', symbol: 'X', segment: seg, side, qty, entryPrice, exitPrice, entryAt: '', exitAt: '', stopLoss: null, target: null, setup: '', tags: [], emotion: null, planFollowed: null, notes: '', createdAt: '', updatedAt: '' };
    return tradeEconomics(t, s);
  }, [seg, side, qty, buy, sell, s]);

  return (
    <div className="grid g2">
      <Card title="Position size from risk">
        <div className="stack" style={{ gap: 12 }}>
          <div className="form-grid">
            <Field label="Entry">{(id) => <NumInput id={id} value={entry} onChange={setEntry} />}</Field>
            <Field label="Stop loss">{(id) => <NumInput id={id} value={stop} onChange={setStop} />}</Field>
            <Field label="Lot size" hint="Blank for shares">{(id) => <NumInput id={id} value={lot} onChange={setLot} />}</Field>
            <Field label="Risk %" hint={`Default ${s.riskPerTradePct}%`}>{(id) => <NumInput id={id} value={riskPct} onChange={setRiskPct} />}</Field>
          </div>
          {size.data && (
            <>
              <dl className="kv">
                <dt>Capital at risk</dt><dd><Money v={size.data.data.riskAmount} /></dd>
                <dt>Risk per unit</dt><dd>{fmtNum(size.data.data.perUnitRisk)}</dd>
                <dt><strong>Quantity</strong></dt><dd><strong>{fmtNum(size.data.data.qty, 0)}</strong>{size.data.data.lots !== null && <span className="muted"> ({size.data.data.lots} lots)</span>}</dd>
                <dt>Capital required</dt><dd><Money v={size.data.data.capitalRequired} /> {size.data.data.capitalPct !== null && <span className="small muted">({size.data.data.capitalPct.toFixed(0)}% of capital)</span>}</dd>
              </dl>
              {size.data.data.warnings.map((w) => <div key={w} className="banner warn small">{w}</div>)}
            </>
          )}
          <p className="small faint">Quantity = (capital × risk %) ÷ |entry − stop|, rounded down to whole lots. Capital: <Money v={s.capital} />.</p>
        </div>
      </Card>
      <Card title="Charges and break-even calculator">
        <div className="stack" style={{ gap: 12 }}>
          <div className="row">
            <select aria-label="Segment" value={seg} onChange={(e) => setSeg(e.target.value as Segment)} style={{ width: 'auto' }}>{Object.entries(SEG_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
            <Segmented<Side> value={side} onChange={setSide} label="Side" options={[{ value: 'LONG', label: 'Buy first' }, { value: 'SHORT', label: 'Sell first' }]} />
          </div>
          <div className="form-grid">
            <Field label="Quantity">{(id) => <NumInput id={id} value={qty} onChange={setQty} />}</Field>
            <Field label="Buy price">{(id) => <NumInput id={id} value={buy} onChange={setBuy} />}</Field>
            <Field label="Sell price">{(id) => <NumInput id={id} value={sell} onChange={setSell} />}</Field>
          </div>
          {eco && (
            <dl className="kv">
              <dt>Gross P&L</dt><dd><Money v={eco.gross} digits={2} signed /></dd>
              <dt>Brokerage</dt><dd><Money v={eco.charges.brokerage} digits={2} /></dd>
              <dt>STT</dt><dd><Money v={eco.charges.stt} digits={2} /></dd>
              <dt>Exchange + SEBI</dt><dd><Money v={eco.charges.exchange + eco.charges.sebi} digits={2} /></dd>
              <dt>Stamp duty</dt><dd><Money v={eco.charges.stamp} digits={2} /></dd>
              <dt>GST</dt><dd><Money v={eco.charges.gst} digits={2} /></dd>
              {eco.charges.dp > 0 && (<><dt>DP charge</dt><dd><Money v={eco.charges.dp} digits={2} /></dd></>)}
              <dt><strong>Total charges</strong></dt><dd><strong><Money v={eco.charges.total} digits={2} /></strong></dd>
              <dt><strong>Net P&L</strong></dt><dd><strong><Money v={eco.net} digits={2} signed /></strong></dd>
            </dl>
          )}
          <p className="small faint">Uses the statutory rates in the app and your brokerage settings. Verify against one contract note.</p>
        </div>
      </Card>
    </div>
  );
}

export function Journal() {
  const loc = useLoc();
  const fy = loc.query.get('fy');
  const [tab, setTab] = useState<Tab>('overview');
  const q = useGet<JournalData>(`/api/journal${fy ? `?fy=${fy}` : ''}`);
  return (
    <div className="page">
      <PageHead
        title="Journal"
        sub="Every trade with charges, R-multiple and rule checks."
        right={<Segmented<Tab> label="Section" value={tab} onChange={setTab} options={[{ value: 'overview', label: 'Overview' }, { value: 'trades', label: 'Trades' }, { value: 'discipline', label: 'Discipline' }, { value: 'tax', label: 'Tax' }, { value: 'tools', label: 'Tools' }]} />}
      />
      {tab === 'tools' ? <Tools /> : (
        <Async q={q} skeleton={<Card><Skeleton h={240} /></Card>}>
          {(j) => (tab === 'overview' ? <Overview j={j} /> : tab === 'trades' ? <Trades j={j} /> : tab === 'discipline' ? <Discipline j={j} /> : <Tax j={j} />)}
        </Async>
      )}
    </div>
  );
}
