// Node HTTP <-> Fetch adapter shared by the production server (server/main.ts)
// and the local dev server (server/dev.ts).
//
// Trust boundary. In production every request must come through the Netlify
// edge proxy, which adds `x-desk-proxy-key` (a shared secret) and
// `x-desk-client-ip` (the visitor's IP as Netlify saw it). Requests without a
// valid key are refused before routing, except the public health check. Any
// client-supplied copies of trusted headers are stripped first, so a caller
// cannot spoof its IP to dodge the login lockout.

import { timingSafeEqual } from 'node:crypto';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { Readable } from 'node:stream';
import { CLIENT_IP_HEADER } from './auth';
import type { Ctx } from './config';
import { handle } from './router';

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

      const headers = new Headers();
      for (const [k, v] of Object.entries(nodeReq.headers)) {
        if (v === undefined) continue;
        const key = k.toLowerCase();
        if (key === PROXY_KEY_HEADER || key === CLIENT_IP_HEADER || key === 'host') continue;
        headers.set(key, Array.isArray(v) ? v.join(', ') : v);
      }
      const forwardedIp = nodeReq.headers[CLIENT_IP_HEADER];
      const ip = viaProxy && typeof forwardedIp === 'string' && forwardedIp ? forwardedIp : nodeReq.socket.remoteAddress ?? 'unknown';
      headers.set(CLIENT_IP_HEADER, ip);

      // The browser-facing origin (the Netlify site in production) is what cookies
      // and the CSRF origin check are about, not the Railway hostname.
      const base = ctx.cfg.publicOrigin ?? `http://${nodeReq.headers.host ?? 'localhost'}`;
      const url = new URL(rawUrl, base);

      const method = (nodeReq.method ?? 'GET').toUpperCase();
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
