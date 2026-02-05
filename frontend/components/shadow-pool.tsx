'use client';

import { useCallback, useEffect, useState } from 'react';

import { fetchJson } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';

interface WalletSummary {
  chain: string;
  address: string;
  total_value: number | null;
  pnl: number | null;
  tier: string;
  updated_at: string;
}

const tiers = ['ocean', 'shadow', 'titan', 'ignore'] as const;

const formatCurrency = (value: number | null) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return '--';
  }
  return `$${value.toFixed(2)}`;
};

const formatDate = (value: string) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
};

const ShadowPool = () => {
  const [selectedTier, setSelectedTier] = useState<(typeof tiers)[number]>('ocean');
  const [wallets, setWallets] = useState<WalletSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadWallets = useCallback(async (tier: string, signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchJson<WalletSummary[]>(`/wallets?tier=${tier}`, {
        signal
      });
      setWallets(data);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unable to load wallets.';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void loadWallets(selectedTier, controller.signal);
    return () => controller.abort();
  }, [loadWallets, selectedTier]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Shadow Pool</CardTitle>
        <CardDescription>Wallets grouped by tier with last known metrics.</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="mb-4 flex flex-wrap gap-2">
          {tiers.map((tier) => (
            <Button
              key={tier}
              type="button"
              className={
                tier === selectedTier
                  ? 'bg-primary text-white'
                  : 'bg-slate-900 text-slate-200 ring-1 ring-slate-700 hover:bg-slate-800'
              }
              onClick={() => setSelectedTier(tier)}
            >
              {tier}
            </Button>
          ))}
        </div>

        {loading && <p className="text-sm text-slate-400">Loading wallets…</p>}
        {error && <p className="text-sm text-rose-400">{error}</p>}
        {!loading && !error && wallets.length === 0 && (
          <p className="text-sm text-slate-400">No wallets found for this tier.</p>
        )}
        {!loading && !error && wallets.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Wallet</TableHead>
                <TableHead>Chain</TableHead>
                <TableHead>Total Value</TableHead>
                <TableHead>PnL</TableHead>
                <TableHead>Tier</TableHead>
                <TableHead>Updated</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {wallets.map((wallet) => (
                <TableRow key={`${wallet.chain}-${wallet.address}`}>
                  <TableCell className="font-mono text-xs text-slate-300">
                    {wallet.address}
                  </TableCell>
                  <TableCell className="capitalize">{wallet.chain}</TableCell>
                  <TableCell>{formatCurrency(wallet.total_value)}</TableCell>
                  <TableCell>{formatCurrency(wallet.pnl)}</TableCell>
                  <TableCell>
                    <Badge className="capitalize">{wallet.tier}</Badge>
                  </TableCell>
                  <TableCell className="whitespace-nowrap">
                    {formatDate(wallet.updated_at)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
};

export default ShadowPool;
