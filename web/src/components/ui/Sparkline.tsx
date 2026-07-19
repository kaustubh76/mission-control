import { Line, LineChart, ResponsiveContainer, YAxis } from "recharts";

interface SparklineProps {
  data: number[];
  color?: string;
  height?: number;
  className?: string;
  /** Extend the Y domain to include this value (e.g. 0 for drawdown) so a short,
   * flat-ish series reads as a curve off a fixed floor instead of a bare full-height diagonal. */
  baseline?: number;
  /** Pad the Y domain by this fraction of the range above & below (default 0 = current behavior). */
  padFrac?: number;
}

/** Minimal inline trend line (no axes / dots) — used in the hero NAV stat & the risk drawdown trend. */
export default function Sparkline({
  data,
  color = "#16c784",
  height = 28,
  className = "",
  baseline,
  padFrac = 0,
}: SparklineProps) {
  if (data.length < 2) return null;
  const rows = data.map((v, i) => ({ i, v }));
  let lo = Math.min(...data);
  let hi = Math.max(...data);
  if (baseline != null) {
    lo = Math.min(lo, baseline);
    hi = Math.max(hi, baseline);
  }
  // Guard a flat series (lo === hi) so padding never collapses the domain to a single point.
  const span = hi - lo || Math.abs(hi) || 1;
  const pad = span * padFrac;
  return (
    <div className={className} style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 2, right: 0, bottom: 2, left: 0 }}>
          <YAxis domain={[lo - pad, hi + pad]} hide />
          <Line type="monotone" dataKey="v" stroke={color} strokeWidth={2} dot={false} isAnimationActive animationDuration={400} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
