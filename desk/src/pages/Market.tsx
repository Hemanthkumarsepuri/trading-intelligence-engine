import { useState } from 'react';
import type { Flows, Overview, SectorRow } from '../../server/services/market';
import type { NewsItem } from '../../server/providers/news';
import type { IpoRow } from '../../server/providers/upstox';
import { fmtNum, fmtSigned, relTime } from '../../shared/format';
import { go } from '../nav';
import { Async, Badge, Card, Empty, PageHead, Pct, Segmented, Signed, Skeleton, Stat } from '../ui';
import { useGet, usePollMs } from '../api';

type Tab = 'sectors' | 'flows' | 'global' | 'ban' | 'ipo' | 'news';

function Sectors() {
  const poll = usePollMs();
  const q = useGet<{ sectors: SectorRow[]; niftyChangePct: number | null }>('/api/sectors', { poll });
  return (
    <Async q={q} skeleton={<Card><Skeleton h={200} /></Card>}>
      {(d) => (
        <Card title="Sector performance" right={d.niftyChangePct !== null ? <span className="small muted">Nifty 50 <Pct v={d.niftyChangePct} /></span> : null}>
          {d.sectors.length === 0 ? (
            <Empty>No sector data available right now.</Empty>
          ) : (
            <div className="heat">
              {d.sectors.map((s) => (
                <div key={s.id} className={s.quote.changePct > 0.05 ? 'pos-bg' : s.quote.changePct < -0.05 ? 'neg-bg' : 'flat-bg'}>
                  <div className="small muted">{s.name}</div>
                  <strong><Pct v={s.quote.changePct} /></strong>
                  <div className="small muted">{s.relative !== null ? `${fmtSigned(s.relative, 2)} vs Nifty` : ''}</div>
                </div>
              ))}
            </div>
          )}
          <p className="small faint" style={{ marginTop: 10 }}>Relative strength = sector change minus Nifty 50 change, in percentage points.</p>
        </Card>
      )}
    </Async>
  );
}

function FlowsTab() {
  const q = useGet<Flows>('/api/flows', { poll: 10 * 60_000 });
  return (
    <Async q={q} skeleton={<Card><Skeleton h={160} /></Card>}>
      {(d) => (
        <div className="grid g2">
          <Card title="FII / DII cash flows" right={d.cash ? <span className="small muted">{d.cash.date}</span> : null}>
            {d.cash ? (
              <div className="stack">
                <div className="grid g2 keep">
                  <Stat label="FII net (₹ Cr)" value={<Signed v={d.cash.fii?.net ?? null} digits={0} />} sub={d.cash.fiiLabel} />
                  <Stat label="DII net (₹ Cr)" value={<Signed v={d.cash.dii?.net ?? null} digits={0} />} sub={d.cash.diiLabel} />
                </div>
                <table>
                  <thead><tr><th /><th className="num">Buy</th><th className="num">Sell</th><th className="num">Net</th></tr></thead>
                  <tbody>
                    {(['fii', 'dii'] as const).map((k) => d.cash![k] && (
                      <tr key={k}><td>{k.toUpperCase()}</td><td className="num">{fmtNum(d.cash![k]!.buy, 0)}</td><td className="num">{fmtNum(d.cash![k]!.sell, 0)}</td><td className="num"><Signed v={d.cash![k]!.net} digits={0} /></td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty>FII/DII data is not available from this host right now.</Empty>
            )}
          </Card>
          <Card title="FII derivatives positioning" right={d.positioning ? <span className="small muted">{d.positioning.date}</span> : null}>
            {d.positioning ? (
              <div className="stack">
                <div className="row between">
                  <Stat label="Index futures long share" value={d.positioning.indexFutLongPct !== null ? `${d.positioning.indexFutLongPct.toFixed(0)}%` : '-'} />
                  <Badge kind={d.positioning.read.tone === 'alert' ? 'neg' : d.positioning.read.tone === 'warn' ? 'warn' : 'info'}>{d.positioning.read.label}</Badge>
                </div>
                <p className="small muted">{d.positioning.read.detail}</p>
                <dl className="kv">
                  <dt>Index futures net (contracts)</dt><dd><Signed v={d.positioning.indexFutNet} digits={0} /></dd>
                  <dt>Index calls net</dt><dd><Signed v={d.positioning.indexCallNet} digits={0} /></dd>
                  <dt>Index puts net</dt><dd><Signed v={d.positioning.indexPutNet} digits={0} /></dd>
                  <dt>Stock futures net</dt><dd><Signed v={d.positioning.stockFutNet} digits={0} /></dd>
                  <dt>Client index futures net</dt><dd><Signed v={d.positioning.client.indexFutNet} digits={0} /></dd>
                  <dt>Pro index futures net</dt><dd><Signed v={d.positioning.pro.indexFutNet} digits={0} /></dd>
                </dl>
              </div>
            ) : (
              <Empty>Participant-wise open interest is not available right now.</Empty>
            )}
          </Card>
        </div>
      )}
    </Async>
  );
}

function GlobalTab() {
  const q = useGet<Overview>('/api/overview', { poll: 60_000 });
  const groups = ['US', 'ASIA', 'EUROPE', 'COMMODITY', 'FX', 'RATES'];
  return (
    <Async q={q} skeleton={<Card><Skeleton h={200} /></Card>}>
      {(d) => (
        <div className="grid g2">
          {groups.map((g) => {
            const items = d.globals.filter((x) => x.group === g);
            if (items.length === 0) return null;
            return (
              <Card key={g} title={g === 'US' ? 'US markets' : g === 'ASIA' ? 'Asia' : g === 'EUROPE' ? 'Europe' : g === 'COMMODITY' ? 'Commodities' : g === 'FX' ? 'Currency' : 'Rates'} flush>
                <table>
                  <tbody>
                    {items.map((i) => (
                      <tr key={i.id} title={i.why}>
                        <td><strong>{i.name}</strong><div className="small muted wrap" style={{ whiteSpace: 'normal', maxWidth: 260 }}>{i.why}</div></td>
                        <td className="num">{i.quote ? fmtNum(i.quote.price) : '-'}</td>
                        <td className="num">{i.quote ? <Pct v={i.quote.changePct} /> : <span className="muted">n/a</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>
            );
          })}
        </div>
      )}
    </Async>
  );
}

function BanTab() {
  const q = useGet<{ symbols: string[]; asOf: string | null }>('/api/ban', { poll: 30 * 60_000 });
  return (
    <Async q={q}>
      {(d) => (
        <Card title="F&O ban list" right={<span className="small muted">{d.asOf ? `trade date ${d.asOf}` : 'trade date unknown'}</span>}>
          {d.symbols.length === 0 ? (
            <Empty>No securities are in the F&O ban period today.</Empty>
          ) : (
            <div className="row">
              {d.symbols.map((s) => <button key={s} className="btn sm" onClick={() => go(`/symbol/${s}`)}>{s}</button>)}
            </div>
          )}
          <p className="small faint" style={{ marginTop: 10 }}>Stocks past 95% of the market-wide position limit: fresh positions are not allowed, only reducing existing ones.</p>
        </Card>
      )}
    </Async>
  );
}

function IpoTab() {
  const q = useGet<{ ipos: IpoRow[] }>('/api/ipos', { poll: 30 * 60_000 });
  return (
    <Async q={q}>
      {(d) => (
        <Card title="IPOs: open and upcoming" flush>
          {d.ipos.length === 0 ? (
            <Empty>No open or upcoming IPOs found (or the Upstox token is not configured).</Empty>
          ) : (
            <div className="tbl-wrap">
              <table>
                <thead><tr><th>Company</th><th>Type</th><th>Status</th><th className="num">Size (₹ Cr)</th><th>Price band</th><th>Opens</th><th>Closes</th></tr></thead>
                <tbody>
                  {d.ipos.map((i) => (
                    <tr key={i.id}>
                      <td><strong>{i.name}</strong></td><td>{i.type}</td><td><Badge kind={i.status.toLowerCase().includes('open') ? 'pos' : 'info'}>{i.status}</Badge></td>
                      <td className="num">{i.issueSizeCr !== null ? fmtNum(i.issueSizeCr, 0) : '-'}</td><td>{i.priceBand ?? '-'}</td><td>{i.opens ?? '-'}</td><td>{i.closes ?? '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}
    </Async>
  );
}

function NewsTab() {
  const q = useGet<{ items: NewsItem[] }>('/api/news', { poll: 5 * 60_000 });
  return (
    <Async q={q}>
      {(d) => (
        <Card title="Market headlines" right={<span className="small muted">Tone and tags are keyword rules, not analysis</span>}>
          <div className="news">
            <ul>
              {d.items.map((it) => (
                <li key={it.id}>
                  <a href={it.link} target="_blank" rel="noopener noreferrer">{it.title}</a>
                  <div className="small muted">{it.source} · {relTime(it.publishedAt)} {it.tone !== 'neutral' && <Badge kind={it.tone === 'positive' ? 'pos' : 'neg'}>{it.tone}</Badge>} {it.tags.map((t) => <span key={t} className="chip">{t}</span>)}</div>
                </li>
              ))}
              {d.items.length === 0 && <li className="muted">No headlines available.</li>}
            </ul>
          </div>
        </Card>
      )}
    </Async>
  );
}

export function Market() {
  const [tab, setTab] = useState<Tab>('sectors');
  return (
    <div className="page">
      <PageHead
        title="Market"
        sub="Sectors, institutional flows, global cues, F&O ban list, IPOs and headlines."
        right={<Segmented<Tab> label="Section" value={tab} onChange={setTab} options={[{ value: 'sectors', label: 'Sectors' }, { value: 'flows', label: 'Flows' }, { value: 'global', label: 'Global' }, { value: 'ban', label: 'F&O ban' }, { value: 'ipo', label: 'IPOs' }, { value: 'news', label: 'News' }]} />}
      />
      {tab === 'sectors' && <Sectors />}
      {tab === 'flows' && <FlowsTab />}
      {tab === 'global' && <GlobalTab />}
      {tab === 'ban' && <BanTab />}
      {tab === 'ipo' && <IpoTab />}
      {tab === 'news' && <NewsTab />}
    </div>
  );
}
