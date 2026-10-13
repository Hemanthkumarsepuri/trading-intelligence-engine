import { useMutation, useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query';
import type { ApiResult, Meta } from '../shared/types';

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

export interface Resp<T> {
  data: T;
  meta: Meta;
}

export const UNAUTH_EVENT = 'desk:unauthenticated';

export async function request<T>(method: string, path: string, body?: unknown): Promise<Resp<T>> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      credentials: 'same-origin',
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, 'NETWORK', 'Cannot reach the server. Check your connection.');
  }
  let json: ApiResult<T> | null = null;
  try {
    json = (await res.json()) as ApiResult<T>;
  } catch {
    /* non-JSON error page */
  }
  if (res.status === 401 && path !== '/api/auth/login' && path !== '/api/auth/me') window.dispatchEvent(new Event(UNAUTH_EVENT));
  if (!json) throw new ApiError(res.status, 'BAD_RESPONSE', `Unexpected response from server (HTTP ${res.status}).`);
  if (!json.ok) throw new ApiError(res.status, json.error.code, json.error.message);
  return { data: json.data, meta: json.meta };
}

export function useGet<T>(path: string | null, opts: { poll?: number | false; enabled?: boolean } = {}) {
  return useQuery<Resp<T>, ApiError>({
    queryKey: ['api', path],
    queryFn: () => request<T>('GET', path!),
    enabled: path !== null && opts.enabled !== false,
    refetchInterval: opts.poll || false,
    refetchIntervalInBackground: false,
    retry: (n, e) => n < 1 && !(e instanceof ApiError && e.status >= 400 && e.status < 500),
  });
}

/** Mutation that refreshes any cached GET whose path starts with one of `invalidate`. */
export function useSend<TIn, TOut = unknown>(method: 'POST' | 'PUT' | 'DELETE', path: string | ((v: TIn) => string), invalidate: string[] = []) {
  const qc = useQueryClient();
  return useMutation<Resp<TOut>, ApiError, TIn>({
    mutationFn: (v) => request<TOut>(method, typeof path === 'function' ? path(v) : path, method === 'DELETE' ? undefined : v),
    onSuccess: async () => {
      await qc.invalidateQueries({
        predicate: (q: { queryKey: QueryKey }) => {
          const k = q.queryKey[1];
          return typeof k === 'string' && invalidate.some((p) => k.startsWith(p));
        },
      });
    },
  });
}

/** Poll faster while the market is open. */
export function usePollMs(): number {
  const s = useGet<{ phase: string }>('/api/session', { poll: 60_000 });
  const phase = s.data?.data.phase;
  return phase === 'OPEN' || phase === 'SPECIAL' ? 20_000 : phase === 'PRE_OPEN' ? 60_000 : 5 * 60_000;
}
