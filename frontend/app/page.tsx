import LiveFeed from '@/components/live-feed';
import ShadowPool from '@/components/shadow-pool';
import TokenScanner from '@/components/token-scanner';

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-6xl flex-col gap-8 px-6 py-12">
      <header className="space-y-3">
        <p className="text-xs uppercase tracking-[0.4em] text-slate-400">Project Titan v6.0</p>
        <h1 className="text-4xl font-semibold text-slate-100">Market Intelligence Dashboard</h1>
        <p className="max-w-2xl text-sm text-slate-300">
          Monitor conviction alerts, shadow-tier wallets, and token risk breakdowns from the Titan backend. No
          authentication required.
        </p>
      </header>

      <section className="grid gap-6">
        <LiveFeed />
      </section>

      <section className="grid gap-6 lg:grid-cols-2">
        <ShadowPool />
        <TokenScanner />
      </section>
    </main>
  );
}
