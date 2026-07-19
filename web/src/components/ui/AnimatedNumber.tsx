import { animate, motion, useAnimationControls, useMotionValue, useReducedMotion } from "framer-motion";
import { useEffect, useRef, useState } from "react";

const UP_TINT = "rgba(22, 199, 132, 0.28)"; // green flash
const DOWN_TINT = "rgba(234, 57, 67, 0.28)"; // red flash

/**
 * Smoothly interpolates between values so a polled metric "ticks" instead of jumping.
 * With `flash`, a real change also briefly pulses the background green (up) / red
 * (down) — the live trading-terminal cue. First mount + reduced-motion never flash.
 */
export default function AnimatedNumber({
  value,
  format,
  duration = 0.8,
  flash = false,
  flashInvert = false,
}: {
  value: number;
  format: (n: number) => string;
  duration?: number;
  flash?: boolean;
  /** For risk metrics where DOWN is good (e.g. drawdown): swap the flash so a rise pulses red. */
  flashInvert?: boolean;
}) {
  const reduce = useReducedMotion();
  const mv = useMotionValue(value);
  const [display, setDisplay] = useState(value);
  const controls = useAnimationControls();
  const prev = useRef(value);

  useEffect(() => {
    const prevVal = prev.current;
    const changed = value !== prevVal;
    prev.current = value;

    if (reduce) {
      setDisplay(value);
    } else {
      const controlsNum = animate(mv, value, {
        duration,
        ease: "easeOut",
        onUpdate: (v) => setDisplay(v),
      });
      // Flash only on a genuine change (not first mount), direction-coloured.
      // `flashInvert` flips it for risk metrics where a rise is bad (drawdown).
      if (flash && changed) {
        const good = flashInvert ? value < prevVal : value > prevVal;
        void controls.start({
          backgroundColor: [good ? UP_TINT : DOWN_TINT, "rgba(0,0,0,0)"],
          transition: { duration: 0.75, ease: "easeOut" },
        });
      }
      return controlsNum.stop;
    }
  }, [value, duration, mv, reduce, flash, flashInvert, controls]);

  if (!flash) return <>{format(display)}</>;
  return (
    <motion.span animate={controls} className="-mx-0.5 rounded-sm px-0.5">
      {format(display)}
    </motion.span>
  );
}
