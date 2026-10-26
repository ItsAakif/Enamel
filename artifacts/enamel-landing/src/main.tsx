import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthProvider } from "@/hooks/use-auth";
import { configureApi } from "@/lib/api";
import { registerServiceWorker } from "@/lib/pwa";
import { queryClient } from "@/lib/query-client";
import App from "./App";
import "./index.css";

// Point the generated client at the API and restore the selected clinic
// before the first request fires.
configureApi();

// Cache the app shell only; API responses are never cached (see public/sw.js).
registerServiceWorker();

createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <AuthProvider>
        <App />
      </AuthProvider>
    </TooltipProvider>
    <Toaster />
  </QueryClientProvider>,
);
