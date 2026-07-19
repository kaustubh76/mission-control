import MissionControl from "./components/MissionControl";
import { useAllocator } from "./hooks/useAllocator";
import { useAwayDigest } from "./hooks/useAwayDigest";
import { useFunctionalAlerts } from "./hooks/useFunctionalAlerts";

export default function App() {
  const allocator = useAllocator(4000);
  useFunctionalAlerts(allocator); // pop-up warnings on connectivity / risk / state transitions
  useAwayDigest(allocator); // "while you were away" summary on tab refocus
  return <MissionControl allocator={allocator} />;
}
