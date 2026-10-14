import type { TrackedRow } from '../../server/services/wb/contracts';
import type { GroupView } from '../../server/services/wb/groupview';
import { fmtIst } from '../../shared/format';
import { useGet } from '../api';
import { go, setQuery, useLoc } from '../nav';
import { Async, Card, Empty, PageHead, Skeleton } from '../ui';
import { CompareTable } from '../wb/CompareTable';
import { NOT_ADVICE } from '../wb/common';

const MAX = 4;

export function Compare() {
  const loc = useLoc();
  const ids = (loc.query.get('ids') ?? '').split(',').map((s) => s.trim()).filter(Boolean).slice(0, MAX);
  const groups = useGet<{ groups: GroupView[]; ungrouped: TrackedRow[] }>('/api/workbench/groups');
  const q = useGet<{ rows: TrackedRow[]; asOf: string; notes: string[] }>(ids.length ? `/api/workbench/compare?ids=${encodeURIComponent(ids.join(','))}` : null, { poll: 30_000 });

  const set = (next: string[]) => setQuery('ids', next.length ? next.join(',') : null);
  const all: { id: string; label: string; group: string }[] = [
    ...(groups.data?.data.groups ?? []).flatMap((g) => g.items.map((x) => ({ id: x.item.id, label: x.row.label, group: g.name }))),
    ...(groups.data?.data.ungrouped ?? []).map((r) => ({ id: r.item.id, label: r.label, group: 'Ungrouped' })),
  ];

  return (
    <div className="page">
      <PageHead title="Compare contracts" sub="Up to four exact contracts side by side. Nothing is assumed equal: each column shows its own quote, liquidity and data quality." right={<button className="btn" onClick={() => go('/workbench')}>Add a contract</button>} />
      <Card title="Choose contracts">
        {groups.isPending ? <Skeleton h={40} /> : all.length === 0 ? (
          <Empty>No contracts to compare yet. Add contracts from the Options Workbench, then return here.</Empty>
        ) : (
          <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
            <legend className="small muted">Tick up to {MAX}</legend>
            <div className="row" style={{ gap: 14 }}>
              {all.map((c) => (
                <label key={c.id} className="row small" style={{ gap: 6 }}>
                  <input type="checkbox" style={{ width: 'auto' }} checked={ids.includes(c.id)} disabled={!ids.includes(c.id) && ids.length >= MAX} onChange={() => set(ids.includes(c.id) ? ids.filter((x) => x !== c.id) : [...ids, c.id])} />
                  {c.label} <span className="muted">({c.group})</span>
                </label>
              ))}
            </div>
          </fieldset>
        )}
      </Card>
      {ids.length === 0 && all.length > 0 && <Empty>Tick two or more contracts to compare them.</Empty>}
      {ids.length > 0 && (
        <Async q={q} skeleton={<Card><Skeleton h={320} /></Card>}>
          {(d) => (
            <Card title={`Comparison · as of ${fmtIst(d.asOf)}`}>
              <CompareTable rows={d.rows} />
              {d.notes.length > 0 && <div className="small muted" style={{ marginTop: 6 }}>{d.notes.join(' ')}</div>}
              <div className="small muted" style={{ marginTop: 6 }}>{NOT_ADVICE}</div>
            </Card>
          )}
        </Async>
      )}
    </div>
  );
}
