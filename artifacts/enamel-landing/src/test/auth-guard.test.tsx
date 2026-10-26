import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { Router, useLocation } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import AppLayout from "@/layouts/AppLayout";

/**
 * The staff route guard.
 *
 * `/app/*` is only reachable with a session: an unauthenticated visitor is
 * redirected to `/login` with the original path in `next`, and the guarded
 * content is never rendered.
 */

const authState = vi.hoisted(() => ({
  isAuthenticated: false,
  isLoading: false,
  user: null,
  memberships: [],
  currentClinic: null,
  activeClinicId: null,
  error: null,
  login: vi.fn(),
  register: vi.fn(),
  logout: vi.fn(),
  selectClinic: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => authState,
}));

function LocationProbe() {
  const [location] = useLocation();
  return <span data-testid="current-path">{location}</span>;
}

function renderAt(path: string) {
  const { hook } = memoryLocation({ path });
  return render(
    <Router hook={hook}>
      <AppLayout>
        <p>patient list</p>
      </AppLayout>
      <LocationProbe />
    </Router>,
  );
}

describe("the staff route guard", () => {
  it("redirects an unauthenticated visitor to /login", async () => {
    authState.isAuthenticated = false;
    authState.isLoading = false;

    renderAt("/app/patients");

    await waitFor(() => {
      expect(screen.getByTestId("current-path").textContent).toBe("/login");
    });
  });

  it("does not render the guarded content for an unauthenticated visitor", async () => {
    authState.isAuthenticated = false;
    authState.isLoading = false;

    renderAt("/app/patients");

    await waitFor(() => {
      expect(screen.getByTestId("current-path").textContent).toBe("/login");
    });
    expect(screen.queryByText("patient list")).not.toBeInTheDocument();
  });

  it("keeps an authenticated visitor on the requested path", async () => {
    authState.isAuthenticated = true;
    authState.isLoading = false;

    renderAt("/app/patients");

    await waitFor(() => {
      expect(screen.getByText("patient list")).toBeInTheDocument();
    });
    expect(screen.getByTestId("current-path").textContent).toBe("/app/patients");
  });

  it("shows the loader instead of redirecting while the session is unknown", () => {
    authState.isAuthenticated = false;
    authState.isLoading = true;

    renderAt("/app/patients");

    expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "true");
    expect(screen.getByTestId("current-path").textContent).toBe("/app/patients");
    expect(screen.queryByText("patient list")).not.toBeInTheDocument();
  });
});
