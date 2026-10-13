// Headlines from public RSS feeds. No LLM, no scraping of article pages.
// Tone and event tags are transparent keyword rules, labelled as such in the UI.

import { XMLParser } from 'fast-xml-parser';
import { fetchText, mapLimit, ProviderError, type FetchOpts } from '../http';

export interface NewsItem {
  id: string;
  title: string;
  link: string;
  source: string;
  publishedAt: string | null;
  tone: 'positive' | 'negative' | 'neutral';
  tags: string[];
}

export interface Feed {
  id: string;
  label: string;
  url: string;
}

const gn = (q: string) => `https://news.google.com/rss/search?q=${encodeURIComponent(q)}&hl=en-IN&gl=IN&ceid=IN:en`;

export const MARKET_FEEDS: Feed[] = [
  { id: 'gn-markets', label: 'Google News: Indian markets', url: gn('Sensex OR Nifty OR "stock market" India when:1d') },
  { id: 'gn-macro', label: 'Google News: RBI & economy', url: gn('RBI OR "repo rate" OR "India inflation" OR GDP when:2d') },
  { id: 'et-markets', label: 'Economic Times Markets', url: 'https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms' },
  { id: 'mint-markets', label: 'Mint Markets', url: 'https://www.livemint.com/rss/markets' },
  { id: 'bs-markets', label: 'Business Standard Markets', url: 'https://www.business-standard.com/rss/markets-106.rss' },
  { id: 'mc-top', label: 'Moneycontrol Top News', url: 'https://www.moneycontrol.com/rss/MCtopnews.xml' },
];

export function symbolFeed(symbol: string, name?: string): Feed {
  const q = name ? `"${name}" OR ${symbol} share NSE when:7d` : `${symbol} share NSE when:7d`;
  return { id: `gn-${symbol}`, label: `Google News: ${symbol}`, url: gn(q) };
}

const POS = ['surge', 'rally', 'jump', 'soar', 'gain', 'rise', 'record high', 'upgrade', 'beat', 'strong', 'profit rises', 'buy rating', 'outperform', 'recover', 'boost', 'win', 'bags order', 'wins order', 'approval'];
const NEG = ['plunge', 'crash', 'slump', 'tumble', 'fall', 'drop', 'slide', 'downgrade', 'miss', 'weak', 'loss', 'probe', 'fraud', 'penalty', 'ban', 'cut', 'selloff', 'sell-off', 'fear', 'concern', 'default', 'raid', 'resign'];

const TAGS: [string, RegExp][] = [
  ['RBI / Rates', /\b(rbi|repo rate|monetary policy|mpc|rate cut|rate hike|cash reserve)\b/i],
  ['Results', /\b(q[1-4]|quarterly|results?|earnings|net profit|ebitda|revenue)\b/i],
  ['FII / DII', /\b(fii|fpi|dii|foreign (portfolio )?investors?|institutional)\b/i],
  ['SEBI / Regulation', /\b(sebi|regulator|circular|f&o rules|stt)\b/i],
  ['Crude / Commodities', /\b(crude|brent|opec|oil price|gold price)\b/i],
  ['Rupee', /\b(rupee|usd\/inr|forex)\b/i],
  ['IPO', /\b(ipo|listing|gmp|anchor investors?|allotment)\b/i],
  ['Budget / Policy', /\b(budget|gst|tax|policy|tariff|customs)\b/i],
  ['Global', /\b(fed|us fed|wall street|nasdaq|dow|china|tariffs?|trump)\b/i],
  ['Geopolitics', /\b(war|ceasefire|sanctions?|attack|border|tension)\b/i],
  ['Dividend / Corporate action', /\b(dividend|bonus|stock split|buyback|rights issue|record date)\b/i],
];

export function classify(title: string): { tone: NewsItem['tone']; tags: string[] } {
  const t = title.toLowerCase();
  const pos = POS.filter((w) => t.includes(w)).length;
  const neg = NEG.filter((w) => t.includes(w)).length;
  return {
    tone: pos > neg ? 'positive' : neg > pos ? 'negative' : 'neutral',
    tags: TAGS.filter(([, re]) => re.test(title)).map(([n]) => n),
  };
}

const normTitle = (s: string) => s.toLowerCase().replace(/\s+-\s+[^-]+$/, '').replace(/[^a-z0-9 ]/g, '').replace(/\s+/g, ' ').trim();

function textOf(v: unknown): string {
  if (typeof v === 'string') return v;
  if (v && typeof v === 'object' && '#text' in (v as Record<string, unknown>)) return String((v as Record<string, unknown>)['#text']);
  return '';
}

export function parseRss(xml: string, source: string): NewsItem[] {
  const parser = new XMLParser({ ignoreAttributes: false, processEntities: true, htmlEntities: true, parseTagValue: false });
  let doc: unknown;
  try { doc = parser.parse(xml); } catch { throw new ProviderError('MALFORMED', `${source}: invalid XML`); }
  const d = doc as { rss?: { channel?: { item?: unknown } }; feed?: { entry?: unknown } };
  const raw = d.rss?.channel?.item ?? d.feed?.entry;
  if (raw === undefined) throw new ProviderError('MALFORMED', `${source}: no items`);
  const list = (Array.isArray(raw) ? raw : [raw]) as Record<string, unknown>[];
  const out: NewsItem[] = [];
  for (const it of list) {
    const title = textOf(it.title).replace(/\s+/g, ' ').trim();
    if (!title) continue;
    const link = textOf(it.link) || textOf((it.link as { '@_href'?: string } | undefined)?.['@_href']);
    const pub = textOf(it.pubDate) || textOf(it.published) || textOf(it.updated);
    const t = pub ? Date.parse(pub) : NaN;
    const srcTag = textOf(it.source);
    const { tone, tags } = classify(title);
    out.push({
      id: normTitle(title).slice(0, 80),
      title,
      link: /^https?:\/\//.test(link) ? link : '',
      source: srcTag || source,
      publishedAt: Number.isNaN(t) ? null : new Date(t).toISOString(),
      tone,
      tags,
    });
  }
  return out;
}

export function mergeNews(batches: NewsItem[][], limit: number, now: Date, maxAgeHours = 72): NewsItem[] {
  const seen = new Set<string>();
  const merged: NewsItem[] = [];
  for (const it of batches.flat()) {
    if (seen.has(it.id)) continue;
    seen.add(it.id);
    if (it.publishedAt && now.getTime() - Date.parse(it.publishedAt) > maxAgeHours * 3600_000) continue;
    merged.push(it);
  }
  return merged
    .sort((a, b) => Date.parse(b.publishedAt ?? '0') - Date.parse(a.publishedAt ?? '0'))
    .slice(0, limit);
}

export async function fetchNews(feeds: Feed[], now: Date, limit = 40, opts: FetchOpts = {}): Promise<{ items: NewsItem[]; failed: { feed: string; reason: string }[] }> {
  const res = await mapLimit(feeds, 4, async (f) => {
    const { text } = await fetchText(f.url, { timeoutMs: 7000, retries: 0, headers: { Accept: 'application/rss+xml, application/xml, text/xml, */*' }, ...opts });
    return parseRss(text, f.label);
  });
  const batches: NewsItem[][] = [];
  const failed: { feed: string; reason: string }[] = [];
  res.forEach((r, i) => {
    if (r.status === 'fulfilled') batches.push(r.value);
    else failed.push({ feed: feeds[i]!.label, reason: r.reason instanceof Error ? r.reason.message : String(r.reason) });
  });
  if (batches.length === 0) throw new ProviderError('NETWORK', `All news feeds failed (${failed.map((f) => f.reason).join('; ')})`);
  return { items: mergeNews(batches, limit, now), failed };
}
