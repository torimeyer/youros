import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Team from "./Team";

// jsdom does not provide window.matchMedia
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

// Suppress the useAppStore notification poll and store calls from TopBar
vi.mock("../lib/api", () => ({
  api: {
    get: vi.fn().mockResolvedValue([]),
    post: vi.fn().mockResolvedValue({}),
  },
  ApiError: class ApiError extends Error {},
  ApiTimeoutError: class ApiTimeoutError extends Error {},
}));

function renderTeam() {
  return render(
    <MemoryRouter>
      <Team />
    </MemoryRouter>
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("Team", () => {
  it("renders empty state when GET /api/team returns configured: false", async () => {
    vi.mocked(fetch).mockImplementation((input) => {
      const url = typeof input === "string" ? input : String(input);
      if (url === "/api/team") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ configured: false }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ items: [], count: 0 }),
      } as Response);
    });

    renderTeam();

    await waitFor(() => {
      expect(
        screen.getByText("Team mode isn't set up yet")
      ).toBeInTheDocument();
    });

    expect(screen.getByText("Configure team")).toBeInTheDocument();
  });

  it("renders search results after search input", async () => {
    const mockResults = [
      {
        member_id: "alice@example.com",
        display_name: "Alice",
        category: "decisions",
        item: {
          title: "Use React for frontend",
          reason: "Team has most experience with it",
          timestamp: new Date().toISOString(),
        },
      },
    ];

    vi.mocked(fetch).mockImplementation((input) => {
      const url = typeof input === "string" ? input : String(input);
      if (url === "/api/team") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ configured: true }),
        } as Response);
      }
      if (url === "/api/team/feed") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ items: [], count: 0 }),
        } as Response);
      }
      if (url.startsWith("/api/team/search")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ results: mockResults, count: 1 }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({}),
      } as Response);
    });

    renderTeam();

    await waitFor(() => {
      expect(screen.getByTestId("team-search-input")).toBeInTheDocument();
    });

    const input = screen.getByTestId("team-search-input");
    fireEvent.change(input, { target: { value: "React" } });

    await waitFor(
      () => {
        expect(screen.getByText("Alice")).toBeInTheDocument();
        expect(screen.getByText("Use React for frontend")).toBeInTheDocument();
      },
      { timeout: 1000 }
    );
  });

  it("Sync now button calls POST /api/team/sync", async () => {
    vi.mocked(fetch).mockImplementation((input) => {
      const url = typeof input === "string" ? input : String(input);
      if (url === "/api/team") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ configured: true }),
        } as Response);
      }
      if (url === "/api/team/feed") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ items: [], count: 0 }),
        } as Response);
      }
      if (url === "/api/team/sync") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ ok: true, pushed: 1, pulled: 0 }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({}),
      } as Response);
    });

    renderTeam();

    await waitFor(() => {
      expect(screen.getByTestId("sync-now-button")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("sync-now-button"));

    await waitFor(() => {
      const calls = vi.mocked(fetch).mock.calls;
      const syncCall = calls.find(([url, opts]) => {
        const u = typeof url === "string" ? url : String(url);
        return (
          u === "/api/team/sync" &&
          (opts as RequestInit)?.method === "POST"
        );
      });
      expect(syncCall).toBeDefined();
    });
  });
});
