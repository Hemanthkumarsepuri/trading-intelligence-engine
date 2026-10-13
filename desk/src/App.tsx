import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { SessionState } from '../shared/types';
import { fmtIst, fmtIstTime } from '../shared/format';
import { UNAUTH_EVENT, request, useGet } from './api';
import { useLoc } from './nav';
import { useTheme } from './theme';
import { Icon, Spinner, useToast } from './ui';
import { Login } from './pages/Login';
import { Dashboard } from './pages/Dashboard';
import { Market } from './pages/Market';
import { Options } from './pages/Options';
import { Watchlist } from './pages/Watchlist';
import { SymbolPage } from './pages/SymbolPage';
import { Alerts } from './pages/Alerts';
import { Journal } from './pages/Journal';
import { Portfolio } from './pages/Portfolio';
import { Settings } from './pages/Settings';

const NAV = [
  { to: '/', label: 'Today', icon: 'home' },
  { to: '/market', label: 'Market', icon: 'market' },
  { to: '/options', label: 'Options', icon: 'options' },
  { to: '/watchlist', label: 'Watchlist', icon: 'watch' },
  { to: '/alerts', label: 'Alerts', icon: 'bell' },
  { to: '/journal', label: 'Journal', icon: 'journal' },
  { to: '/portfolio', label: 'Portfolio', icon: 'pie' },
  { to: '/settings', label: 'Settings', icon: 'gear' },
] as const;

function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 15_000);
    return () => clearInterval(t);
  }, []);
  return <span className="mono small muted">{fmtIstTime(now)} IST</span>;
}

function SessionPill() {
  const s = useGet<SessionState>('/api/session', { poll: 60_000 });
  const d = s.data?.data;
  if (!d) return <span className="badge">Market …</span>;
  const kind = d.phase === 'OPEN' || d.phase === 'SPECIAL' ? 'pos' : d.phase === 'PRE_OPEN' ? 'warn' : undefined;
  return (
    <span className="row" style={{ gap: 6 }}>
      <span className={`dot ${d.phase === 'OPEN' || d.phase === 'SPECIAL' ? 'on' : d.phase === 'PRE_OPEN' ? 'pre' : ''}`} />
      <span className={`badge ${kind ?? ''}`} title={d.reason}>{d.label}</span>
      {d.nextChangeLabel && <span className="small muted hide-m">{d.nextChangeLabel}{d.nextChangeAt ? ` ${fmtIst(d.nextChangeAt)}` : ''}</span>}
      {!d.calendarKnown && <span className="badge warn" title="Holiday list does not cover this year; verify market days on nseindia.com">Holiday list outdated</span>}
    </span>
  );
}

function Shell({ demo }: { demo: boolean }) {
  const loc = useLoc();
  const qc = useQueryClient();
  const toast = useToast();
  const [theme, toggleTheme] = useTheme();
  const active = (to: string) => (to === '/' ? loc.path === '/' : loc.path === to || loc.path.startsWith(`${to}/`));

  const logout = async () => {
    try {
      await request('POST', '/api/auth/logout', {});
    } catch (e) {
      // The browser is signed out regardless, but never claim the server revoked the session when it did not.
      toast(e instanceof Error ? e.message : 'Could not confirm sign-out on the server.', true);
    } finally {
      window.dispatchEvent(new Event(UNAUTH_EVENT));
    }
  };

  let page: JSX.Element;
  if (loc.path === '/') page = <Dashboard />;
  else if (loc.path === '/market') page = <Market />;
  else if (loc.path === '/options') page = <Options />;
  else if (loc.path === '/watchlist') page = <Watchlist />;
  else if (loc.path.startsWith('/symbol/')) page = <SymbolPage symbol={decodeURIComponent(loc.path.slice('/symbol/'.length))} />;
  else if (loc.path === '/alerts') page = <Alerts />;
  else if (loc.path === '/journal') page = <Journal />;
  else if (loc.path === '/portfolio') page = <Portfolio />;
  else if (loc.path === '/settings') page = <Settings />;
  else page = <div className="page"><div className="empty">Page not found. <a href="#/">Go to Today</a></div></div>;

  return (
    <div className="app">
      <aside className="side">
        <div className="brand">
          <svg viewBox="0 0 64 64" aria-hidden="true"><rect width="64" height="64" rx="14" fill="#0b0f17" /><path d="M12 44 24 30l9 8 19-22" fill="none" stroke="#2dd4bf" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" /></svg>
          TIRE Desk
        </div>
        <nav className="nav" aria-label="Main">
          {NAV.map((n) => (
            <a key={n.to} href={`#${n.to}`} className={active(n.to) ? 'active' : ''} aria-current={active(n.to) ? 'page' : undefined}>
              <Icon name={n.icon} />
              {n.label}
            </a>
          ))}
        </nav>
        <div className="side-foot">
          <div className="small faint">Read-only research tool. No orders are ever placed. Not investment advice.</div>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="grow row"><SessionPill /></div>
          <Clock />
          <button className="icon-btn" title="Refresh all data" aria-label="Refresh all data" onClick={() => { void qc.invalidateQueries(); toast('Refreshing…'); }}>
            <Icon name="refresh" />
          </button>
          <button className="icon-btn" title="Toggle theme" aria-label="Toggle theme" onClick={toggleTheme}>
            <Icon name={theme === 'dark' ? 'sun' : 'moon'} />
          </button>
          <button className="icon-btn" title="Sign out" aria-label="Sign out" onClick={() => void logout()}>
            <Icon name="logout" />
          </button>
        </header>
        {demo && (
          <div className="banner warn small" role="note" style={{ borderRadius: 0, justifyContent: 'center' }}>
            <strong>DEMO DATA.</strong> The server is in offline mode (DESK_OFFLINE=1): all market values are synthetic. Your journal, holdings and alerts are real.
          </div>
        )}
        <main>{page}</main>
      </div>
      <nav className="tabbar" aria-label="Main">
        {NAV.map((n) => (
          <a key={n.to} href={`#${n.to}`} className={active(n.to) ? 'active' : ''} aria-current={active(n.to) ? 'page' : undefined}>
            <Icon name={n.icon} />
            <span>{n.label}</span>
          </a>
        ))}
      </nav>
    </div>
  );
}

export function App() {
  const qc = useQueryClient();
  const me = useGet<{ authenticated: boolean; configured: boolean; demo: boolean }>('/api/auth/me');

  useEffect(() => {
    const on = () => {
      qc.removeQueries({ predicate: (q) => q.queryKey[1] !== '/api/auth/me' });
      qc.setQueryData(['api', '/api/auth/me'], (old: unknown) => {
        const o = old as { data: { authenticated: boolean; configured: boolean }; meta: unknown } | undefined;
        return o ? { ...o, data: { ...o.data, authenticated: false } } : o;
      });
    };
    window.addEventListener(UNAUTH_EVENT, on);
    return () => window.removeEventListener(UNAUTH_EVENT, on);
  }, [qc]);

  if (me.isPending) return <div className="login"><Spinner /></div>;
  if (me.isError) {
    return (
      <div className="login">
        <div className="banner neg" role="alert">
          <div style={{ flex: 1 }}>
            <strong>Cannot check your sign-in right now.</strong>
            <div className="small">{me.error.message}</div>
          </div>
          <button className="btn sm" onClick={() => void me.refetch()} disabled={me.isFetching}>Retry</button>
        </div>
      </div>
    );
  }
  if (!me.data.data.authenticated) return <Login configured={me.data.data.configured} />;
  return <Shell demo={me.data.data.demo} />;
}
