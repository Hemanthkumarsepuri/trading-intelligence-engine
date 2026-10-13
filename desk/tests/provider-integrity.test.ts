import { describe, expect, it, vi } from 'vitest';
import { fetchJson } from '../server/http';
import { fetchNseChainRaw, fetchNseExpiriesRaw, nseExpiries, nseExpiryParam, parseBreadth, parseNseChain, parseParticipantOi } from '../server/providers/nse';

const HDR = 'Client Type,Future Index Long,Future Index Short,Future Stock Long,Future Stock Short,Option Index Call Long,Option Index Put Long,Option Index Call Short,Option Index Put Short';
const csv = (fii: string) => `TRADING DATE,01-10-2026\n${HDR}\nFII,${fii}\nClient,1,2,3,4,5,6,7,8`;

describe('provider parsers never turn missing data into zero', () => {
  it('reads a complete participant-OI row (including a genuine zero and thousands separators)', () => {
    const f = parseParticipantOi(csv('"1,234",0,5,6,7,8,9,10'), '2026-10-01').participants.FII;
    expect(f.indexFutLong).toBe(1234);
    expect(f.indexFutShort).toBe(0);
  });
  it('rejects a blank or non-numeric participant-OI cell as MALFORMED', () => {
    expect(() => parseParticipantOi(csv('abc,1,5,6,7,8,9,10'), 'd')).toThrow(/non-numeric/);
    try {
      parseParticipantOi(csv('abc,1,5,6,7,8,9,10'), 'd');
      expect.unreachable('should have thrown');
    } catch (e) {
      expect(e).toMatchObject({ name: 'ProviderError', kind: 'MALFORMED' });
    }
    expect(() => parseParticipantOi(csv('1,,5,6,7,8,9,10'), 'd')).toThrow(/non-numeric/);
    expect(() => parseParticipantOi(csv('1,2,5,6,7,8,9'), 'd')).toThrow(/non-numeric/); // short row
  });
  it('reads breadth counts, including strings with separators and a real zero', () => {
    expect(parseBreadth({ data: [{ index: 'NIFTY 500', advances: '1,020', declines: 0, unchanged: '12' }] })).toEqual({ advances: 1020, declines: 0, unchanged: 12 });
  });
  it('rejects missing or garbage breadth counts instead of reporting 0 advances / 0 declines', () => {
    expect(() => parseBreadth({ data: [{ index: 'NIFTY 500', advances: 'n/a', declines: 5, unchanged: 1 }] })).toThrow(/MALFORMED|non-numeric/i);
    expect(() => parseBreadth({ data: [{ index: 'NIFTY 500', declines: 5, unchanged: 1 }] })).toThrow();
    expect(() => parseBreadth({ data: [] })).toThrow(/not found/);
  });
  it('rejects a breadth row where only "unchanged" is missing or NaN, as a MALFORMED provider error', () => {
    expect(() => parseBreadth({ data: [{ index: 'NIFTY 500', advances: 5, declines: 5 }] })).toThrow(/non-numeric/);
    try {
      parseBreadth({ data: [{ index: 'NIFTY 500', advances: 5, declines: NaN, unchanged: 1 }] });
      expect.unreachable('should have thrown');
    } catch (e) {
      expect(e).toMatchObject({ name: 'ProviderError', kind: 'MALFORMED' });
    }
  });
});

describe('upstream error bodies are not echoed to callers', () => {
  it('keeps the response excerpt out of the error message (host only)', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const secretish = '<html>internal-hostname.corp.example token=abc123</html>';
    const fetchImpl = (async () => new Response(secretish, { status: 200 })) as unknown as typeof fetch;
    const err = await fetchJson('https://api.example.com/x', { fetchImpl, retries: 0 }).catch((e: Error) => e);
    expect(err).toMatchObject({ kind: 'MALFORMED' });
    expect((err as Error).message).toBe('Non-JSON response from api.example.com');
    expect((err as Error).message).not.toContain('internal-hostname');
    vi.restoreAllMocks();
  });
});

describe('NSE option chain uses the v3 API (option-chain-indices was retired)', () => {
  const nseFetch = (routes: Record<string, () => Response>) => {
    const seen: string[] = [];
    const impl = (async (input: string | URL | Request) => {
      const url = String(input instanceof Request ? input.url : input);
      seen.push(url);
      if (url === 'https://www.nseindia.com/') return new Response('<html></html>', { status: 200, headers: { 'set-cookie': 'nsit=t; Path=/' } });
      const hit = Object.entries(routes).find(([p]) => url.includes(p));
      return hit ? hit[1]() : new Response('<html>Not found</html>', { status: 404, headers: { 'content-type': 'text/html' } });
    }) as typeof fetch;
    return { impl, seen };
  };

  it('requests contract-info for expiries and option-chain-v3 per expiry, never the retired endpoint', async () => {
    const { impl, seen } = nseFetch({
      '/api/option-chain-contract-info?symbol=NIFTY': () => Response.json({ expiryDates: ['27-Oct-2026', '13-Oct-2026'], strikePrice: [] }),
      '/api/option-chain-v3?type=Indices&symbol=NIFTY&expiry=27-Oct-2026': () => Response.json({ records: { data: [], underlyingValue: 1 } }),
    });
    expect(nseExpiries(await fetchNseExpiriesRaw('NIFTY', { fetchImpl: impl }))).toEqual(['2026-10-13', '2026-10-27']);
    await fetchNseChainRaw('NIFTY', '2026-10-27', { fetchImpl: impl });
    expect(seen.some((u) => u.includes('/api/option-chain-v3?type=Indices&symbol=NIFTY&expiry=27-Oct-2026'))).toBe(true);
    expect(seen.some((u) => u.includes('option-chain-indices'))).toBe(false);
  });

  it('a 404 HTML answer is a provider error, never an empty chain', async () => {
    const { impl } = nseFetch({});
    await expect(fetchNseChainRaw('NIFTY', '2026-10-27', { fetchImpl: impl })).rejects.toMatchObject({ name: 'ProviderError' });
  });

  it('parses v3 rows: per-row expiryDates, buyPrice1/sellPrice1 as bid/ask, other expiries skipped, missing stays null', () => {
    const c = parseNseChain(
      {
        records: {
          underlyingValue: 25010,
          data: [
            { strikePrice: 25000, expiryDates: '27-Oct-2026', CE: { lastPrice: 120, openInterest: 500, changeinOpenInterest: 50, totalTradedVolume: 0, impliedVolatility: 12, buyPrice1: 119, sellPrice1: 121 }, PE: { lastPrice: 95 } },
            { strikePrice: 25000, expiryDates: '03-Nov-2026', CE: { lastPrice: 999 }, PE: { lastPrice: 999 } },
          ],
        },
      },
      'NIFTY',
      '2026-10-27',
      't',
    );
    expect(c.rows).toHaveLength(1);
    expect(c.spot).toBe(25010);
    expect(c.rows[0]!.ce).toEqual({ ltp: 120, oi: 500, prevOi: 450, volume: 0, iv: 12, bid: 119, ask: 121 });
    expect(c.rows[0]!.pe).toEqual({ ltp: 95, oi: null, prevOi: null, volume: null, iv: null, bid: null, ask: null });
  });

  it('a v3 body with no rows for the requested expiry is MALFORMED, not an empty chain', () => {
    expect(() => parseNseChain({ records: { data: [{ strikePrice: 1, expiryDates: '03-Nov-2026' }] } }, 'NIFTY', '2026-10-27', 't')).toThrow(/no rows for expiry/);
  });

  it('formats the expiry parameter and rejects a malformed expiry', () => {
    expect(nseExpiryParam('2026-10-27')).toBe('27-Oct-2026');
    expect(() => nseExpiryParam('2026-13-01')).toThrow(/bad expiry/);
    expect(() => nseExpiryParam('27-Oct-2026')).toThrow(/bad expiry/);
  });
});
