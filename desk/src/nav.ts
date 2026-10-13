import { useEffect, useState } from 'react';

/** Tiny hash router: '#/journal?fy=2025-26' -> { path: '/journal', query } */
export interface Loc {
  path: string;
  query: URLSearchParams;
}

function read(): Loc {
  const raw = window.location.hash.replace(/^#/, '') || '/';
  const [path, qs] = raw.split('?');
  return { path: path || '/', query: new URLSearchParams(qs ?? '') };
}

export function useLoc(): Loc {
  const [loc, setLoc] = useState<Loc>(read);
  useEffect(() => {
    const on = () => setLoc(read());
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  return loc;
}

export function go(path: string): void {
  window.location.hash = path;
}

export function setQuery(key: string, value: string | null): void {
  const { path, query } = read();
  if (value === null) query.delete(key);
  else query.set(key, value);
  const qs = query.toString();
  window.location.hash = qs ? `${path}?${qs}` : path;
}
