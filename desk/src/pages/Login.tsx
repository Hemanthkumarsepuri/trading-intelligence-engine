import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { request } from '../api';
import { errMsg, Field, Spinner } from '../ui';

export function Login({ configured }: { configured: boolean }) {
  const qc = useQueryClient();
  const [pw, setPw] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await request('POST', '/api/auth/login', { password: pw });
      setPw('');
      await qc.refetchQueries({ queryKey: ['api', '/api/auth/me'] });
    } catch (e2) {
      setErr(errMsg(e2));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <form className="card" onSubmit={(e) => void submit(e)}>
        <div className="card-b stack" style={{ gap: 16 }}>
          <div className="row" style={{ gap: 10 }}>
            <svg viewBox="0 0 64 64" width="32" height="32" aria-hidden="true"><rect width="64" height="64" rx="14" fill="#0b0f17" /><path d="M12 44 24 30l9 8 19-22" fill="none" stroke="#2dd4bf" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" /></svg>
            <div>
              <h1>TIRE Desk</h1>
              <div className="muted small">Private market desk</div>
            </div>
          </div>
          {!configured && (
            <div className="banner warn small" role="alert">
              The API service is not fully configured yet (session secret or password hash missing on Railway). See RAILWAY_INTEGRATION.md.
            </div>
          )}
          <Field label="Password" error={err}>
            {(id) => <input id={id} type="password" autoComplete="current-password" autoFocus value={pw} onChange={(e) => setPw(e.target.value)} required />}
          </Field>
          <button className="btn primary" disabled={busy || pw.length === 0}>
            {busy ? <Spinner /> : 'Sign in'}
          </button>
          <div className="faint small">Single-owner app. Repeated wrong passwords lock sign-in for 15 minutes.</div>
        </div>
      </form>
    </div>
  );
}
