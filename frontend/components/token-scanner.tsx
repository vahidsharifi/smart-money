'use client';

import { FormEvent, useState } from 'react';

import { fetchJson } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';

interface TokenRisk {
  chain: string;
  address: string;
  score: number | null;
  components: Record<string, unknown>;
  updated_at: string;
}

interface AlertItem {
  id: string;
  chain: string;
  token_address: string | null;
  reasons: Record<string, unknown>;
  narrative: string | null;
  created_at: string;
}

const formatDate = (value: string) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
};

const TokenScanner = () => {
  const [chain, setChain] = useState('ethereum');
  const [address, setAddress] = useState('');
  const [risk, setRisk] = useState<TokenRisk | null>(null);
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!address.trim()) {
      setError('Enter a token address to scan.');
      return;
    }
    setLoading(true);
    setError(null);
    setRisk(null);
    setAlerts([]);

    try {
      const tokenRisk = await fetchJson<TokenRisk>(
        `/tokens/${chain}/${address.toLowerCase()}/risk`
      );
      setRisk(tokenRisk);

      const alertData = await fetchJson<AlertItem[]>(
        `/alerts?limit=50&offset=0&chain=${chain}`
      );
      const filtered = alertData.filter(
        (alert) => alert.token_address?.toLowerCase() === address.toLowerCase()
      );
      setAlerts(filtered.slice(0, 5));
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unable to scan token.';
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Token Scanner</CardTitle>
        <CardDescription>Look up risk breakdown and recent alerts for any token.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="grid gap-4 md:grid-cols-[160px_1fr_auto]">
          <div className="space-y-1">
            <label className="text-xs uppercase text-slate-400">Chain</label>
            <Input value={chain} onChange={(event) => setChain(event.target.value)} />
          </div>
          <div className="space-y-1">
            <label className="text-xs uppercase text-slate-400">Token address</label>
            <Input
              value={address}
              onChange={(event) => setAddress(event.target.value)}
              placeholder="0x..."
            />
          </div>
          <div className="flex items-end">
            <Button type="submit" disabled={loading}>
              {loading ? 'Scanning…' : 'Scan'}
            </Button>
          </div>
        </form>

        {error && <p className="mt-4 text-sm text-rose-400">{error}</p>}

        {risk && (
          <div className="mt-6 space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <Badge className="uppercase">{risk.chain}</Badge>
              <span className="font-mono text-xs text-slate-300">{risk.address}</span>
              <Badge className="bg-emerald-600/20 text-emerald-200">
                Score: {risk.score ?? '--'}
              </Badge>
              <span className="text-xs text-slate-400">Updated {formatDate(risk.updated_at)}</span>
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              {Object.keys(risk.components ?? {}).length === 0 && (
                <p className="text-sm text-slate-400">No component breakdown available.</p>
              )}
              {Object.entries(risk.components ?? {}).map(([key, value]) => (
                <div key={key} className="rounded-xl border border-slate-800 bg-slate-950 p-4">
                  <p className="text-sm font-semibold capitalize text-slate-200">{key}</p>
                  <pre className="mt-2 whitespace-pre-wrap text-xs text-slate-400">
                    {JSON.stringify(value, null, 2)}
                  </pre>
                </div>
              ))}
            </div>

            <div>
              <h4 className="text-sm font-semibold text-slate-200">Latest alerts</h4>
              {alerts.length === 0 ? (
                <p className="mt-2 text-sm text-slate-400">No alerts found for this token.</p>
              ) : (
                <ul className="mt-2 space-y-2">
                  {alerts.map((alert) => (
                    <li key={alert.id} className="rounded-xl border border-slate-800 bg-slate-950 p-3">
                      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
                        <span>{formatDate(alert.created_at)}</span>
                        <span className="uppercase">{alert.chain}</span>
                      </div>
                      <p className="mt-1 text-sm text-slate-200">
                        {alert.narrative ?? 'Narrative pending'}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export default TokenScanner;
