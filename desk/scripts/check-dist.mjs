#!/usr/bin/env node
// Post-build guard for the browser bundle (dist/). Fails the Netlify build if
// the shipped frontend could call localhost, call Railway or Telegram directly,
// or contains anything that looks like a server-side secret.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';

const root = process.argv[2] ?? 'dist';
const RULES = [
  [/\blocalhost\b|127\.0\.0\.1|\[::1\]/, 'a localhost address'],
  [/\.up\.railway\.app|railway\.internal/, 'a direct Railway URL (the browser must use the same-origin /api proxy)'],
  [/api\.telegram\.org/, 'a direct Telegram API URL'],
  [/\b(TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID|DESK_PROXY_SECRET|SESSION_SECRET|APP_PASSWORD_HASH|UPSTOX_ANALYTICS_TOKEN|UPSTOX_ACCESS_TOKEN)\b/, 'a server-side environment variable name'],
  [/\b\d{6,12}:[A-Za-z0-9_-]{30,}\b/, 'something shaped like a Telegram bot token'],
  [/pbkdf2\$sha256\$/, 'a password hash'],
];

const files = [];
(function walk(d) {
  for (const n of readdirSync(d)) {
    const p = path.join(d, n);
    if (statSync(p).isDirectory()) walk(p);
    else if (/\.(js|mjs|html|css|json|webmanifest)$/.test(n)) files.push(p);
  }
})(root);

const problems = [];
for (const f of files) {
  const text = readFileSync(f, 'utf8');
  for (const [re, what] of RULES) {
    const m = re.exec(text);
    if (m) problems.push(`${f}: contains ${what} ("${m[0].slice(0, 40)}")`);
  }
}
if (problems.length) {
  console.error(`check-dist: ${problems.length} problem(s)\n - ${problems.join('\n - ')}`);
  process.exit(1);
}
console.log(`check-dist: ${files.length} files clean (no localhost, no direct Railway/Telegram calls, no secrets).`);
