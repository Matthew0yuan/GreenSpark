import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { AppState } from "./state";
import "./styles.css";

const qc = new QueryClient({
  defaultOptions: { queries: { staleTime: 5 * 60_000, refetchOnWindowFocus: false, retry: (n, e) => n < 2 && !(e as { status?: number }).status } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <AppState><App /></AppState>
    </QueryClientProvider>
  </StrictMode>,
);
