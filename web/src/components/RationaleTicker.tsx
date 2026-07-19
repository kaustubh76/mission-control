import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useState } from "react";

import type { RationaleFeed } from "../api/types";
import { clockHM } from "../lib/format";
import Card from "./ui/Card";

const CLAMP_AT = 140; // chars — entries longer than this start collapsed

export default function RationaleTicker({ feed }: { feed: RationaleFeed }) {
  const items = feed.items;
  const reduce = useReducedMotion();
  const [open, setOpen] = useState<string | null>(null);

  return (
    <Card
      label="Agent Rationale — what it's thinking"
      accent="#16c784"
      className="flex h-full flex-col"
      right={
        items[0]?.ts ? <span className="text-[11px] text-muted">as of {clockHM(items[0].ts)}</span> : undefined
      }
    >
      {items.length === 0 ? (
        <div className="flex h-24 items-center justify-center text-xs text-muted">
          the agent hasn't posted a rationale yet — waiting for the next live rebalance
        </div>
      ) : (
        <ul className="max-h-[230px] space-y-2 overflow-y-auto pr-1">
          <AnimatePresence initial={false}>
            {items.map((it, i) => {
              const k = `${it.ts}-${i}`;
              const long = it.rationale.length > CLAMP_AT;
              const expanded = open === k || !long;
              return (
                <motion.li
                  key={k}
                  initial={reduce ? false : { opacity: 0, x: -12 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: 0.35 }}
                  className={`rounded-sm border-l-2 bg-panel2/60 text-xs leading-relaxed ${
                    i === 0 ? "border-neon text-ink" : "border-edge text-sub"
                  }`}
                >
                  <button
                    onClick={() => long && setOpen((cur) => (cur === k ? null : k))}
                    aria-expanded={expanded}
                    disabled={!long}
                    className={`block w-full px-3 py-2 text-left ${long ? "cursor-pointer" : "cursor-default"}`}
                  >
                    <span className="mr-2 font-mono text-[10px] text-muted">{clockHM(it.ts)}</span>
                    {i === 0 && <span className="mr-1 text-neon">▌</span>}
                    {expanded ? it.rationale : `${it.rationale.slice(0, CLAMP_AT).trimEnd()}…`}
                    {long && (
                      <span className="ml-1.5 font-mono text-[10px] text-cyan">
                        {expanded ? "less ▴" : "more ▾"}
                      </span>
                    )}
                  </button>
                </motion.li>
              );
            })}
          </AnimatePresence>
        </ul>
      )}
    </Card>
  );
}
