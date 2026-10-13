// Provider self-test. Free market-data endpoints change and some block cloud
// IPs, so the app can prove - from the deployed site itself - which sources
// actually work, instead of the owner discovering it mid-session.

import { MAIN_INDICES } from '../../shared/universe';
import type { ProviderHealth } from '../../shared/types';
import { configProblems, type Ctx } from '../config';
import { fetchEquityList, fetchFiiDii, fetchBreadth } from '../providers/nse';
import { fetchNews, MARKET_FEEDS } from '../providers/news';
import { telegramGetMe } from '../providers/telegram';
import { upstoxQuotes } from '../providers/upstox';
import { fetchJson } from '../http';
import { yahooQuote } from '../providers/yahoo';

async function probe(id: string, label: string, configured: boolean, run: () => Promise<string>, ctx: Ctx): Promise<ProviderHealth> {
  const checkedAt = ctx.now().toISOString();
  if (!configured) return { id, label, configured: false, ok: null, detail: 'Not configured', latencyMs: null, checkedAt };
  const t0 = Date.now();
  try {
    const detail = await run();
    return { id, label, configured, ok: true, detail, latencyMs: Date.now() - t0, checkedAt };
  } catch (e) {
    return { id, label, configured, ok: false, detail: e instanceof Error ? e.message : 'failed', latencyMs: Date.now() - t0, checkedAt };
  }
}

export async function runSelfTest(ctx: Ctx): Promise<{ problems: string[]; storage: string; offline: boolean; providers: ProviderHealth[] }> {
  const f = { fetchImpl: ctx.fetchImpl };
  const now = ctx.now();

  if (ctx.cfg.offline) {
    return {
      problems: configProblems(ctx.cfg),
      storage: ctx.cfg.storage,
      offline: true,
      providers: [{ id: 'fixture', label: 'Demo data (DESK_OFFLINE=1)', configured: true, ok: true, detail: 'All market data is synthetic. Unset DESK_OFFLINE for live data.', latencyMs: 0, checkedAt: now.toISOString() }],
    };
  }

  const nifty = MAIN_INDICES[0]!;
  const providers = await Promise.all([
    probe('storage', 'Storage (read/write/CAS)', true, async () => {
      const key = 'selftest/probe';
      const w = await ctx.kv.put(key, { at: now.toISOString() }, '*');
      if (!w.ok) throw new Error('write rejected');
      const r = await ctx.kv.get<{ at: string }>(key);
      if (!r.value || r.version !== w.version) throw new Error('read-after-write mismatch');
      const stale = await ctx.kv.put(key, { at: 'x' }, 'definitely-not-the-version');
      if (stale.ok) throw new Error('compare-and-set did not reject a stale version');
      await ctx.kv.del(key);
      return `${ctx.cfg.storage}: consistent, compare-and-set enforced`;
    }, ctx),
    probe('yahoo', 'Yahoo Finance (indices, equities, globals)', true, async () => {
      const q = await yahooQuote(nifty.yahoo, now, f);
      return `Nifty ${q.price.toFixed(2)} (${q.changePct.toFixed(2)}%)`;
    }, ctx),
    probe('upstox', 'Upstox Analytics token (official)', Boolean(ctx.cfg.upstoxToken), async () => {
      const key = nifty.upstox!;
      const r = await upstoxQuotes(ctx.cfg.upstoxToken!, [key], now, f);
      const q = Object.values(r)[0];
      if (!q) throw new Error('token accepted but no quote returned');
      return `Nifty ${q.price.toFixed(2)}`;
    }, ctx),
    probe('nse-archive', 'NSE archives (symbols, lot sizes, bhavcopy)', true, async () => `${(await fetchEquityList(f)).length} listed symbols`, ctx),
    probe('nse-api', 'NSE live API (FII/DII, breadth)', true, async () => {
      const fii = await fetchFiiDii(f).catch((e: Error) => { throw new Error(`FII/DII: ${e.message}`); });
      const b = await fetchBreadth(f).catch((e: Error) => { throw new Error(`breadth: ${e.message}`); });
      return `FII/DII ${fii.date}, ${b.advances} up / ${b.declines} down`;
    }, ctx),
    probe('news', 'News RSS', true, async () => {
      const r = await fetchNews(MARKET_FEEDS, now, 10, f);
      if (r.items.length === 0) throw new Error(r.failed.map((x) => `${x.feed}: ${x.reason}`).join('; ') || 'no items');
      return `${r.items.length} headlines (${r.failed.length} feed(s) failed)`;
    }, ctx),
    probe('telegram', 'Telegram (bot token check, nothing is sent)', Boolean(ctx.cfg.telegram), async () => {
      const me = await telegramGetMe(ctx.cfg.telegram!.token, ctx.fetchImpl);
      return `Bot @${me.username ?? me.id} accepted the token. Use "Send test message" to verify delivery to your chat.`;
    }, ctx),
    probe('engine', 'TIRE research engine (Railway, read-only health)', Boolean(ctx.cfg.engineUrl), async () => {
      const r = await fetchJson<{ token_configured?: boolean; session_window?: { session_window?: string }; data_root?: string }>(`${ctx.cfg.engineUrl}/api/health`, { timeoutMs: 6000, retries: 0, ...f });
      return `Reachable; provider token ${r.token_configured ? 'configured' : 'NOT configured'}; session ${r.session_window?.session_window ?? 'unknown'}.`;
    }, ctx),
  ]);

  return { problems: configProblems(ctx.cfg), storage: ctx.cfg.storage, offline: false, providers };
}
