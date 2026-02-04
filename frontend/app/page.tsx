import { Button } from '@/components/ui/button';

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-4xl flex-col gap-6 px-6 py-16">
      <div className="space-y-4">
        <p className="text-sm uppercase tracking-[0.3em] text-slate-400">Project Titan v6.0</p>
        <h1 className="text-4xl font-semibold">Local-first token scoring cockpit.</h1>
        <p className="text-slate-300">
          Deterministic scoring, aggressive caching, and structured reasons designed for
          transparent risk assessments.
        </p>
      </div>
      <div className="grid gap-4 rounded-2xl bg-muted p-6">
        <div>
          <h2 className="text-lg font-semibold">Status</h2>
          <p className="text-sm text-slate-300">API ready · Worker ready · Ollama optional</p>
        </div>
        <div className="flex flex-wrap gap-3">
          <Button>Run a score</Button>
          <Button className="bg-transparent text-foreground ring-1 ring-slate-600 hover:bg-slate-800">
            View documentation
          </Button>
        </div>
      </div>
    </main>
  );
}
