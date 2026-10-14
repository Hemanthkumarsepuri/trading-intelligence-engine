import { Card, Icon, PageHead } from '../ui';

const ITEMS = [
  { to: '/explorer', label: 'Contract Explorer', icon: 'chain', text: 'Every strike for an underlying and expiry, with the quality of each quote.' },
  { to: '/events', label: 'News and Events', icon: 'news', text: 'Exchange filings, results dates and public headlines with publisher and original time.' },
  { to: '/data-health', label: 'Data Health', icon: 'pulse', text: 'Provider status, freshness limits, entitlements and workbench alerts.' },
  { to: '/options', label: 'Index option chain', icon: 'options', text: 'Open-interest structure and implied move for NIFTY, BANKNIFTY and other index options.' },
  { to: '/portfolio', label: 'Portfolio', icon: 'pie', text: 'Holdings, P&L and tax view.' },
  { to: '/settings', label: 'Settings', icon: 'gear', text: 'Capital, risk limits, charges, backups and status.' },
] as const;

export function More() {
  return (
    <div className="page">
      <PageHead title="More" sub="Everything that does not fit in the bottom bar." />
      <div className="grid g2">
        {ITEMS.map((i) => (
          <Card key={i.to}>
            <a href={`#${i.to}`} className="row" style={{ gap: 12, textDecoration: 'none', color: 'inherit' }}>
              <Icon name={i.icon} />
              <span><strong>{i.label}</strong><span className="small muted" style={{ display: 'block' }}>{i.text}</span></span>
            </a>
          </Card>
        ))}
      </div>
    </div>
  );
}
