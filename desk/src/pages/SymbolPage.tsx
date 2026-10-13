import { useMemo } from 'react';
import type { SymbolDetail } from '../../server/services/symbolDetail';
import { OBSERVATION_LABEL } from '../../shared/indicators';
import { fmtNum, fmtPct, fmtQty, relTime } from '../../shared/format';
import { useGet, useSend, usePollMs } from '../api';
import { CandleChart, type Level } from '../charts';
import { Async, Badge, Card, Empty, errMsg, Icon, PageHead, Pct, Signed, Skeleton, useToast } from '../ui';

export function SymbolPage({ symbol }: { symbol: string }) {
  const poll = usePollMs();
  const q = useGet<SymbolDetail>(`/api/symbols/${encodeURIComponent(symbol)}`, { poll });
  const toast = useToast();
  const add = useSend<{ symbol: string }>('POST', '/api/watchlist', ['/api/watchlist']);
  const d = q.data?.data;
  const levels = useMemo<Level[]>(() => {
    const t = d?.tech;
    if (!t) return [];
    const out: Level[] = [];
    if (t.support) out.push({ price: t.support, label: 'Support', color: '#22c55e' });
    if (t.resistance) out.push({ price: t.resistance, label: 'Resistance', color: '#f43f5e' });
    if (t.pivots) out.push({ price: t.pivots.pivot, label: 'Pivot', color: '#8b97b0' });
    return out;
  }, [d?.tech]);

  return (
    <div className="page">
      <PageHead
        title={symbol}
        sub={d?.name ? `${d.name}${d.sector ? ` · ${d.sector}` : ''}` : undefined}
        right={
          <>
            <a className="btn sm" href="#/watchlist">Back</a>
            <button className="btn sm" onClick={() => add.mutate({ symbol }, { onSuccess: () => toast(`${symbol} is on your watchlist`), onError: (e) => toast(errMsg(e), true) })}><Icon name="plus" />Watch</button>
            <a className="btn sm" href="#/alerts">Alert</a>
          </>
        }
      />
      <Async q={q} skeleton={<Card><Skeleton h={360} /></Card>}>
        {(s) => (
          <>
            <Card>
              <div className="row between" style={{ alignItems: 'flex-end' }}>
                <div>
                  <div className="stat"><div className="value" style={{ fontSize: 30 }}>{s.quote ? fmtNum(s.quote.price) : '-'}</div></div>
                  {s.quote && <div><Signed v={s.quote.change} /> (<Pct v={s.quote.changePct} />) <span className="small muted">· prev close {fmtNum(s.quote.prevClose)} · {s.quote.source}{s.quote.marketTime ? ` · traded ${relTime(s.quote.marketTime)}` : ''}</span></div>}
                </div>
                <div className="row">
                  {s.inFnoBan && <Badge kind="neg">In F&O ban</Badge>}
                  {s.lotSize && <Badge>Lot {s.lotSize}</Badge>}
                  {s.tech && <Badge kind={s.tech.trend === 'UP' ? 'pos' : s.tech.trend === 'DOWN' ? 'neg' : undefined}>Trend: {s.tech.trend.toLowerCase()}</Badge>}
                </div>
              </div>
              {s.quote && (
                <div className="row small muted" style={{ marginTop: 10, gap: 16 }}>
                  <span>Open {fmtNum(s.quote.open)}</span><span>High {fmtNum(s.quote.high)}</span><span>Low {fmtNum(s.quote.low)}</span><span>Volume {fmtQty(s.quote.volume)}</span>
                </div>
              )}
            </Card>
            <Card title="Daily chart"><CandleChart candles={s.candles} levels={levels} /></Card>
            <div className="grid g3">
              <Card title="Technicals">
                {s.tech ? (
                  <dl className="kv">
                    <dt>RSI (14)</dt><dd>{s.tech.rsi14?.toFixed(1) ?? '-'}</dd>
                    <dt>EMA 20</dt><dd>{fmtNum(s.tech.ema20)}</dd>
                    <dt>SMA 50</dt><dd>{fmtNum(s.tech.sma50)}</dd>
                    <dt>SMA 200</dt><dd>{fmtNum(s.tech.sma200)}</dd>
                    <dt>ATR (14)</dt><dd>{fmtNum(s.tech.atr14)} {s.tech.atrPct != null && <span className="small muted">({fmtPct(s.tech.atrPct, 1, false)})</span>}</dd>
                    <dt>52w high / low</dt><dd>{fmtNum(s.tech.high52w)} / {fmtNum(s.tech.low52w)}</dd>
                    <dt>Below 52w high by</dt><dd>{s.tech.distFromHigh52wPct != null ? fmtPct(s.tech.distFromHigh52wPct, 1, false) : '-'}</dd>
                    <dt>Volume vs 20d avg</dt><dd>{s.tech.volRatio20 != null ? `${s.tech.volRatio20.toFixed(2)}x` : '-'}</dd>
                    <dt>Bars used</dt><dd>{s.tech.bars}</dd>
                  </dl>
                ) : <Empty>Not enough history.</Empty>}
              </Card>
              <Card title="Levels (from price history)">
                {s.tech?.pivots ? (
                  <dl className="kv">
                    <dt>Nearest resistance</dt><dd>{fmtNum(s.tech.resistance)}</dd>
                    <dt>Nearest support</dt><dd>{fmtNum(s.tech.support)}</dd>
                    <dt>Pivot (prev day)</dt><dd>{fmtNum(s.tech.pivots.pivot)}</dd>
                    <dt>R1 / R2</dt><dd>{fmtNum(s.tech.pivots.r1)} / {fmtNum(s.tech.pivots.r2)}</dd>
                    <dt>S1 / S2</dt><dd>{fmtNum(s.tech.pivots.s1)} / {fmtNum(s.tech.pivots.s2)}</dd>
                  </dl>
                ) : <Empty>No levels available.</Empty>}
                <p className="small faint" style={{ marginTop: 10 }}>Swing highs/lows and classic floor pivots from yesterday's range. Reference levels, not predictions.</p>
              </Card>
              <Card title="Observations & delivery">
                <div>{s.tech?.observations.length ? s.tech.observations.map((o) => <span key={o} className="chip">{OBSERVATION_LABEL[o]}</span>) : <span className="muted small">Nothing notable on the daily chart.</span>}</div>
                <dl className="kv" style={{ marginTop: 12 }}>
                  <dt>Delivery %</dt><dd>{s.delivery?.deliveryPct != null ? `${s.delivery.deliveryPct.toFixed(1)}%` : '-'}</dd>
                  <dt>Delivered qty</dt><dd>{fmtQty(s.delivery?.deliveredQty)}</dd>
                  <dt>As of</dt><dd>{s.delivery?.date ?? '-'}</dd>
                </dl>
                <p className="small faint" style={{ marginTop: 10 }}>High delivery % on a big move suggests investor (not intraday) participation.</p>
              </Card>
            </div>
            <Card title="News">
              <div className="news">
                <ul>
                  {s.news.map((it) => (
                    <li key={it.id}>
                      <a href={it.link} target="_blank" rel="noopener noreferrer">{it.title}</a>
                      <div className="small muted">{it.source} · {relTime(it.publishedAt)} {it.tone !== 'neutral' && <Badge kind={it.tone === 'positive' ? 'pos' : 'neg'}>{it.tone}</Badge>}</div>
                    </li>
                  ))}
                  {s.news.length === 0 && <li className="muted">No recent headlines.</li>}
                </ul>
              </div>
            </Card>
          </>
        )}
      </Async>
    </div>
  );
}
