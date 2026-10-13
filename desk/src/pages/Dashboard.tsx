import type { Overview } from '../../server/services/market';
import type { PortfolioData } from '../../server/services/portfolio';
import type { WatchRow } from '../../server/services/watchlist';
import type { NewsItem } from '../../server/providers/news';
import type { RiskStatus } from '../../shared/journal';
import { fmtNum, relTime } from '../../shared/format';
import { go } from '../nav';
import { Async, Badge, Card, Money, PageHead, Pct, Signed, Skeleton, Stat } from '../ui';
import { useGet, usePollMs } from '../api';

function RiskBanner() {
  const q = useGet<RiskStatus>('/api/risk', { poll: 60_000 });
  const r = q.data?.data;
  if (!r) return null;
  const kind = r.state === 'BREACHED' ? 'neg' : r.state === 'WARN' ? 'warn' : 'info';
  return (
    <div className={`banner ${kind}`} role={r.state === 'OK' ? 'status' : 'alert'}>
      <div style={{ flex: 1 }}>
        <strong>Risk guard: {r.state === 'OK' ? 'within limits' : r.state === 'WARN' ? 'caution' : 'limit reached'}</strong>
        <div className="small">{r.messages.join(' ')}</div>
        <div className="row small muted" style={{ marginTop: 6, gap: 16 }}>
          <span>Today net <Money v={r.realizedToday} signed /></span>
          <span>Loss limit used {r.lossLimitUsedPct.toFixed(0)}% of <Money v={r.dailyLossLimit} /></span>
          <span>Trades {r.tradesToday}/{r.maxTrades}</span>
          <span>Open {r.openPositions}/{r.maxOpenPositions}</span>
        </div>
      </div>
      <a className="btn sm" href="#/journal">Journal</a>
    </div>
  );
}

export function Dashboard() {
  const poll = usePollMs();
  const ov = useGet<Overview>('/api/overview', { poll });
  const wl = useGet<{ rows: WatchRow[] }>('/api/watchlist', { poll });
  const pf = useGet<PortfolioData>('/api/portfolio', { poll });
  const news = useGet<{ items: NewsItem[] }>('/api/news', { poll: 5 * 60_000 });

  return (
    <div className="page">
      <PageHead title="Today" sub="Market pulse, your risk limits, watchlist and portfolio at a glance." />
      <RiskBanner />

      <Async q={ov} skeleton={<div className="ticker">{[0, 1, 2, 3].map((i) => <Card key={i}><Skeleton h={48} /></Card>)}</div>}>
        {(d) => (
          <>
            <div className="ticker">
              {d.indices.map((i) => (
                <Card key={i.id} className="stat">
                  <Stat small label={i.name} value={i.quote ? fmtNum(i.quote.price) : '-'} sub={i.quote ? <><Signed v={i.quote.change} /> (<Pct v={i.quote.changePct} />)</> : 'unavailable'} />
                </Card>
              ))}
              {d.vix && (
                <Card className="stat">
                  <Stat small label="India VIX" value={fmtNum(d.vix.quote.price)} sub={<><Pct v={d.vix.quote.changePct} /> · {d.vix.reading.label}</>} />
                </Card>
              )}
            </div>
            <div className="grid g-main">
              <div className="stack">
                {d.vix && (
                  <Card title="Volatility regime" right={<Badge kind={d.vix.reading.tone === 'alert' ? 'neg' : d.vix.reading.tone === 'warn' ? 'warn' : 'info'}>{d.vix.reading.label}</Badge>}>
                    <p className="small muted">{d.vix.reading.detail}</p>
                    {d.breadth && (
                      <p className="small" style={{ marginTop: 8 }}>
                        Breadth: <strong className="pos">{d.breadth.advances} up</strong> / <strong className="neg">{d.breadth.declines} down</strong> / {d.breadth.unchanged} flat · {d.breadth.label}
                      </p>
                    )}
                  </Card>
                )}
                <Card title="Global cues" right={<a className="small" href="#/market">More</a>}>
                  <div className="heat">
                    {d.globals.slice(0, 12).map((g) => (
                      <div key={g.id} className={g.quote ? (g.quote.changePct > 0.05 ? 'pos-bg' : g.quote.changePct < -0.05 ? 'neg-bg' : 'flat-bg') : 'flat-bg'} title={g.why}>
                        <div className="small muted">{g.name}</div>
                        <strong>{g.quote ? fmtNum(g.quote.price) : '-'}</strong>
                        <div className="small">{g.quote ? <Pct v={g.quote.changePct} /> : <span className="muted">n/a</span>}</div>
                      </div>
                    ))}
                  </div>
                </Card>
              </div>
              <div className="stack">
                <Card title="Portfolio" right={<a className="small" href="#/portfolio">Open</a>}>
                  <Async q={pf} skeleton={<Skeleton h={60} />}>
                    {(p) =>
                      p.holdings.length === 0 ? (
                        <p className="muted small">No holdings yet. Import a broker CSV from the Portfolio page.</p>
                      ) : (
                        <div className="grid g2 keep">
                          <Stat small label="Value" value={<Money v={p.summary.value} />} />
                          <Stat small label="Day P&L" value={<Money v={p.summary.dayPnl} signed />} sub={<Pct v={p.summary.dayPnlPct} />} />
                          <Stat small label="Total P&L" value={<Money v={p.summary.pnl} signed />} sub={<Pct v={p.summary.pnlPct} />} />
                          <Stat small label="Invested" value={<Money v={p.summary.invested} />} />
                        </div>
                      )
                    }
                  </Async>
                </Card>
                <Card title="Watchlist movers" right={<a className="small" href="#/watchlist">Open</a>} flush>
                  <Async q={wl} skeleton={<div className="card-b"><Skeleton h={80} /></div>}>
                    {(w) => {
                      const rows = w.rows.filter((r) => r.quote).sort((a, b) => Math.abs(b.quote!.changePct) - Math.abs(a.quote!.changePct)).slice(0, 6);
                      return rows.length === 0 ? (
                        <div className="empty">No quotes yet.</div>
                      ) : (
                        <table>
                          <tbody>
                            {rows.map((r) => (
                              <tr key={r.item.symbol} className="hover" onClick={() => go(`/symbol/${r.item.symbol}`)}>
                                <td><strong>{r.item.symbol}</strong></td>
                                <td className="num">{fmtNum(r.quote!.price)}</td>
                                <td className="num"><Pct v={r.quote!.changePct} /></td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      );
                    }}
                  </Async>
                </Card>
              </div>
            </div>
          </>
        )}
      </Async>

      <Card title="Headlines" right={<a className="small" href="#/market">More</a>}>
        <Async q={news} skeleton={<Skeleton h={80} />}>
          {(n) => (
            <div className="news">
              <ul>
                {n.items.slice(0, 6).map((it) => (
                  <li key={it.id}>
                    <a href={it.link} target="_blank" rel="noopener noreferrer">{it.title}</a>
                    <div className="small muted">{it.source} · {relTime(it.publishedAt)} {it.tone !== 'neutral' && <Badge kind={it.tone === 'positive' ? 'pos' : 'neg'}>{it.tone}</Badge>} {it.tags.map((t) => <span key={t} className="chip">{t}</span>)}</div>
                  </li>
                ))}
                {n.items.length === 0 && <li className="muted">No headlines available.</li>}
              </ul>
            </div>
          )}
        </Async>
      </Card>
      <div className="small faint">Observations are descriptive facts, not recommendations.</div>
    </div>
  );
}
