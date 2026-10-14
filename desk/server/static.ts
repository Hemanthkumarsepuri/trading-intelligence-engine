// Static hosting of the built frontend for single-origin mode (DESK_INGRESS=railway).
//
// The app and its API share one origin, so the session cookie stays first-party and
// no credential has to be handed to a third-party proxy. The header set below mirrors
// the `/*` block of netlify.toml (a test keeps the two in step).

import { promises as fs } from 'node:fs';
import path from 'node:path';

export const SECURITY_HEADERS: Record<string, string> = {
  'Strict-Transport-Security': 'max-age=63072000; includeSubDomains',
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
  'Referrer-Policy': 'no-referrer',
  'Cross-Origin-Opener-Policy': 'same-origin',
  'Cross-Origin-Resource-Policy': 'same-origin',
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()',
  'X-Robots-Tag': 'noindex, nofollow, noarchive',
  'Content-Security-Policy':
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; manifest-src 'self'; worker-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
};

const TYPES: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.webmanifest': 'application/manifest+json',
  '.txt': 'text/plain; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
};

export interface StaticFile {
  status: number;
  headers: Record<string, string>;
  body: Buffer;
}

function notFound(): StaticFile {
  return { status: 404, headers: { ...SECURITY_HEADERS, 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store' }, body: Buffer.from('Not found') };
}

/** Resolve a request path to a file inside `root`. Never escapes `root`; `/` and extension-less paths map to index.html. */
export async function serveStatic(root: string, urlPath: string): Promise<StaticFile> {
  let rel: string;
  try {
    rel = decodeURIComponent(urlPath);
  } catch {
    return notFound();
  }
  if (rel.includes('\0') || rel.includes(String.fromCharCode(92))) return notFound();
  if (rel === '/' || rel === '') rel = '/index.html';
  const abs = path.resolve(root, `.${rel}`);
  const base = path.resolve(root);
  if (abs !== base && !abs.startsWith(base + path.sep)) return notFound();
  try {
    const st = await fs.stat(abs);
    if (!st.isFile()) return notFound();
    const body = await fs.readFile(abs);
    const ext = path.extname(abs).toLowerCase();
    const type = TYPES[ext];
    if (!type) return notFound();
    const immutable = rel.startsWith('/assets/');
    return {
      status: 200,
      headers: { ...SECURITY_HEADERS, 'content-type': type, 'cache-control': immutable ? 'public, max-age=31536000, immutable' : 'no-cache' },
      body,
    };
  } catch {
    return notFound();
  }
}
