import type { TokenRotation } from "../api/types";
import { TokenRotationBody } from "./TokenRotationCard";
import Card from "./ui/Card";
import FreshnessPill from "./ui/FreshnessPill";
import InfoTip from "./ui/Tooltip";

/**
 * Token Rotation coverage. The full-width rotation-coverage grid proves which of the 8 contest tokens
 * have actually been touched this week (momentum top-2 + contest-floor rotation on the rest).
 */
export default function TokenUniversePanel({
  rotation,
  live = true,
  lastTickTs,
}: {
  rotation: TokenRotation | null;
  live?: boolean;
  lastTickTs?: string | null;
}) {
  const right = (
    <span className="flex items-center gap-1.5">
      <InfoTip
        title="Token rotation"
        text="Which of the 8 tokens have been traded. The momentum arm holds only the top-2; the floor rotation touches the rest with ~0-NAV round-trips. Coverage, not an edge claim."
      />
      <FreshnessPill live={live} lastTickTs={lastTickTs} srLive="live rotation" />
    </span>
  );

  return (
    <Card label="Token Universe · Rotation" accent="#7C5CFF" right={right}>
      <TokenRotationBody rotation={rotation} />
    </Card>
  );
}
