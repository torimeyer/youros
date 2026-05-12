import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ExportButton from "./ExportButton";

describe("ExportButton", () => {
  let clickedHrefs: string[] = [];
  let originalCreateElement: typeof document.createElement;

  beforeEach(() => {
    clickedHrefs = [];
    originalCreateElement = document.createElement.bind(document);
    // Intercept anchor clicks so the test can record the URL without
    // actually navigating.
    vi.spyOn(document, "createElement").mockImplementation(
      ((tag: string) => {
        const el = originalCreateElement(tag) as HTMLAnchorElement;
        if (tag === "a") {
          el.click = () => {
            clickedHrefs.push(el.href);
          };
        }
        return el;
      }) as typeof document.createElement
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the export button", () => {
    render(<ExportButton buildUrl={(f) => `/api/export/tasks?format=${f}`} />);
    expect(screen.getByTestId("export-button")).toBeInTheDocument();
  });

  it("shows the Download label in the button", () => {
    render(<ExportButton buildUrl={(f) => `/api/export/tasks?format=${f}`} />);
    expect(screen.getByText("Download")).toBeInTheDocument();
  });

  it("opens the dropdown menu on click", () => {
    render(<ExportButton buildUrl={(f) => `/api/export/tasks?format=${f}`} />);
    expect(screen.queryByTestId("export-menu")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("export-button"));
    expect(screen.getByTestId("export-menu")).toBeInTheDocument();
    expect(screen.getByText("As Markdown")).toBeInTheDocument();
    expect(screen.getByText("As CSV")).toBeInTheDocument();
  });

  it("triggers a download with the correct URL when a format is picked", () => {
    render(
      <ExportButton
        contentLabel="tasks"
        buildUrl={(f) => `/api/export/tasks?format=${f}&status=open`}
      />
    );
    fireEvent.click(screen.getByTestId("export-button"));
    fireEvent.click(screen.getByTestId("export-option-csv"));
    expect(clickedHrefs).toHaveLength(1);
    expect(clickedHrefs[0]).toContain("/api/export/tasks?format=csv&status=open");
  });

  it("shows a confirmation message after triggering a download", () => {
    render(
      <ExportButton
        contentLabel="tasks"
        buildUrl={(f) => `/api/export/tasks?format=${f}`}
      />
    );
    fireEvent.click(screen.getByTestId("export-button"));
    fireEvent.click(screen.getByTestId("export-option-markdown"));
    expect(screen.getByTestId("export-confirmation")).toHaveTextContent(
      "Downloaded tasks.md"
    );
  });

  it("only renders the formats requested via props", () => {
    render(
      <ExportButton
        contentLabel="timeline"
        formats={["markdown"]}
        buildUrl={(f) => `/api/export/timeline?format=${f}`}
      />
    );
    fireEvent.click(screen.getByTestId("export-button"));
    expect(screen.getByText("As Markdown")).toBeInTheDocument();
    expect(screen.queryByText("As CSV")).not.toBeInTheDocument();
  });
});
