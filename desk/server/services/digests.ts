// Telegram digests: a pre-market brief and an end-of-day summary. Facts only -
// no calls, no predictions. Each digest is claimed once per trading day with a
// CAS write so a retried or overlapping schedule cannot send twice.

import { fmtINR, fmtIstTime, fmtPct, fmtNum, fmtSigned } from '../../shared/format';
import { istDateStr } from '../../shared/india';
import { riskStatus, closedViews } from '../../shared/journal';
import type { Quote } from '../../shared/types';
import type { Ctx } from '../config';
import { escapeHtml } from '../providers/telegram';
import { update } from '../kv';
import { getFlows, getBan, getIpos, getNews, getOverview, getSectors, getSession } from './market';
import { getPortfolio } from './portfolio';
import { notify } from './notify';
import { getSettings, KEYS, listTrades } from './userdata';
import { getWatchlistView } from './watchlist';

export type DigestKind = 'pre' | 'eod';

const q = (x: Quote | null | undefined) => (x ? `${fmtNum(x.price)} (${fmtPct(x.changePct)})` : 'n/a');

export async function buildPreMarket(ctx: Ctx): Promise<string> {
  const [ov, flows, ban, news, ipos] = await Promise.all([
    getOverview(ctx).catch(() => null),
    getFlows(ctx).catch(() => null),
    getBan(ctx).catch(() => null),
    getNews(ctx).catch(() => null),
    getIpos(ctx).catch(() => null),
  ]);
  const L: string[] = [`<b>Pre-market brief</b> - ${istDateStr(ctx.now())}, ${fmtIstTime(ctx.now())} IST`];

  if (ov) {
    const g = ov.data.globals.filter((x) => x.quote);
    if (g.length) {
      L.push('', '<b>Global cues</b>');
      for (const x of g.slice(0, 10)) L.push(`${escapeHtml(x.name)}: ${q(x.quote)}`);
    }
    if (ov.data.vix) L.push('', `<b>India VIX</b> ${fmtNum(ov.data.vix.quote.price)} - ${ov.data.vix.reading.label}`);
    const n = ov.data.indices.find((i) => i.id === 'NIFTY')?.quote;
    if (n) L.push(`Nifty last close ${q(n)}`);
  }
  const cash = flows?.data.cash;
  if (cash) {
    L.push('', `<b>FII/DII cash (${escapeHtml(cash.date)})</b>`);
    if (cash.fii) L.push(`FII net ${fmtSigned(cash.fii.net, 0)} Cr - ${cash.fiiLabel}`);
    if (cash.dii) L.push(`DII net ${fmtSigned(cash.dii.net, 0)} Cr - ${cash.diiLabel}`);
  }
  const pos = flows?.data.positioning;
  if (pos?.indexFutLongPct != null) L.push(`FII index futures: ${pos.indexFutLongPct.toFixed(0)}% long - ${pos.read.label}`);
  if (ban && ban.data.symbols.length) L.push('', `<b>F&amp;O ban</b>: ${ban.data.symbols.map(escapeHtml).join(', ')}`);
  if (ipos && ipos.data.ipos.length) {
    L.push('', '<b>IPOs</b>');
    for (const i of ipos.data.ipos.slice(0, 5)) L.push(`${escapeHtml(i.name)} (${escapeHtml(i.status)}${i.priceBand ? `, ${escapeHtml(i.priceBand)}` : ''})`);
  }
  if (news) {
    L.push('', '<b>Headlines</b>');
    for (const n of news.data.items.slice(0, 5)) L.push(`- ${escapeHtml(n.title)}`);
  }
  const notes = [...(ov?.notes ?? []), ...(flows?.notes ?? [])].filter((n) => !n.startsWith('DEMO'));
  if (notes.length) L.push('', `<i>Data notes: ${escapeHtml(notes.slice(0, 3).join(' | '))}</i>`);
  L.push('', '<i>Information only. Not investment advice.</i>');
  return L.join('\n');
}

export async function buildEod(ctx: Ctx): Promise<string> {
  const now = ctx.now();
  const [ov, sec, flows, wl, pf, trades, settings] = await Promise.all([
    getOverview(ctx).catch(() => null),
    getSectors(ctx).catch(() => null),
    getFlows(ctx).catch(() => null),
    getWatchlistView(ctx, { deadlineMs: 8000 }).catch(() => null),
    getPortfolio(ctx).catch(() => null),
    listTrades(ctx),
    getSettings(ctx),
  ]);
  const L: string[] = [`<b>End-of-day summary</b> - ${istDateStr(now)}, ${fmtIstTime(now)} IST`];

  if (ov) {
    L.push('');
    for (const i of ov.data.indices.slice(0, 4)) if (i.quote) L.push(`${escapeHtml(i.name)}: ${q(i.quote)}`);
    if (ov.data.vix) L.push(`India VIX: ${fmtNum(ov.data.vix.quote.price)} (${ov.data.vix.reading.label})`);
    if (ov.data.breadth) L.push(`Breadth: ${ov.data.breadth.advances} up / ${ov.data.breadth.declines} down - ${ov.data.breadth.label}`);
  }
  if (sec && sec.data.sectors.length >= 4) {
    const s = sec.data.sectors;
    L.push('', `<b>Leaders</b>: ${s.slice(0, 3).map((x) => `${escapeHtml(x.name)} ${fmtPct(x.quote.changePct, 1)}`).join(', ')}`);
    L.push(`<b>Laggards</b>: ${s.slice(-3).reverse().map((x) => `${escapeHtml(x.name)} ${fmtPct(x.quote.changePct, 1)}`).join(', ')}`);
  }
  const cash = flows?.data.cash;
  if (cash?.fii) L.push('', `FII net ${fmtSigned(cash.fii.net, 0)} Cr, DII net ${fmtSigned(cash.dii?.net ?? null, 0)} Cr (${escapeHtml(cash.date)})`);

  if (wl) {
    const movers = wl.data.rows.filter((r) => r.quote).sort((a, b) => Math.abs(b.quote!.changePct) - Math.abs(a.quote!.changePct)).slice(0, 4);
    if (movers.length) {
      L.push('', '<b>Watchlist movers</b>');
      for (const r of movers) L.push(`${escapeHtml(r.item.symbol)} ${q(r.quote)}`);
    }
  }
  if (pf && pf.data.holdings.length) {
    const s = pf.data.summary;
    L.push('', `<b>Portfolio</b>: value ${fmtINR(s.value)}, day ${fmtINR(s.dayPnl)} (${fmtPct(s.dayPnlPct)}), total ${fmtINR(s.pnl)}`);
  }

  const rs = riskStatus(trades, settings, now);
  const today = istDateStr(now);
  const todays = closedViews(trades, settings).filter((v) => v.exitDate === today);
  L.push('', '<b>Journal</b>');
  L.push(`Trades today: ${rs.tradesToday}, closed net ${fmtINR(rs.realizedToday)}, loss limit used ${rs.lossLimitUsedPct.toFixed(0)}%`);
  L.push(`Risk guard: ${rs.state}${rs.state === 'OK' ? '' : ` - ${escapeHtml(rs.messages.join(' '))}`}`);
  if (todays.length === 0 && rs.openPositions > 0) L.push(`${rs.openPositions} position(s) still open. Update exits in the journal.`);
  L.push('', '<i>Charges and tax figures in the app are estimates.</i>');
  return L.join('\n');
}

export interface DigestResult {
  kind: DigestKind;
  sent: boolean;
  reason?: string;
}

export async function runDigest(ctx: Ctx, kind: DigestKind, opts: { force?: boolean } = {}): Promise<DigestResult> {
  const now = ctx.now();
  const session = await getSession(ctx);
  if (!opts.force && !session.isTradingDay) return { kind, sent: false, reason: `Not a trading day (${session.reason}).` };
  if (!ctx.cfg.telegram) return { kind, sent: false, reason: 'Telegram not configured.' };

  const today = istDateStr(now);
  let claimed = false;
  if (!opts.force) {
    await update<Record<string, string>>(ctx.kv, KEYS.digestSent, {}, (cur) => {
      claimed = cur[kind] !== today;
      return claimed ? { ...cur, [kind]: today } : cur;
    });
    if (!claimed) return { kind, sent: false, reason: 'Already sent today.' };
  }

  const text = kind === 'pre' ? await buildPreMarket(ctx) : await buildEod(ctx);
  const res = await notify(ctx, text, `digest-${kind}`);
  if (!res.delivered && claimed) {
    // release the claim so the next scheduled retry can deliver
    await update<Record<string, string>>(ctx.kv, KEYS.digestSent, {}, (cur) => {
      const { [kind]: _drop, ...rest } = cur;
      return rest;
    });
  }
  return { kind, sent: res.delivered, reason: res.error };
}
