import { QueryClient } from "@tanstack/react-query";

/**
 * One QueryClient for the whole app.
 *
 * It lives in its own module (rather than inside `App.tsx`) so the auth
 * provider and pages can invalidate or clear the cache without importing the
 * component tree.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 15_000,
    },
  },
});
