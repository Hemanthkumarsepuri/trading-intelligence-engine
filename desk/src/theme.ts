import { useCallback, useState } from 'react';

export type ThemePref = 'dark' | 'light';

function current(): ThemePref {
  const t = document.documentElement.dataset.theme;
  if (t === 'light' || t === 'dark') return t;
  return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

export function useTheme(): [ThemePref, () => void] {
  const [t, setT] = useState<ThemePref>(current);
  const toggle = useCallback(() => {
    const next: ThemePref = t === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem('desk-theme', next);
    } catch {
      /* private mode: theme just won't persist */
    }
    setT(next);
  }, [t]);
  return [t, toggle];
}
