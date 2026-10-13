import { istDateStr } from '../../shared/india';
import { summarizePortfolio, viewHolding, type HoldingView, type PortfolioSummary } from '../../shared/portfolio';
import type { Ctx } from '../config';
import { equityTarget, getQuotes } from './quotes';
import type { Svc } from './market';
import { getSectorMap } from './symbols';
import { listHoldings } from './userdata';

export interface PortfolioData {
  holdings: HoldingView[];
  summary: PortfolioSummary;
}

export async function getPortfolio(ctx: Ctx): Promise<Svc<PortfolioData>> {
  const holdings = await listHoldings(ctx);
  const notes: string[] = [];
  const symbols = [...new Set(holdings.map((h) => h.symbol))];

  const [qr, sectors] = await Promise.all([
    symbols.length > 0
      ? getQuotes(ctx, 'holdings', symbols.map(equityTarget), 20, 900).catch((e: Error) => {
          notes.push(`Prices unavailable: ${e.message}. Holdings are shown at cost.`);
          return null;
        })
      : Promise.resolve(null),
    getSectorMap(ctx),
  ]);
  if (qr) notes.push(...qr.notes);

  const today = istDateStr(ctx.now());
  const views = holdings.map((h) => viewHolding(h, qr?.quotes[h.symbol], sectors[h.symbol], today));
  const summary = summarizePortfolio(views);
  return {
    data: { holdings: views, summary },
    source: qr?.source ?? 'none',
    stale: Boolean(qr?.stale),
    notes: [...new Set(notes)],
  };
}
