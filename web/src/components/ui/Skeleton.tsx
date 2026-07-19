/** Shimmer placeholder that mirrors the dashboard layout, so first load / Render
 * cold-start reads as "loading", not "broken". Pure presentational. */

function Block({ className = "" }: { className?: string }) {
  return <div className={`skeleton ${className}`} />;
}

function CardSkel({ className = "", lines = 3 }: { className?: string; lines?: number }) {
  return (
    <div className={`glow-card p-4 ${className}`}>
      <Block className="mb-4 h-3 w-28" />
      <div className="space-y-2.5">
        {Array.from({ length: lines }).map((_, i) => (
          <Block key={i} className="h-3" />
        ))}
      </div>
    </div>
  );
}

export default function DashboardSkeleton({ message }: { message?: string }) {
  return (
    <div className="mx-auto max-w-[1500px] space-y-6 p-4 md:p-6" aria-busy="true" aria-label="loading dashboard">
      {/* status strip */}
      <div className="glow-card flex items-center gap-3 px-4 py-3">
        <Block className="h-2.5 w-2.5 rounded-full" />
        <Block className="h-3 w-56" />
        <span className="ml-auto text-[11px] text-muted">{message ?? "connecting to agent…"}</span>
      </div>

      {/* hero 3-up */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <div key={i} className="glow-card p-5 md:p-6">
            <Block className="mb-3 h-3 w-24" />
            <Block className="mb-3 h-9 w-40" />
            <Block className="h-3 w-32" />
          </div>
        ))}
      </div>

      {/* tier-B grid */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <div className="lg:col-span-8">
          <div className="glow-card p-4">
            <Block className="mb-4 h-3 w-28" />
            <Block className="h-[180px] w-full" />
          </div>
        </div>
        <div className="lg:col-span-4">
          <CardSkel lines={4} />
        </div>
        <div className="lg:col-span-7">
          <CardSkel lines={3} />
        </div>
        <div className="lg:col-span-5">
          <CardSkel lines={3} />
        </div>
      </div>
    </div>
  );
}
