'use client';
import { useEffect, useMemo, useState } from 'react';
import { fetchJson } from '@/lib/api';

type OpsMetrics = { trap_rate: number; top_pairs: { pair_address: string; count: number }[]; top_wallets: { address: string; chain: string; merit_score: number }[] };
type Alert = { id: string; reasons: { netev?: { netev_usd?: number } } };

export default function OutcomesPage() {
  const [metrics, setMetrics] = useState<OpsMetrics | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  useEffect(() => {
    void fetchJson<OpsMetrics>('/ops/metrics').then(setMetrics);
    void fetchJson<Alert[]>('/alerts?limit=100').then(setAlerts);
  }, []);
  const buckets = useMemo(() => {
    const values = alerts.map((a) => Number(a.reasons?.netev?.netev_usd ?? 0)).filter((v) => Number.isFinite(v));
    const edges = [-100, -10, 0, 10, 50, 100, 100000];
    return edges.slice(0, -1).map((start, i) => ({ range: `${start} to ${edges[i + 1]}`, count: values.filter((v) => v >= start && v < edges[i + 1]).length }));
  }, [alerts]);
  return <main className="mx-auto min-h-screen max-w-5xl space-y-6 px-6 py-10"><h1 className="text-3xl font-semibold">Outcomes</h1><p>Trap rate: {metrics ? `${(metrics.trap_rate * 100).toFixed(2)}%` : '...'}</p><section><h2 className="text-xl">Net return distribution (USD est.)</h2><table className="w-full text-left text-sm"><tbody>{buckets.map((b) => <tr key={b.range}><td>{b.range}</td><td>{b.count}</td></tr>)}</tbody></table></section><section><h2 className="text-xl">Top pairs</h2><ul>{metrics?.top_pairs?.map((p) => <li key={p.pair_address}>{p.pair_address}: {p.count}</li>)}</ul></section><section><h2 className="text-xl">Top wallets</h2><ul>{metrics?.top_wallets?.map((w) => <li key={`${w.chain}:${w.address}`}>{w.chain} {w.address} ({w.merit_score.toFixed(3)})</li>)}</ul></section></main>;
}
