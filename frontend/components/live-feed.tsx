'use client';

import { useCallback, useEffect, useState } from 'react';

import { fetchJson } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';

interface AlertItem {
  id: string;
  chain: string;
  token_address: string | null;
  reasons: Record<string, unknown>;
  narrative: string | null;
  created_at: string;
}

interface RegimeResponse {
  regime: string;
  updated_at: string | null;
}

const formatNumber = (value: unknown) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return '--';
  }
  return Number(value).toFixed(2);
};

const formatDate = (value: string) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
};

const LiveFeed = () => {
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [regime, setRegime] = useState<RegimeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadAlerts = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);

    try {
      const [alertsData, regimeData] = await Promise.all([
        fetchJson<AlertItem[]>(`/alerts?limit=15&offset=0`, {
          signal
        }),
        fetchJson<RegimeResponse>('/regime', { signal })
      ]);
      setAlerts(alertsData);
      setRegime(regimeData);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unable to load alerts.';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void loadAlerts(controller.signal);
    return () => controller.abort();
  }, [loadAlerts]);

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle>Live Feed</CardTitle>
            <CardDescription>Latest conviction alerts and market regime snapshot.</CardDescription>
          </div>
          <Button
            type="button"
            className="bg-slate-900 text-slate-200 ring-1 ring-slate-700 hover:bg-slate-800"
            onClick={() => void loadAlerts()}
          >
            Refresh
          </Button>
        </div>
        {regime && (
          <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-slate-300">
            <Badge>Regime: {regime.regime}</Badge>
            {regime.updated_at && <span>Updated {formatDate(regime.updated_at)}</span>}
          </div>
        )}
      </CardHeader>
      <CardContent>
        {loading && <p className="text-sm text-slate-400">Loading alerts…</p>}
        {error && <p className="text-sm text-rose-400">{error}</p>}
        {!loading && !error && alerts.length === 0 && (
          <p className="text-sm text-slate-400">No alerts yet. Run the workers to seed activity.</p>
        )}
        {!loading && !error && alerts.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Time</TableHead>
                <TableHead>Chain</TableHead>
                <TableHead>Token</TableHead>
                <TableHead>TSS</TableHead>
                <TableHead>Conviction</TableHead>
                <TableHead>Regime</TableHead>
                <TableHead>Narrative</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {alerts.map((alert) => {
                const reasons = alert.reasons ?? {};
                const tss = (reasons as { tss?: number }).tss;
                const conviction = (reasons as { conviction?: number }).conviction;
                return (
                  <TableRow key={alert.id}>
                    <TableCell className="whitespace-nowrap">{formatDate(alert.created_at)}</TableCell>
                    <TableCell className="capitalize">{alert.chain}</TableCell>
                    <TableCell className="font-mono text-xs text-slate-300">
                      {alert.token_address ?? '--'}
                    </TableCell>
                    <TableCell>{formatNumber(tss)}</TableCell>
                    <TableCell>{formatNumber(conviction)}</TableCell>
                    <TableCell>{regime?.regime ?? '--'}</TableCell>
                    <TableCell className="max-w-xs text-slate-300">
                      {alert.narrative ?? '—'}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
};

export default LiveFeed;
