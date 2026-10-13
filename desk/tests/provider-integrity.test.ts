import { describe, expect, it, vi } from 'vitest';
import { fetchJson } from '../server/http';
import { parseBreadth, parseParticipantOi } from '../server/providers/nse';

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
