import { useState } from "react";

import type { UseAllocator } from "../hooks/useAllocator";
import Card from "./ui/Card";
import { useToast } from "./ui/Toast";

const OFFLINE_MSG = "controls need the live API — connect the Render backend";

export default function ControlPanel({ allocator }: { allocator: UseAllocator }) {
  const { data, busy, live, toggleKill } = allocator;
  const { toast } = useToast();
  const [msg, setMsg] = useState<string | null>(null);
  const killed = data?.health.kill_switch_engaged ?? false;

  async function onKill() {
    setMsg(null);
    try {
      const r = await toggleKill(!killed);
      setMsg(r.message);
      if (r.engaged) toast.warn(r.message, { title: "Kill switch" });
      else toast.success(r.message, { title: "Kill switch" });
    } catch {
      setMsg(OFFLINE_MSG);
      toast.error(OFFLINE_MSG, { key: "ctrl-offline", title: "Offline" });
    }
  }

  return (
    <Card label="Controls" accent="#7C5CFF" className="flex h-full flex-col">
      {!live && (
        <div className="mb-3 rounded-sm border border-amber/40 bg-amber/10 px-2 py-1 text-[11px] text-amber">
          cached snapshot — reconnecting to the live API
        </div>
      )}
      <div className="flex flex-col gap-2">
        <button
          onClick={onKill}
          disabled={busy}
          aria-busy={busy}
          className={`rounded-sm border-3 px-3 py-2 font-display text-sm font-bold shadow-brut-sm transition focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/60 disabled:opacity-40 ${
            killed
              ? "border-neon/50 bg-neon/10 text-neon hover:bg-neon/20"
              : "border-danger/50 bg-danger/10 text-danger hover:bg-danger/20"
          }`}
        >
          {killed ? "✓ Release kill switch" : "■ Engage kill switch"}
        </button>
      </div>
      <p className="mt-3 text-[11px] leading-relaxed text-muted">
        {msg ?? "The kill switch halts evaluations and flattens the book to cash — the only state-changing control here."}
      </p>
    </Card>
  );
}
