import { createContext, useCallback, useContext, useEffect, useId, useMemo, useState, type ReactNode } from 'react';
import type { UseQueryResult } from '@tanstack/react-query';
import { fmtNum, fmtPct, fmtSigned, isNum, relTime, tone } from '../shared/format';
import type { Meta } from '../shared/types';
import { ApiError, type Resp } from './api';

// ---- Icons (24px grid, stroke) -----------------------------------------------------

const P: Record<string, string> = {
  home: 'M3 11.5 12 4l9 7.5M5 10v10h5v-6h4v6h5V10',
  market: 'M3 17l5-6 4 3 8-9M16 5h4v4',
  options: 'M4 20V10m6 10V4m6 16v-7m4 7H2',
  watch: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zm10 3a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  bell: 'M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9m4.3 12a2 2 0 0 0 3.4 0',
  journal: 'M5 3h12a2 2 0 0 1 2 2v16l-4-3H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zm3 5h8m-8 4h5',
  pie: 'M12 3v9h9a9 9 0 1 1-9-9zm3 0a9 9 0 0 1 6 6h-6z',
  gear: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm7.4-3a7.4 7.4 0 0 0-.1-1.3l2-1.5-2-3.5-2.3.9a7.5 7.5 0 0 0-2.2-1.3L14.5 3h-4l-.4 2.3a7.5 7.5 0 0 0-2.2 1.3l-2.3-.9-2 3.5 2 1.5a7.400 7.400 0 0 0 0 2.600l-2 1.500 2 3.500 2.300-.900a7.500 7.500 0 0 0 2.200 1.300l.4 2.300h4l.4-2.300a7.500 7.500 0 0 0 2.200-1.300l2.300.9 2-3.500-2-1.500c.1-.4.1-.9.1-1.300z',
  sun: 'M12 4V2m0 20v-2m8-8h2M2 12h2m13.700-5.700 1.400-1.400M4.900 19.100l1.400-1.400m0-11.400L4.900 4.900m14.200 14.200-1.400-1.400M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10z',
  moon: 'M20 14.500A8 8 0 0 1 9.500 4 8 8 0 1 0 20 14.500z',
  plus: 'M12 5v14M5 12h14',
  trash: 'M4 7h16M10 11v6m4-6v6M6 7l1 13h10l1-13M9 7V4h6v3',
  edit: 'M4 20h4L19 9l-4-4L4 16v4zM14 6l4 4',
  refresh: 'M20 11a8 8 0 0 0-14.600-3M4 4v4h4M4 13a8 8 0 0 0 14.600 3M20 20v-4h-4',
  logout: 'M15 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4M10 16l-4-4 4-4M6 12h10',
  x: 'M6 6l12 12M18 6 6 18',
  info: 'M12 8h.01M11 12h1v5h1m-1 5a10 10 0 1 1 0-20 10 10 0 0 1 0 20z',
};

export function Icon({ name }: { name: keyof typeof P | string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={P[name] ?? ''} />
    </svg>
  );
}

// ---- Toasts ---------------------------------------------------------------------------

interface ToastMsg {
  id: number;
  text: string;
  err: boolean;
}
const ToastCtx = createContext<(text: string, err?: boolean) => void>(() => undefined);
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastMsg[]>([]);
  const push = useCallback((text: string, err = false) => {
    const id = Date.now() + Math.random();
    setItems((cur) => [...cur.slice(-3), { id, text, err }]);
    setTimeout(() => setItems((cur) => cur.filter((t) => t.id !== id)), err ? 7000 : 3500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast${t.err ? ' err' : ''}`}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : 'Something went wrong';
}

// ---- Basic pieces --------------------------------------------------------------------

export function Card({ title, right, children, flush, className = '' }: { title?: ReactNode; right?: ReactNode; children: ReactNode; flush?: boolean; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || right) && (
        <header className="card-h">
          <h2>{title}</h2>
          <div className="row">{right}</div>
        </header>
      )}
      <div className={`card-b${flush ? ' flush' : ''}`}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, sub, valueClass, small }: { label: ReactNode; value: ReactNode; sub?: ReactNode; valueClass?: string; small?: boolean }) {
  return (
    <div className={`stat${small ? ' sm' : ''}`}>
      <div className="label">{label}</div>
      <div className={`value ${valueClass ?? ''}`}>{value}</div>
      {sub !== undefined && sub !== null && <div className="sub">{sub}</div>}
    </div>
  );
}

export function Pct({ v, digits = 2 }: { v: number | null | undefined; digits?: number }) {
  return <span className={tone(v)}>{fmtPct(v, digits)}</span>;
}

export function Signed({ v, digits = 2, prefix = '' }: { v: number | null | undefined; digits?: number; prefix?: string }) {
  if (!isNum(v)) return <span className="muted">-</span>;
  return <span className={tone(v)}>{prefix}{fmtSigned(v, digits)}</span>;
}

export function Money({ v, digits = 0, signed }: { v: number | null | undefined; digits?: number; signed?: boolean }) {
  if (!isNum(v)) return <span className="muted">-</span>;
  const abs = `₹${fmtNum(Math.abs(v), digits)}`;
  const text = v < 0 ? `-${abs}` : signed && v > 0 ? `+${abs}` : abs;
  return <span className={signed ? tone(v) : ''}>{text}</span>;
}

export function Badge({ kind, children, title }: { kind?: 'pos' | 'neg' | 'warn' | 'info'; children: ReactNode; title?: string }) {
  return (
    <span className={`badge ${kind ?? ''}`} title={title}>
      {children}
    </span>
  );
}

export function Spinner() {
  return <span className="spinner" role="progressbar" aria-label="Loading" />;
}

export function Skeleton({ h = 16, w = '100%' }: { h?: number; w?: number | string }) {
  return <div className="skeleton" style={{ height: h, width: w }} />;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function ErrorBox({ error, retry }: { error: unknown; retry?: () => void }) {
  return (
    <div className="banner neg" role="alert">
      <div style={{ flex: 1 }}>
        <strong>Could not load this.</strong>
        <div className="small">{errMsg(error)}</div>
      </div>
      {retry && (
        <button className="btn sm" onClick={retry}>
          Retry
        </button>
      )}
    </div>
  );
}

/** Data-quality strip: source, staleness, and every note the server attached. Never hidden. */
export function DataNotes({ meta, fetchedAt }: { meta: Meta | undefined; fetchedAt?: number }) {
  if (!meta) return null;
  const notes = meta.notes.filter(Boolean);
  // Demo mode is announced once, app-wide (see Shell), so it is not repeated per card.
  const rest = notes.filter((n) => !n.startsWith('DEMO DATA'));
  return (
    <div className="stack" style={{ gap: 6 }}>
      {meta.stale && (
        <div className="banner warn small" role="note">
          <strong>Showing last good data.</strong> The live source failed; values may be out of date.
        </div>
      )}
      {rest.length > 0 && (
        <details className="notes">
          <summary>
            <Icon name="info" />
            {rest.length} data note{rest.length > 1 ? 's' : ''} · source: {meta.source}
            {fetchedAt ? ` · updated ${relTime(new Date(fetchedAt).toISOString())}` : ''}
          </summary>
          <ul>
            {rest.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

/** Standard loading/error/data wrapper for a query. */
export function Async<T>({ q, children, skeleton }: { q: UseQueryResult<Resp<T>, ApiError>; children: (data: T, meta: Meta) => ReactNode; skeleton?: ReactNode }) {
  if (q.isPending) return <>{skeleton ?? <Card><Skeleton h={90} /></Card>}</>;
  if (q.isError) return <ErrorBox error={q.error} retry={() => void q.refetch()} />;
  return (
    <>
      <DataNotes meta={q.data.meta} fetchedAt={q.dataUpdatedAt} />
      {children(q.data.data, q.data.meta)}
    </>
  );
}

export function PageHead({ title, sub, right }: { title: string; sub?: ReactNode; right?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {sub && <div className="muted small" style={{ marginTop: 2 }}>{sub}</div>}
      </div>
      <div className="row">{right}</div>
    </div>
  );
}

// ---- Forms ---------------------------------------------------------------------------------

export function Field({ label, hint, error, children }: { label: string; hint?: string; error?: string | null; children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <label className="field" htmlFor={id}>
      <span>{label}</span>
      {children(id)}
      {hint && !error && <span className="hint">{hint}</span>}
      {error && <span className="err" role="alert">{error}</span>}
    </label>
  );
}

export function NumInput({ id, value, onChange, step = 'any', min, placeholder }: { id?: string; value: number | null; onChange: (v: number | null) => void; step?: string; min?: number; placeholder?: string }) {
  const [text, setText] = useState(value === null ? '' : String(value));
  useEffect(() => {
    setText((cur) => (Number(cur) === value || (cur === '' && value === null) ? cur : value === null ? '' : String(value)));
  }, [value]);
  return (
    <input
      id={id}
      inputMode="decimal"
      type="number"
      step={step}
      min={min}
      value={text}
      placeholder={placeholder}
      onChange={(e) => {
        setText(e.target.value);
        const n = e.target.value === '' ? null : Number(e.target.value);
        onChange(n !== null && Number.isFinite(n) ? n : null);
      }}
    />
  );
}

export function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; label?: string }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" className={o.value === value ? 'on' : ''} aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', k);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', k);
      document.body.style.overflow = prev;
    };
  }, [onClose]);
  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title}>
        <header className="card-h">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <Icon name="x" />
          </button>
        </header>
        <div className="card-b">{children}</div>
      </div>
    </div>
  );
}

export function useConfirm() {
  return useMemo(() => (msg: string) => window.confirm(msg), []);
}
