'use client';
import { useEffect, useState } from 'react';
import { fetchJson } from '@/lib/api';

type TuningResponse = { source: string; warning: string | null; thresholds: Record<string, number> };
type PreviewResponse = { total_considered: number; would_trigger: number };

export default function TuningPage() {
  const [thresholds, setThresholds] = useState<Record<string, number>>({ min_conviction: 45, min_tss: 35, min_netev_usd: 0 });
  const [warning, setWarning] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  useEffect(() => { void fetchJson<TuningResponse>('/ops/tuning').then((r) => { setThresholds(r.thresholds); setWarning(r.warning); }); }, []);
  const save = async () => { await fetchJson<TuningResponse>('/ops/tuning', { method: 'PUT', body: JSON.stringify({ thresholds }) }); };
  const runPreview = async () => { const result = await fetchJson<PreviewResponse>('/ops/tuning/preview', { method: 'POST', body: JSON.stringify({ thresholds }) }); setPreview(result); };
  return <main className="mx-auto min-h-screen max-w-4xl space-y-6 px-6 py-10"><h1 className="text-3xl font-semibold">Tuning</h1>{warning && <p className="text-amber-400">{warning}</p>}{Object.entries(thresholds).map(([key, value]) => <label key={key} className="block"><span>{key}</span><input className="ml-3 rounded border px-2 py-1 text-black" type="number" value={value} onChange={(e) => setThresholds((old) => ({ ...old, [key]: Number(e.target.value) }))} /></label>)}<div className="space-x-3"><button className="rounded bg-blue-500 px-4 py-2" onClick={save}>Save</button><button className="rounded bg-slate-600 px-4 py-2" onClick={runPreview}>Dry-run preview</button></div>{preview && <p>Would trigger: {preview.would_trigger} / {preview.total_considered} (last 50 alerts)</p>}</main>;
}
