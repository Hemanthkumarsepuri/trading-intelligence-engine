// Node HTTP <-> Fetch adapter shared by the production server (server/main.ts)
// and the local dev server (server/dev.ts).
//
// Trust boundary. Two modes:
//  - Single origin (DESK_INGRESS=railway, the default design): the browser talks to
//    Railway directly, there is no proxy and therefore no proxy credential. The
//    caller's IP is the single header the Railway edge sets (x-real-ip); it was
//    verified live that X-Forwarded-For is NOT overwritten by Railway and is
//    therefore never used. Client-supplied x-desk-* headers are never trusted.
//  - Legacy edge proxy (DESK_PROXY_SECRET): every request must come through the
//    Netlify edge proxy, which adds `x-desk-proxy-key` and `x-desk-client-ip`.
//    Requests without a valid key are refused before routing, except the health check.
// In both modes client-supplied copies of trusted headers are stripped first.

import { timingSafeEqual } from 'node:crypto';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { Readable } from 'node:stream';
import { CLIENT_IP_HEADER, singleHeaderIp } from './auth';
import type { Ctx } from './config';
import { handle } from './router';
import { serveStatic } from './static';

export const PROXY_KEY_HEADER = 'x-desk-proxy-key';
const MAX_BODY = 6 * 1024 * 1024;
const PUBLIC_PATHS = new Set(['/api/health']);

function safeEqual(a: string, b: string): boolean {
  const x = Buffer.from(a);
  const y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}

function send(res: ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' });
  res.end(JSON.stringify(body));
}

export interface AdapterOptions {
  /** Require the Netlify proxy key on every non-public route. */
  requireProxyKey: boolean;
  /** Name of the header the platform edge sets to the caller IP (Railway: x-real-ip). Only trusted behind that edge. */
  platformIpHeader?: string | null;
  /** Serve the built frontend from this directory for non-/api GET/HEAD requests. */
  webRoot?: string | null;
}

export function createHandler(getCtx: () => Promise<Ctx>, opts: AdapterOptions) {
  return async (nodeReq: IncomingMessage, nodeRes: ServerResponse): Promise<void> => {
    try {
      const ctx = await getCtx();
      const rawUrl = nodeReq.url ?? '/';
      const path = rawUrl.split('?')[0] ?? '/';

      // ---- trust boundary ----------------------------------------------------------
      const presented = nodeReq.headers[PROXY_KEY_HEADER];
      const viaProxy = typeof presented === 'string' && ctx.cfg.proxySecret !== null && safeEqual(presented, ctx.cfg.proxySecret);
      if (opts.requireProxyKey && !viaProxy && !PUBLIC_PATHS.has(path)) {
        send(nodeRes, 403, { ok: false, error: { code: 'DIRECT_ACCESS', message: 'This API is only reachable through the TIRE Desk site.' } });
        return;
      }

      const method = (nodeReq.method ?? 'GET').toUpperCase();
      if (opts.webRoot && !path.startsWith('/api/')) {
        if (method !== 'GET' && method !== 'HEAD') {
          send(nodeRes, 405, { ok: false, error: { code: 'METHOD_NOT_ALLOWED', message: 'Method not allowed.' } });
          return;
        }
        const f = await serveStatic(opts.webRoot, path);
        nodeRes.writeHead(f.status, { ...f.headers, 'content-length': String(f.body.length) });
        nodeRes.end(method === 'HEAD' ? undefined : f.body);
        return;
      }

      const headers = new Headers();
      for (const [k, v] of Object.entries(nodeReq.headers)) {
        if (v === undefined) continue;
        const key = k.toLowerCase();
        if (key === PROXY_KEY_HEADER || key === CLIENT_IP_HEADER || key === 'host') continue;
        headers.set(key, Array.isArray(v) ? v.join(', ') : v);
      }
      const forwardedIp = nodeReq.headers[CLIENT_IP_HEADER];
      const platformIp = opts.platformIpHeader ? singleHeaderIp(nodeReq.headers[opts.platformIpHeader]) : null;
      const ip = viaProxy && typeof forwardedIp === 'string' && forwardedIp ? forwardedIp : platformIp ?? nodeReq.socket.remoteAddress ?? 'unknown';
      headers.set(CLIENT_IP_HEADER, ip);

      // The browser-facing origin (the Netlify site in production) is what cookies
      // and the CSRF origin check are about, not the Railway hostname.
      const base = ctx.cfg.publicOrigin ?? `http://${nodeReq.headers.host ?? 'localhost'}`;
      const url = new URL(rawUrl, base);

      let body: Buffer | undefined;
      if (method !== 'GET' && method !== 'HEAD') {
        const chunks: Buffer[] = [];
        let size = 0;
        for await (const c of nodeReq) {
          size += (c as Buffer).length;
          if (size > MAX_BODY) {
            send(nodeRes, 413, { ok: false, error: { code: 'TOO_LARGE', message: 'Request body too large.' } });
            return;
          }
          chunks.push(c as Buffer);
        }
        body = Buffer.concat(chunks);
      }
      const req = new Request(url, { method, headers, body: body && body.length > 0 ? new Uint8Array(body) : undefined });
      const res = await handle(req, ctx);

      const out: Record<string, string | string[]> = {};
      res.headers.forEach((v, k) => {
        if (k !== 'set-cookie') out[k] = v;
      });
      const cookies = res.headers.getSetCookie();
      if (cookies.length) out['set-cookie'] = cookies;
      nodeRes.writeHead(res.status, out);
      if (res.body) Readable.fromWeb(res.body as never).pipe(nodeRes);
      else nodeRes.end();
    } catch (e) {
      console.error('[http] unhandled', e instanceof Error ? e.message : e);
      if (!nodeRes.headersSent) send(nodeRes, 500, { ok: false, error: { code: 'INTERNAL', message: 'Something went wrong on the server.' } });
      else nodeRes.end();
    }
  };
}
