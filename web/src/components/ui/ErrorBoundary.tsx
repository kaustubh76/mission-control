import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** Short label so the fallback can name which panel failed. */
  label?: string;
  /** Optional custom fallback; defaults to a compact inline notice. */
  fallback?: ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * Isolates a render failure to a single panel so one bad payload (e.g. a live
 * snapshot with an unexpected null) can never white-screen the whole dashboard.
 * The rest of Mission Control keeps rendering and polling; the failed panel
 * shows a small notice instead. React error boundaries must be class components.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Surface for debugging without taking the app down.
    console.error(
      `[ErrorBoundary${this.props.label ? ` · ${this.props.label}` : ""}]`,
      error,
      info.componentStack,
    );
  }

  render(): ReactNode {
    if (this.state.error) {
      if (this.props.fallback !== undefined) return this.props.fallback;
      return (
        <div className="flex h-full min-h-[6rem] flex-col items-center justify-center gap-1 rounded-md border border-edge bg-panel p-4 text-center">
          <span className="font-mono text-xs text-amber">
            {this.props.label ?? "panel"} hit a snag
          </span>
          <span className="text-[10px] leading-snug text-muted opacity-70">
            the rest of the dashboard is still live
          </span>
        </div>
      );
    }
    return this.props.children;
  }
}
