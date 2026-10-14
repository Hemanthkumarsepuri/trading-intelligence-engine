// Single HTTP entry for /api/*. Framework-free: takes a Fetch Request, returns a
// Fetch Response, so it runs identically in the Railway Node server
// (server/node-http.ts), the local dev server and in tests.

import { ZodError, z } from 'zod';
import { positionSize, riskStatus } from '../shared/journal';
import { parseHoldingsCsv } from '../shared/portfolio';
import type { ApiResult, Meta, TelegramLog } from '../shared/types';
import { cleanSymbol, isValidSymbol } from '../shared/universe';
import { authenticate, checkPassword, clearCookie, clearFailures, clientKey, cookieName, issueToken, pruneRevocations, readCookie, reserveAttempt, revokeSession, SessionStoreError, sessionCookie, verifyToken } from './auth';
import { configProblems, type Ctx } from './config';
import { ProviderError } from './http';
import { ConflictError } from './kv';
import { alertInput, contractWatchInput, holdingInput, loginInput, settingsInput, tradeInput, watchInput } from './schemas';
import { parseContractId } from '../shared/contract';
import { istDateStr } from '../shared/india';
import type { ScanFilters } from '../shared/scanner';
import { getMtf, getUnderlyingView } from './services/wb/analysis';
import { assertListed, contractChart, contractView, explorerView, ExplorerError, searchWorkbench, trackedView } from './services/wb/contracts';
import { defaultExpiry, loadExpiries, resolveInstrument } from './services/wb/data';
import { getEvents } from './services/wb/events';
import { evaluateWbAlerts, getContext, getHealth, listWbAlerts } from './services/wb/misc';
import { runScan } from './services/wb/scanner';
import { runAlertsTick, lastTick } from './services/alertsRunner';
import { offsiteStatus, runOffsiteBackup, verifyLatestOffsite } from './services/offsite';
import { ImportError, exportAll, importAll, listBackups, restoreBackup, runBackup, verifyBackup } from './services/backup';
import { notify, notifyFailureOnly, telegramLog } from './services/notify';
import { schedulerStatus } from './scheduler';
import { telegramGetMe, TelegramError } from './providers/telegram';
import { fmtIstTime } from '../shared/format';
import { update } from './kv';
import { runDigest } from './services/digests';
import { getJournal } from './services/journalView';
import { getBan, getFlows, getIpos, getNews, getOverview, getSectors, getSession, type Svc } from './services/market';
import { findUnderlying, getChain, getExpiries } from './services/options';
import { getPortfolio } from './services/portfolio';
import { runSelfTest } from './services/selftest';
import { getLotSizes, searchSymbols, symbolExists } from './services/symbols';
import { getSymbolDetail } from './services/symbolDetail';
import * as U from './services/userdata';
import { getWatchlistView } from './services/watchlist';

type Json = unknown;

class HttpError extends Error {
  constructor(public status: number, public code: string, message: string, public headers: Record<string, string> = {}) {
    super(message);
  }
}

interface Route {
  method: 'GET' | 'POST' | 'PUT' | 'DELETE';
  path: string;
  pattern: RegExp;
  keys: string[];
  public?: boolean;
  maxBytes?: number;
  handler: (c: RouteCtx) => Promise<Reply>;
}

interface RouteCtx {
  ctx: Ctx;
  req: Request;
  url: URL;
  params: Record<string, string>;
  body: () => Promise<unknown>;
}

/** What a handler returns: plain data, or data with provenance. */
type Reply = { data: Json; meta?: Partial<Meta>; status?: number; headers?: Record<string, string> };

const svc = <T>(r: Svc<T>): Reply => ({ data: r.data, meta: { source: r.source, stale: r.stale, notes: r.notes } });
const plain = (data: Json, status = 200): Reply => ({ data, status });

const routes: Route[] = [];
function route(method: Route['method'], path: string, handler: Route['handler'], opts: { public?: boolean; maxBytes?: number } = {}): void {
  const keys: string[] = [];
  const src = path.replace(/:([a-zA-Z]+)/g, (_m, k: string) => {
    keys.push(k);
    return '([^/]+)';
  });
  routes.push({ method, path, pattern: new RegExp(`^${src}/?$`), keys, handler, ...opts });
}

const DEFAULT_MAX_BODY = 256 * 1024;
const IMPORT_MAX_BODY = 5 * 1024 * 1024; // Netlify's synchronous payload ceiling is ~6 MB

function cleanSym(raw: string): string {
  const s = cleanSymbol(decodeURIComponent(raw));
  if (!isValidSymbol(s)) throw new HttpError(400, 'BAD_SYMBOL', 'Invalid symbol');
  return s;
}

/** Problems that block login. A plain-text dev password is allowed (and flagged on the status page). */
function setupProblems(ctx: Ctx): string[] {
  return configProblems(ctx.cfg).filter((p) => !p.startsWith('APP_PASSWORD is set in plain text'));
}

/** Session check including revocation. If revocation cannot be checked, fail closed with 503 (never accept). */
async function sessionOrThrow(req: Request, ctx: Ctx) {
  try {
    return await authenticate(req, ctx.cfg, ctx.kv, ctx.now());
  } catch (e) {
    if (e instanceof SessionStoreError) throw new HttpError(503, 'AUTH_UNAVAILABLE', 'Cannot verify the session right now. Please retry.');
    throw e;
  }
}

/** Every registered route (used by tests to prove each one is protected). */
export function listRoutes(): { method: Route['method']; path: string; public: boolean }[] {
  return routes.map((r) => ({ method: r.method, path: r.path, public: Boolean(r.public) }));
}

// ---- Public routes --------------------------------------------------------------------

route('GET', '/api/health', async () => plain({ ok: true }), { public: true });

route(
  'GET',
  '/api/auth/me',
  async ({ ctx, req }) => {
    const authed = Boolean(await sessionOrThrow(req, ctx));
    return plain({ authenticated: authed, configured: setupProblems(ctx).length === 0, demo: ctx.cfg.offline });
  },
  { public: true },
);

route(
  'POST',
  '/api/auth/login',
  async ({ ctx, req, body }) => {
    const problems = setupProblems(ctx);
    if (problems.length > 0) throw new HttpError(503, 'NOT_CONFIGURED', `Server is not configured: ${problems.join(' ')}`);
    const { password } = loginInput.parse(await body());
    const key = clientKey(req);
    // Admit (and count) the attempt atomically BEFORE the slow hash check, so parallel guesses cannot exceed the limit.
    const slot = await reserveAttempt(ctx.kv, key, ctx.now());
    if (!slot.allowed) throw new HttpError(429, 'LOCKED', `Too many attempts. Try again in ${Math.ceil(slot.retryAfterSec / 60)} minute(s).`, { 'Retry-After': String(slot.retryAfterSec) });
    if (!(await checkPassword(ctx.cfg, password))) {
      await new Promise((r) => setTimeout(r, 400));
      throw new HttpError(401, 'BAD_CREDENTIALS', 'Incorrect password.');
    }
    await clearFailures(ctx.kv, key);
    const { token, maxAge } = issueToken(ctx.cfg.sessionSecret!, ctx.now());
    const secure = new URL(req.url).protocol === 'https:';
    return { data: { authenticated: true }, headers: { 'Set-Cookie': sessionCookie(token, maxAge, secure) } };
  },
  { public: true, maxBytes: 4096 },
);

route(
  'POST',
  '/api/auth/logout',
  async ({ ctx, req }) => {
    const secure = new URL(req.url).protocol === 'https:';
    const headers = { 'Set-Cookie': clearCookie(secure) };
    const session = verifyToken(readCookie(req.headers.get('cookie'), cookieName(secure)), ctx.cfg.sessionSecret, ctx.now());
    if (session) {
      try {
        await revokeSession(ctx.kv, session);
      } catch {
        // The browser cookie is still cleared, but this is NOT reported as a successful sign-out.
        console.error('[auth] logout: server-side revocation failed');
        throw new HttpError(503, 'LOGOUT_NOT_REVOKED', 'Signed out in this browser, but the server could not revoke the session. Try again.', headers);
      }
      await pruneRevocations(ctx.kv, ctx.now()).catch(() => 0);
    }
    return { data: { authenticated: false }, headers };
  },
  { public: true, maxBytes: 1024 },
);

// ---- Market ---------------------------------------------------------------------------------

route('GET', '/api/session', async ({ ctx }) => plain(await getSession(ctx)));
route('GET', '/api/overview', async ({ ctx }) => svc(await getOverview(ctx)));
route('GET', '/api/sectors', async ({ ctx }) => svc(await getSectors(ctx)));
route('GET', '/api/flows', async ({ ctx }) => svc(await getFlows(ctx)));
route('GET', '/api/ban', async ({ ctx }) => svc(await getBan(ctx)));
route('GET', '/api/ipos', async ({ ctx }) => svc(await getIpos(ctx)));
route('GET', '/api/news', async ({ ctx, url }) => {
  const sym = url.searchParams.get('symbol');
  return svc(await getNews(ctx, sym ? cleanSym(sym) : undefined));
});

route('GET', '/api/symbols/search', async ({ ctx, url }) => plain({ results: await searchSymbols(ctx, (url.searchParams.get('q') ?? '').slice(0, 30)) }));
route('GET', '/api/symbols/:symbol', async ({ ctx, params }) => svc(await getSymbolDetail(ctx, cleanSym(params.symbol!))));

route('GET', '/api/options/:id/expiries', async ({ ctx, params }) => {
  const u = findUnderlying(params.id!);
  if (!u) throw new HttpError(404, 'NOT_FOUND', 'Unknown underlying');
  return svc(await getExpiries(ctx, u));
});
route('GET', '/api/options/:id/chain', async ({ ctx, params, url }) => {
  const u = findUnderlying(params.id!);
  if (!u) throw new HttpError(404, 'NOT_FOUND', 'Unknown underlying');
  const ex = await getExpiries(ctx, u);
  const asked = url.searchParams.get('expiry');
  if (asked && !/^\d{4}-\d{2}-\d{2}$/.test(asked)) throw new HttpError(400, 'BAD_EXPIRY', 'expiry must be YYYY-MM-DD');
  const expiry = asked ?? ex.data.expiries[0];
  if (!expiry) throw new HttpError(404, 'NO_EXPIRY', 'No upcoming expiry found');
  if (asked && !ex.data.expiries.includes(asked)) throw new HttpError(400, 'BAD_EXPIRY', `${asked} is not a listed expiry`);
  const chain = await getChain(ctx, u, expiry, ex.data.lotSize);
  return { data: { ...chain.data, expiry, expiries: ex.data.expiries }, meta: { source: chain.source, stale: chain.stale || ex.stale, notes: [...ex.notes, ...chain.notes] } };
});

// ---- Watchlist ---------------------------------------------------------------------------------

route('GET', '/api/watchlist', async ({ ctx }) => svc(await getWatchlistView(ctx)));
route(
  'POST',
  '/api/watchlist',
  async ({ ctx, body }) => {
    const input = watchInput.parse(await body());
    const chk = await symbolExists(ctx, input.symbol);
    if (!chk.exists) throw new HttpError(422, 'UNKNOWN_SYMBOL', `${input.symbol} is not a listed NSE symbol.`);
    const item = await U.upsertWatch(ctx, input);
    return { data: item, status: 201, meta: { notes: chk.verified ? [] : ['Symbol could not be verified against the NSE list right now.'] } };
  },
);
route('PUT', '/api/watchlist/:symbol', async ({ ctx, params, body }) => plain(await U.upsertWatch(ctx, watchInput.parse({ ...(await body() as object), symbol: params.symbol }))));
route('DELETE', '/api/watchlist/:symbol', async ({ ctx, params }) => {
  await U.removeWatch(ctx, cleanSym(params.symbol!));
  return plain({ deleted: true });
});

// ---- Alerts ----------------------------------------------------------------------------------------

route('GET', '/api/alerts', async ({ ctx }) => plain({ alerts: await U.listAlerts(ctx), events: await U.listEvents(ctx), lastTick: await lastTick(ctx), telegram: Boolean(ctx.cfg.telegram) }));
route('POST', '/api/alerts', async ({ ctx, body }) => plain(await U.createAlert(ctx, alertInput.parse(await body())), 201));
route('PUT', '/api/alerts/:id', async ({ ctx, params, body }) => plain(await U.updateAlert(ctx, params.id!, alertInput.parse(await body()))));
route('DELETE', '/api/alerts/:id', async ({ ctx, params }) => {
  await U.deleteAlert(ctx, params.id!);
  return plain({ deleted: true });
});
route('POST', '/api/alerts-run', async ({ ctx }) => plain(await runAlertsTick(ctx)), { maxBytes: 1024 });

// ---- Journal & risk ----------------------------------------------------------------------------------

route('GET', '/api/risk', async ({ ctx }) => plain(riskStatus(await U.listTrades(ctx), await U.getSettings(ctx), ctx.now())));
route('GET', '/api/journal', async ({ ctx, url }) => plain(await getJournal(ctx, url.searchParams.get('fy') ?? undefined)));
route('POST', '/api/trades', async ({ ctx, body }) => plain(await U.createTrade(ctx, tradeInput.parse(await body())), 201));
route('PUT', '/api/trades/:id', async ({ ctx, params, body }) => plain(await U.updateTrade(ctx, params.id!, tradeInput.parse(await body()))));
route('DELETE', '/api/trades/:id', async ({ ctx, params }) => {
  await U.deleteTrade(ctx, params.id!);
  return plain({ deleted: true });
});

const sizingQuery = z.object({
  entry: z.coerce.number().positive().max(1e8),
  stop: z.coerce.number().positive().max(1e8),
  symbol: z.string().max(20).optional(),
  riskPct: z.coerce.number().min(0.05).max(10).optional(),
  lotSize: z.coerce.number().int().positive().max(100000).optional(),
});
route('GET', '/api/sizing', async ({ ctx, url }) => {
  const q = sizingQuery.parse(Object.fromEntries(url.searchParams));
  const s = await U.getSettings(ctx);
  let lot = q.lotSize ?? null;
  const notes: string[] = [];
  if (lot === null && q.symbol) {
    lot = (await getLotSizes(ctx))[cleanSymbol(q.symbol)] ?? null;
    if (lot === null) notes.push(`No F&O lot size found for ${q.symbol}; sizing is in shares.`);
  }
  return { data: positionSize({ capital: s.capital, riskPct: q.riskPct ?? s.riskPerTradePct, entry: q.entry, stop: q.stop, lotSize: lot }), meta: { notes } };
});

// ---- Portfolio ------------------------------------------------------------------------------------------

route('GET', '/api/portfolio', async ({ ctx }) => svc(await getPortfolio(ctx)));
route('POST', '/api/holdings', async ({ ctx, body }) => plain(await U.createHoldings(ctx, [holdingInput.parse(await body())]), 201));
route('PUT', '/api/holdings/:id', async ({ ctx, params, body }) => plain(await U.updateHolding(ctx, params.id!, holdingInput.parse(await body()))));
route('DELETE', '/api/holdings/:id', async ({ ctx, params }) => {
  await U.deleteHolding(ctx, params.id!);
  return plain({ deleted: true });
});

const importBody = z.object({ csv: z.string().max(2_000_000), mode: z.enum(['preview', 'append', 'replace']), confirm: z.boolean().optional() });
route(
  'POST',
  '/api/holdings-import',
  async ({ ctx, body }) => {
    const b = importBody.parse(await body());
    const parsed = parseHoldingsCsv(b.csv);
    const errors = [...parsed.errors];
    const valid: z.infer<typeof holdingInput>[] = [];
    parsed.rows.forEach((r, i) => {
      const v = holdingInput.safeParse(r);
      if (v.success) valid.push(v.data);
      else errors.push(`Row ${i + 2} (${r.symbol}): ${v.error.issues[0]?.message ?? 'invalid'}`);
    });
    if (b.mode === 'preview') return plain({ rows: valid, errors, committed: false });
    if (valid.length === 0) throw new HttpError(422, 'NOTHING_TO_IMPORT', errors[0] ?? 'No valid rows found.');
    if (b.mode === 'replace' && b.confirm !== true) throw new HttpError(400, 'CONFIRM_REQUIRED', 'Replacing holdings requires confirm: true.');
    const saved = b.mode === 'replace' ? await U.replaceHoldings(ctx, valid) : await U.createHoldings(ctx, valid);
    return plain({ rows: saved, errors, committed: true });
  },
  { maxBytes: 3 * 1024 * 1024 },
);

// ---- Settings, status, backup ---------------------------------------------------------------------------

route('GET', '/api/settings', async ({ ctx }) => plain(await U.getSettings(ctx)));
route('PUT', '/api/settings', async ({ ctx, body }) => plain(await U.saveSettings(ctx, settingsInput.parse(await body()))));

route('GET', '/api/status', async ({ ctx }) => {
  const test = await runSelfTest(ctx);
  return plain({ ...test, lastTick: await lastTick(ctx), scheduler: await schedulerStatus(ctx), backups: (await listBackups(ctx)).slice(0, 20), offsite: await offsiteStatus(ctx), now: ctx.now().toISOString() });
});

route('GET', '/api/telegram/status', async ({ ctx }) => {
  const log = await telegramLog(ctx);
  const chat = ctx.cfg.telegram?.chatId ?? null;
  return plain({ configured: Boolean(ctx.cfg.telegram), chatHint: chat ? `…${chat.slice(-3)}` : null, ...log });
});
/** Owner-initiated only: validates the token (getMe) and sends one test message to the configured chat. */
route('POST', '/api/telegram/test', async ({ ctx }) => {
  if (!ctx.cfg.telegram) throw new HttpError(409, 'NOT_CONFIGURED', 'Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID on the Railway service first.');
  const now = ctx.now();
  let allowed = false;
  await update<TelegramLog>(ctx.kv, U.KEYS.telegramLog, { lastSuccessAt: null, lastSuccessKind: null, lastTestAt: null, failures: [] }, (cur) => {
    allowed = !cur.lastTestAt || now.getTime() - Date.parse(cur.lastTestAt) >= 30_000;
    return allowed ? { ...cur, lastTestAt: now.toISOString() } : cur;
  });
  if (!allowed) throw new HttpError(429, 'TOO_SOON', 'A test message was sent less than 30 seconds ago.');
  let bot: string | null = null;
  try {
    const me = await telegramGetMe(ctx.cfg.telegram.token, ctx.fetchImpl);
    bot = me.username ? `@${me.username}` : String(me.id);
  } catch (e) {
    const res = { delivered: false, error: e instanceof Error ? e.message : 'failed', category: e instanceof TelegramError ? e.kind : 'UNKNOWN' };
    await notifyFailureOnly(ctx, 'test', res);
    return plain({ connection: false, sent: false, bot: null, error: res.error });
  }
  const res = await notify(ctx, `<b>TIRE Desk test message</b>\nRequested by you at ${fmtIstTime(now)} IST. Alerts will arrive in this chat.`, 'test');
  return plain({ connection: true, sent: res.delivered, bot, error: res.error ?? null });
}, { maxBytes: 1024 });

route('GET', '/api/backup/verify', async ({ ctx, url }) => {
  const key = url.searchParams.get('key') ?? '';
  return plain(await verifyBackup(ctx, key));
});
route('POST', '/api/backup/restore', async ({ ctx, body }) => {
  const b = z.object({ key: z.string().regex(/^backup\/[\w.-]+$/), confirm: z.literal(true) }).parse(await body());
  return plain(await restoreBackup(ctx, b.key));
}, { maxBytes: 1024 });
route('POST', '/api/digest/:kind', async ({ ctx, params }) => {
  if (params.kind !== 'pre' && params.kind !== 'eod') throw new HttpError(404, 'NOT_FOUND', 'Unknown digest');
  return plain(await runDigest(ctx, params.kind, { force: true }));
}, { maxBytes: 1024 });

route('GET', '/api/export', async ({ ctx }) => ({
  data: await exportAll(ctx),
  headers: { 'Content-Disposition': `attachment; filename="tire-desk-backup-${ctx.now().toISOString().slice(0, 10)}.json"` },
}));
route('POST', '/api/import', async ({ ctx, body }) => plain(await importAll(ctx, await body())), { maxBytes: IMPORT_MAX_BODY });
route('POST', '/api/backup/run', async ({ ctx }) => plain(await runBackup(ctx)), { maxBytes: 1024 });
/** Owner-initiated: write an encrypted off-volume copy now. */
route('POST', '/api/backup/offsite/run', async ({ ctx }) => {
  try {
    return plain(await runOffsiteBackup(ctx));
  } catch (e) {
    throw new HttpError(502, 'OFFSITE_FAILED', e instanceof Error ? e.message : 'Off-site backup failed.');
  }
}, { maxBytes: 1024 });
/** Fetch the newest off-site object, decrypt and validate it. Writes nothing. */
route('GET', '/api/backup/offsite/verify', async ({ ctx }) => plain(await verifyLatestOffsite(ctx)));

// ---- Options Workbench (read-only research; no order endpoint exists or may be added here) ---------------------------------

const tfParam = z.enum(['1m', '3m', '5m', '15m', '30m', '1h', '1d']);
const idParam = z.string().max(60);

async function instrumentOr404(ctx: Ctx, raw: string) {
  const inst = await resolveInstrument(ctx, decodeURIComponent(raw));
  if (!inst) throw new HttpError(404, 'UNKNOWN_UNDERLYING', 'Unknown underlying symbol.');
  return inst;
}

function explorerErr(e: unknown): never {
  if (e instanceof ExplorerError) throw new HttpError(e.code === 'UNSUPPORTED_CONTRACT' || e.code === 'BAD_EXPIRY' || e.code === 'NO_EXPIRY' ? 422 : e.code === 'BAD_CONTRACT' ? 400 : 404, e.code, e.message);
  if (e instanceof ProviderError && e.kind === 'NOT_CONFIGURED') throw new HttpError(422, 'UNSUPPORTED_CONTRACT', e.message);
  throw e;
}

route('GET', '/api/workbench/search', async ({ ctx, url }) => {
  const q = (url.searchParams.get('q') ?? '').slice(0, 60);
  return plain(await searchWorkbench(ctx, q));
});
route('GET', '/api/workbench/context', async ({ ctx }) => plain(await getContext(ctx)));
route('GET', '/api/workbench/health', async ({ ctx }) => plain(await getHealth(ctx)));

route('GET', '/api/workbench/underlying/:id', async ({ ctx, params, url }) => {
  const inst = await instrumentOr404(ctx, params.id!);
  const tf = tfParam.parse(url.searchParams.get('tf') ?? '15m');
  const v = await getUnderlyingView(ctx, inst, tf);
  return { data: v, meta: { source: v.price.prov?.source ?? 'none', stale: v.freshness === 'STALE', notes: v.notes } };
});
route('GET', '/api/workbench/underlying/:id/mtf', async ({ ctx, params }) => plain(await getMtf(ctx, await instrumentOr404(ctx, params.id!))));
route('GET', '/api/workbench/underlying/:id/expiries', async ({ ctx, params }) => {
  const inst = await instrumentOr404(ctx, params.id!);
  try {
    const ex = await loadExpiries(ctx, inst);
    const d = defaultExpiry(ex.expiries, istDateStr(ctx.now()));
    return { data: { instrument: inst, expiries: ex.expiries, lotSize: ex.lotSize ?? inst.lotSize, default: d }, meta: { source: ex.source, stale: ex.stale, notes: [] } };
  } catch (e) {
    return explorerErr(e);
  }
});
route('GET', '/api/workbench/chain/:id', async ({ ctx, params, url }) => {
  const inst = await instrumentOr404(ctx, params.id!);
  const expiry = url.searchParams.get('expiry');
  if (expiry && !/^\d{4}-\d{2}-\d{2}$/.test(expiry)) throw new HttpError(400, 'BAD_EXPIRY', 'expiry must be YYYY-MM-DD');
  const each = z.coerce.number().int().min(3).max(40).catch(12).parse(url.searchParams.get('each') ?? 12);
  try {
    const v = await explorerView(ctx, inst, expiry, each);
    return { data: v, meta: { source: v.provenance.source, stale: v.state === 'STALE', notes: v.notes } };
  } catch (e) {
    return explorerErr(e);
  }
});
route('GET', '/api/workbench/contract', async ({ ctx, url }) => {
  const id = idParam.parse(url.searchParams.get('id') ?? '');
  try {
    const v = await contractView(ctx, id);
    return { data: v, meta: { source: v.chain.source, stale: v.chain.stale || v.validation.state === 'STALE', notes: v.notes } };
  } catch (e) {
    return explorerErr(e);
  }
});
route('GET', '/api/workbench/contract/chart', async ({ ctx, url }) => {
  const id = idParam.parse(url.searchParams.get('id') ?? '');
  const tf = tfParam.parse(url.searchParams.get('tf') ?? '5m');
  try {
    return plain(await contractChart(ctx, id, tf));
  } catch (e) {
    return explorerErr(e);
  }
});

route('GET', '/api/workbench/contracts', async ({ ctx }) => {
  const t = await trackedView(ctx);
  return { data: t, meta: { notes: t.notes } };
});
route(
  'POST',
  '/api/workbench/contracts',
  async ({ ctx, body }) => {
    const input = contractWatchInput.parse(await body());
    try {
      await assertListed(ctx, parseContractId(input.id)!);
    } catch (e) {
      return explorerErr(e);
    }
    const r = await U.addContract(ctx, input);
    return { data: r, status: r.duplicate ? 200 : 201, meta: { notes: r.duplicate ? ['Already tracked; nothing was changed.'] : [] } };
  },
  { maxBytes: 2048 },
);
route('DELETE', '/api/workbench/contracts', async ({ ctx, url }) => {
  const id = idParam.parse(url.searchParams.get('id') ?? '');
  if (!parseContractId(id)) throw new HttpError(400, 'BAD_CONTRACT', 'Not a valid contract id.');
  await U.removeContract(ctx, id);
  return plain({ deleted: true });
});

const bool = z.enum(['1', '0', 'true', 'false']).transform((v) => v === '1' || v === 'true');
const scanQuery = z.object({
  segment: z.enum(['ALL', 'STOCK', 'INDEX']).optional(),
  direction: z.enum(['ANY', 'UP', 'DOWN']).optional(),
  minLiquidity: z.enum(['ANY', 'MODERATE', 'LIQUID']).optional(),
  optionType: z.enum(['ANY', 'CE', 'PE']).optional(),
  trend: z.enum(['ANY', 'UP', 'DOWN']).optional(),
  expiry: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
  minRvol: z.coerce.number().min(0).max(20).optional(),
  avoidEvents: bool.optional(),
  requireOptions: bool.optional(),
  cap: z.coerce.number().int().min(5).max(60).optional(),
  states: z.string().max(200).optional(),
});
const STATE_NAMES = ['NOT_READY', 'APPROACHING_LEVEL', 'POTENTIAL_BREAKOUT', 'CONFIRMATION_PENDING', 'CONFIRMED', 'FAILED', 'INVALIDATED'] as const;
route('GET', '/api/workbench/scan', async ({ ctx, url }) => {
  const q = scanQuery.parse(Object.fromEntries(url.searchParams));
  const { cap, states, minRvol, ...rest } = q;
  const filters: Partial<ScanFilters> = { ...rest };
  if (minRvol !== undefined) filters.minRvol = minRvol === 0 ? null : minRvol;
  if (states) {
    const list = states.split(',').map((s) => s.trim()).filter((s): s is (typeof STATE_NAMES)[number] => (STATE_NAMES as readonly string[]).includes(s));
    if (list.length === 0) throw new HttpError(400, 'VALIDATION', 'states: none of the names are recognised');
    filters.states = list;
  }
  const r = await runScan(ctx, filters, cap ?? 40);
  return { data: r, meta: { source: 'scanner', stale: false, notes: r.notes } };
});

route('GET', '/api/workbench/events', async ({ ctx, url }) => {
  const symbols = (url.searchParams.get('symbols') ?? '').split(',').map((s) => s.trim().toUpperCase()).filter(Boolean).slice(0, 8);
  for (const s of symbols) if (!isValidSymbol(s)) throw new HttpError(400, 'BAD_SYMBOL', `Invalid symbol: ${s.slice(0, 24)}`);
  const v = await getEvents(ctx, symbols);
  return { data: v, meta: { notes: v.notes } };
});

route('GET', '/api/workbench/alerts', async ({ ctx }) => plain({ alerts: await listWbAlerts(ctx) }));
route('POST', '/api/workbench/alerts/evaluate', async ({ ctx }) => plain(await evaluateWbAlerts(ctx)), { maxBytes: 1024 });

// ---- Dispatcher ---------------------------------------------------------------------------------------------

const BASE_HEADERS = {
  'Content-Type': 'application/json; charset=utf-8',
  'Cache-Control': 'no-store',
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
};

function respond(status: number, payload: ApiResult<unknown>, extra: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(payload), { status, headers: { ...BASE_HEADERS, ...extra } });
}

function fail(status: number, code: string, message: string, extra: Record<string, string> = {}): Response {
  return respond(status, { ok: false, error: { code, message } }, extra);
}

async function readBody(req: Request, maxBytes: number): Promise<unknown> {
  const declared = Number(req.headers.get('content-length') ?? '0');
  if (declared > maxBytes) throw new HttpError(413, 'TOO_LARGE', 'Request body too large.');
  const ct = req.headers.get('content-type') ?? '';
  if (!ct.toLowerCase().includes('application/json')) throw new HttpError(415, 'BAD_CONTENT_TYPE', 'Content-Type must be application/json.');
  const text = await req.text();
  if (text.length > maxBytes) throw new HttpError(413, 'TOO_LARGE', 'Request body too large.');
  if (text.trim() === '') return {};
  try {
    return JSON.parse(text);
  } catch {
    throw new HttpError(400, 'BAD_JSON', 'Request body is not valid JSON.');
  }
}

/**
 * CSRF defence in depth (SameSite=Strict is the first line): a browser-sent
 * Origin must be one of the exact configured origins (or this server's own
 * origin in development). In production an Origin header is mandatory on writes.
 */
function checkOrigin(req: Request, url: URL, ctx: Ctx): void {
  const origin = req.headers.get('origin');
  const allowed = new Set([...ctx.cfg.allowedOrigins, ...(ctx.cfg.production ? [] : [url.origin])]);
  if (origin === null) {
    if (ctx.cfg.production) throw new HttpError(403, 'BAD_ORIGIN', 'Origin header required.');
  } else if (!allowed.has(origin)) {
    throw new HttpError(403, 'BAD_ORIGIN', 'Cross-origin request refused.');
  }
  const site = req.headers.get('sec-fetch-site');
  if (site && site !== 'same-origin' && site !== 'none') throw new HttpError(403, 'BAD_ORIGIN', 'Cross-site request refused.');
}

export async function handle(req: Request, ctxIn: Ctx): Promise<Response> {
  const url = new URL(req.url);
  // DESK_OFFLINE demo only (production refuses offline mode): a header may freeze the clock so browser tests are deterministic.
  let ctx = ctxIn;
  const demoNow = ctxIn.cfg.offline ? req.headers.get('x-desk-demo-now') : null;
  if (demoNow && !Number.isNaN(Date.parse(demoNow))) {
    const frozen = new Date(demoNow);
    ctx = { ...ctxIn, now: () => new Date(frozen.getTime()) };
  }
  const method = req.method.toUpperCase();
  try {
    if (method === 'OPTIONS') return new Response(null, { status: 204, headers: { ...BASE_HEADERS, Allow: 'GET, POST, PUT, DELETE' } });

    let matched: { r: Route; params: Record<string, string> } | null = null;
    let pathExists = false;
    for (const r of routes) {
      const m = r.pattern.exec(url.pathname);
      if (!m) continue;
      pathExists = true;
      if (r.method !== method) continue;
      matched = { r, params: Object.fromEntries(r.keys.map((k, i) => [k, m[i + 1]!])) };
      break;
    }
    if (!matched) return pathExists ? fail(405, 'METHOD_NOT_ALLOWED', 'Method not allowed') : fail(404, 'NOT_FOUND', 'Not found');

    const { r, params } = matched;
    if (method !== 'GET') checkOrigin(req, url, ctx);
    if (!r.public && !(await sessionOrThrow(req, ctx))) return fail(401, 'UNAUTHENTICATED', 'Sign in required.');

    let cached: Promise<unknown> | null = null;
    const body = () => (cached ??= readBody(req, r.maxBytes ?? DEFAULT_MAX_BODY));
    const reply = await r.handler({ ctx, req, url, params, body });

    const meta: Meta = { asOf: ctx.now().toISOString(), source: 'app', stale: false, notes: [], ...reply.meta };
    return respond(reply.status ?? 200, { ok: true, data: reply.data, meta }, reply.headers);
  } catch (e) {
    return mapError(e, url);
  }
}

function mapError(e: unknown, url: URL): Response {
  if (e instanceof HttpError) return fail(e.status, e.code, e.message, e.headers);
  if (e instanceof ZodError) {
    const i = e.issues[0];
    const where = i && i.path.length ? `${i.path.join('.')}: ` : '';
    return fail(400, 'VALIDATION', `${where}${i?.message ?? 'Invalid input'}`);
  }
  if (e instanceof ImportError) return fail(422, 'IMPORT_REJECTED', `${e.message} ${e.problems.join('; ')}`);
  if (e instanceof U.NotFound) return fail(404, 'NOT_FOUND', e.message);
  if (e instanceof U.LimitError) return fail(422, 'LIMIT', e.message);
  if (e instanceof ConflictError) return fail(409, 'CONFLICT', 'Another change happened at the same time. Please retry.');
  if (e instanceof ProviderError) return fail(502, 'UPSTREAM', e.message);
  // Unexpected: log in full server-side, never leak internals to the client.
  console.error(`[api] ${url.pathname}`, e);
  if (e instanceof Error && /unavailable|No quotes available/i.test(e.message)) return fail(502, 'UPSTREAM', e.message);
  return fail(500, 'INTERNAL', 'Something went wrong on the server.');
}
