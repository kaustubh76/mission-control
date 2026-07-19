import React from "react";
import ReactDOM from "react-dom/client";

import App from "./App";
import ErrorBoundary from "./components/ui/ErrorBoundary";
import { ToastProvider } from "./components/ui/Toast";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary
      label="Dashboard"
      fallback={
        <div className="flex min-h-screen items-center justify-center p-6 text-center text-sm text-muted">
          The dashboard hit an unexpected error. Reload to reconnect to the agent.
        </div>
      }
    >
      <ToastProvider>
        <App />
      </ToastProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
