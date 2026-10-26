import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import AppLayout from "@/layouts/AppLayout";

/**
 * The staff header and sidebar carry only controls that do something.
 *
 * The previous shell had four dead header buttons (Workspace/Calendar/
 * Inventory/Reports), a decorative bell with a red dot, a "Book a Demo" link
 * that did not belong in an authenticated shell, and a sidebar "Home" entry
 * that left the workspace. This suite pins the fixed state: exactly one
 * working Workspace link, no bell, no demo CTA, and every sidebar item inside
 * /app except the guide link.
 */

const authState = vi.hoisted(() => ({
  isAuthenticated: true,
  isLoading: false,
  user: { full_name: "Olivia Owner" },
  memberships: [],
  currentClinic: { name: "Enamel Central" },
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

function renderLayout() {
  const { hook } = memoryLocation({ path: "/app" });
  return render(
    <Router hook={hook}>
      <AppLayout>
        <p>dashboard</p>
      </AppLayout>
    </Router>,
  );
}

describe("the staff shell keeps only working controls", () => {
  it("has one Workspace link to /app and header ENAMEL sign links to /app", () => {
    renderLayout();

    const workspace = screen.getAllByRole("link", { name: "Workspace" });
    expect(workspace).toHaveLength(1);
    expect(workspace[0]).toHaveAttribute("href", "/app");

    const wordmark = screen.getByTestId("app-wordmark");
    expect(wordmark).toHaveAttribute("href", "/app");
  });

  it("carries no dead Calendar/Inventory/Reports buttons, no bell and no demo CTA", () => {
    renderLayout();

    expect(screen.queryByText("Calendar", { selector: "button" })).not.toBeInTheDocument();
    expect(screen.queryByText("Inventory")).not.toBeInTheDocument();
    expect(screen.queryByText("Reports")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/notifications?/i)).not.toBeInTheDocument();
    expect(screen.queryByText("Book a Demo")).not.toBeInTheDocument();
  });

  it("sidebar has Dashboard -> /app, no marketing Home, and a Help & guide link", () => {
    renderLayout();

    const dashboard = screen.getAllByRole("link", { name: "Dashboard" });
    expect(dashboard.length).toBeGreaterThanOrEqual(1);
    for (const link of dashboard) {
      expect(link).toHaveAttribute("href", "/app");
    }
    // Every other sidebar target stays inside the workspace.
    for (const item of screen.getAllByRole("link")) {
      const href = item.getAttribute("href") ?? "";
      if (item === screen.getByTestId("sidebar-guide-link")) {
        expect(href).toBe("/guide");
      } else {
        expect(href.startsWith("/app")).toBe(true);
      }
    }
    expect(screen.getByTestId("sidebar-guide-link").getAttribute("href")).toBe("/guide");
  });
});
