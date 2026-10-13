import { useState } from 'react';
import type { ChainView } from '../../server/services/options';
import { OPTION_UNDERLYINGS } from '../../shared/universe';
import { fmtNum, fmtPct, fmtQty, fmtSigned } from '../../shared/format';
import { OiChart } from '../charts';
import { Async, Badge, Card, PageHead, Segmented, Skeleton, Stat } from '../ui';
import { useGet, usePollMs } from '../api';

type ChainResp = ChainView & { expiry: string; expiries: string[] };

export function Options() {
  const [u, setU] = useState('NIFTY');
  const [expiry, setExpiry] = useState<string | null>(null);
  const poll = usePollMs();
  const q = useGet<ChainResp>(`/api/options/${u}/chain${expiry ? `?expiry=${expiry}` : ''}`, { poll });

  return (
    <div className="page">
      <PageHead
        title="Option chain"
        sub="Open-interest structure and market-implied move. Facts about positioning, not trade signals."
        right={
          <>
            <Segmented value={u} onChange={(v) => { setU(v); setExpiry(null); }} label="Underlying" options={OPTION_UNDERLYINGS.map((o) => ({ value: o.id, label: o.name }))} />
            {q.data && (
              <select aria-label="Expiry" style={{ width: 'auto' }} value={q.data.data.expiry} onChange={(e) => setExpiry(e.target.value)}>
                {q.data.data.expiries.map((e) => <option key={e} value={e}>{e}</option>)}
              </select>
            )}
          </>
        }
      />
      <Async q={q} skeleton={<Card><Skeleton h={320} /></Card>}>
        {(d) => {
          const a = d.analytics;
          return (
            <>
              <div className="grid g4">
                <Card><Stat small label="Spot" value={fmtNum(a.spot)} sub={a.atmStrike ? `ATM ${a.atmStrike}` : undefined} /></Card>
                <Card><Stat small label="PCR (OI)" value={a.pcrOi !== null ? a.pcrOi.toFixed(2) : '-'} sub={a.pcrRead.label} /></Card>
                <Card><Stat small label="Max pain" value={a.maxPain ?? '-'} sub={a.spot && a.maxPain ? `${fmtSigned(a.maxPain - a.spot, 0)} pts from spot` : undefined} /></Card>
                <Card><Stat small label="Implied move to expiry" value={a.impliedMovePct !== null ? fmtPct(a.impliedMovePct, 2, false) : '-'} sub={a.impliedRange ? `${fmtNum(a.impliedRange.low, 0)} – ${fmtNum(a.impliedRange.high, 0)}` : undefined} /></Card>
              </div>
              <div className="grid g-main">
                <Card title="Open interest by strike" right={<span className="small muted">{d.source} · {d.rows.length} strikes</span>}>
                  <OiChart rows={d.rows} spot={a.spot} atm={a.atmStrike} maxPain={a.maxPain} />
                </Card>
                <div className="stack">
                  <Card title="Reading">
                    <p className="small"><strong>{a.pcrRead.label}.</strong> <span className="muted">{a.pcrRead.detail}</span></p>
                    <dl className="kv" style={{ marginTop: 10 }}>
                      <dt>ATM IV</dt><dd>{a.atmIv !== null ? `${a.atmIv.toFixed(1)}%` : '-'}</dd>
                      <dt>Put-call IV skew</dt><dd>{a.ivSkew !== null ? fmtSigned(a.ivSkew, 1) : '-'}</dd>
                      <dt>ATM straddle</dt><dd>{fmtNum(a.atmStraddle)}</dd>
                      <dt>Total call OI</dt><dd>{fmtQty(a.totalCallOi)} <span className="small muted">({a.totalCallOiChange !== null ? fmtQty(a.totalCallOiChange) : 'change n/a'})</span></dd>
                      <dt>Total put OI</dt><dd>{fmtQty(a.totalPutOi)} <span className="small muted">({a.totalPutOiChange !== null ? fmtQty(a.totalPutOiChange) : 'change n/a'})</span></dd>
                      {d.lotSize && (<><dt>Lot size</dt><dd>{d.lotSize}</dd></>)}
                    </dl>
                  </Card>
                  <Card title="OI walls">
                    <div className="small muted">Highest call OI</div>
                    <div className="row" style={{ marginBottom: 8 }}>{a.callWalls.map((w) => <Badge key={w.strike} kind="neg" title={`OI ${fmtQty(w.oi)}, change ${fmtSigned(w.oiChange, 0)}`}>{w.strike}</Badge>)}</div>
                    <div className="small muted">Highest put OI</div>
                    <div className="row" style={{ marginBottom: 8 }}>{a.putWalls.map((w) => <Badge key={w.strike} kind="pos" title={`OI ${fmtQty(w.oi)}, change ${fmtSigned(w.oiChange, 0)}`}>{w.strike}</Badge>)}</div>
                    <div className="small muted">Fresh writing today</div>
                    <div className="row">
                      {a.freshCallWriting.slice(0, 2).map((w) => <Badge key={`c${w.strike}`} title={w.oiChange !== null ? `+${fmtQty(w.oiChange)} OI` : 'OI change unknown'}>CE {w.strike}</Badge>)}
                      {a.freshPutWriting.slice(0, 2).map((w) => <Badge key={`p${w.strike}`} title={w.oiChange !== null ? `+${fmtQty(w.oiChange)} OI` : 'OI change unknown'}>PE {w.strike}</Badge>)}
                    </div>
                  </Card>
                </div>
              </div>
              <Card title="Chain" flush>
                <div className="tbl-wrap" style={{ maxHeight: 520, overflowY: 'auto' }}>
                  <table>
                    <thead>
                      <tr><th className="num">CE OI</th><th className="num">CE chg</th><th className="num">CE IV</th><th className="num">CE LTP</th><th className="center">Strike</th><th className="num">PE LTP</th><th className="num">PE IV</th><th className="num">PE chg</th><th className="num">PE OI</th></tr>
                    </thead>
                    <tbody>
                      {d.rows.map((r) => {
                        const atm = r.strike === a.atmStrike;
                        const chg = (l: typeof r.ce) => (l.oi !== null && l.prevOi !== null ? l.oi - l.prevOi : null);
                        return (
                          <tr key={r.strike} style={atm ? { background: 'var(--card-2)' } : undefined}>
                            <td className="num">{fmtQty(r.ce.oi)}</td>
                            <td className="num">{chg(r.ce) !== null ? fmtQty(chg(r.ce)) : '-'}</td>
                            <td className="num">{r.ce.iv !== null ? r.ce.iv.toFixed(1) : '-'}</td>
                            <td className="num">{fmtNum(r.ce.ltp)}</td>
                            <td className="center"><strong>{r.strike}</strong>{atm && <> <Badge kind="info">ATM</Badge></>}</td>
                            <td className="num">{fmtNum(r.pe.ltp)}</td>
                            <td className="num">{r.pe.iv !== null ? r.pe.iv.toFixed(1) : '-'}</td>
                            <td className="num">{chg(r.pe) !== null ? fmtQty(chg(r.pe)) : '-'}</td>
                            <td className="num">{fmtQty(r.pe.oi)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </Card>
              {a.notes.length > 0 && <div className="small muted">{a.notes.join(' ')}</div>}
            </>
          );
        }}
      </Async>
    </div>
  );
}
