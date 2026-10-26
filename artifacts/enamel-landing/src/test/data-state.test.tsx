import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DataState } from "@/components/common/DataState";

/**
 * The four-state contract of every data screen (ADR-012).
 *
 * The component is the guarantee that a failed request never renders
 * fabricated data, so the assertions are about what is *not* on the screen as
 * much as about what is.
 */
describe("DataState", () => {
  it("renders the loading skeleton and none of the children", () => {
    render(
      <DataState loading>
        <p>patient list</p>
      </DataState>,
    );

    const status = screen.getByRole("status");
    expect(status).toBeInTheDocument();
    expect(status).toHaveAttribute("aria-busy", "true");
    expect(screen.getByText("Loading")).toBeInTheDocument();
    expect(screen.queryByText("patient list")).not.toBeInTheDocument();
  });

  it("renders the error state with the API message and never the children", () => {
    render(
      <DataState error={new Error("The clinic could not be reached.")}>
        <p>patient list</p>
      </DataState>,
    );

    const alert = screen.getByRole("alert");
    expect(alert).toBeInTheDocument();
    expect(alert).toHaveTextContent("The clinic could not be reached.");
    expect(screen.queryByText("patient list")).not.toBeInTheDocument();
  });

  it("offers a retry action that calls back", async () => {
    const onRetry = vi.fn();
    const user = userEvent.setup();

    render(
      <DataState error={new Error("boom")} onRetry={onRetry}>
        <p>patient list</p>
      </DataState>,
    );

    await user.click(screen.getByRole("button", { name: /retry/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("hides the retry action when no callback is given", () => {
    render(
      <DataState error={new Error("boom")}>
        <p>patient list</p>
      </DataState>,
    );

    expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
  });

  it("renders the empty state with the configured title and message", () => {
    render(
      <DataState empty emptyTitle="No appointments" emptyMessage="You have no appointments on file.">
        <p>patient list</p>
      </DataState>,
    );

    expect(screen.getByText("No appointments")).toBeInTheDocument();
    expect(screen.getByText("You have no appointments on file.")).toBeInTheDocument();
    expect(screen.queryByText("patient list")).not.toBeInTheDocument();
  });

  it("renders the children when the request succeeded", () => {
    render(
      <DataState>
        <p>patient list</p>
      </DataState>,
    );

    expect(screen.getByText("patient list")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("prefers the error state over the empty state", () => {
    render(
      <DataState error={new Error("boom")} empty>
        <p>patient list</p>
      </DataState>,
    );

    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("Nothing here yet")).not.toBeInTheDocument();
  });
});
