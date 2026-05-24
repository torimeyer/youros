import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import TimeFilter, { type TimePeriod } from "./TimeFilter";

describe("TimeFilter", () => {
  it("renders 3 pills with correct labels", () => {
    render(<TimeFilter value="week" onChange={vi.fn()} />);
    expect(screen.getByTestId("time-filter-today")).toBeInTheDocument();
    expect(screen.getByTestId("time-filter-week")).toBeInTheDocument();
    expect(screen.getByTestId("time-filter-month")).toBeInTheDocument();
    expect(screen.getByText("Today")).toBeInTheDocument();
    expect(screen.getByText("This Week")).toBeInTheDocument();
    expect(screen.getByText("This Month")).toBeInTheDocument();
  });

  it("marks the active pill with aria-checked=true", () => {
    render(<TimeFilter value="month" onChange={vi.fn()} />);
    expect(screen.getByTestId("time-filter-month")).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("time-filter-week")).toHaveAttribute("aria-checked", "false");
  });

  it("calls onChange with the correct value when a pill is clicked", () => {
    const handler = vi.fn();
    render(<TimeFilter value="week" onChange={handler} />);
    fireEvent.click(screen.getByTestId("time-filter-today"));
    expect(handler).toHaveBeenCalledWith("today");
  });

  it("calls onChange when Enter is pressed on a focused pill", () => {
    const handler = vi.fn();
    render(<TimeFilter value="week" onChange={handler} />);
    const pill = screen.getByTestId("time-filter-month");
    fireEvent.keyDown(pill, { key: "Enter" });
    expect(handler).toHaveBeenCalledWith("month");
  });

  it("calls onChange when Space is pressed on a focused pill", () => {
    const handler = vi.fn();
    render(<TimeFilter value="week" onChange={handler} />);
    const pill = screen.getByTestId("time-filter-month");
    fireEvent.keyDown(pill, { key: " " });
    expect(handler).toHaveBeenCalledWith("month");
  });

  it("ArrowRight moves selection to next pill", () => {
    const handler = vi.fn();
    render(<TimeFilter value="today" onChange={handler} />);
    fireEvent.keyDown(screen.getByTestId("time-filter-today"), { key: "ArrowRight" });
    expect(handler).toHaveBeenCalledWith("week");
  });

  it("ArrowLeft wraps selection to last pill", () => {
    const handler = vi.fn();
    render(<TimeFilter value="today" onChange={handler} />);
    fireEvent.keyDown(screen.getByTestId("time-filter-today"), { key: "ArrowLeft" });
    expect(handler).toHaveBeenCalledWith("month");
  });

  it("supports custom labels override", () => {
    render(
      <TimeFilter value="today" onChange={vi.fn()} labels={{ today: "Now" }} />
    );
    expect(screen.getByText("Now")).toBeInTheDocument();
    // Other labels stay default
    expect(screen.getByText("This Week")).toBeInTheDocument();
  });

  it("has role=radiogroup on container and role=radio on each pill", () => {
    render(<TimeFilter value="week" onChange={vi.fn()} />);
    const group = screen.getByRole("radiogroup");
    expect(group).toBeInTheDocument();
    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(3);
  });
});
