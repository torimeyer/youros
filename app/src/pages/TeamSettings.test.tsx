import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import TeamSettings from "./TeamSettings";

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

const EXISTING_CONFIG = {
  configured: true,
  config: {
    team_repo: "/Users/alice/team",
    member_id: "alice@example.com",
    display_name: "Alice",
    auto_sync: true,
    category_defaults: {
      decisions: "shared",
      tasks: "private",
      notes: "shared",
      transcripts: "private",
    },
  },
};

function renderTeamSettings() {
  return render(
    <MemoryRouter>
      <TeamSettings />
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

describe("TeamSettings", () => {
  it("pre-fills form from GET /api/team response", async () => {
    vi.mocked(fetch).mockImplementation((input) => {
      const url = typeof input === "string" ? input : String(input);
      if (url === "/api/team") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(EXISTING_CONFIG),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({}),
      } as Response);
    });

    renderTeamSettings();

    await waitFor(() => {
      const repoField = screen.getByTestId(
        "field-team-repo"
      ) as HTMLInputElement;
      expect(repoField.value).toBe("/Users/alice/team");

      const nameField = screen.getByTestId(
        "field-display-name"
      ) as HTMLInputElement;
      expect(nameField.value).toBe("Alice");

      const memberField = screen.getByTestId(
        "field-member-id"
      ) as HTMLInputElement;
      expect(memberField.value).toBe("alice@example.com");

      const autoSyncField = screen.getByTestId(
        "field-auto-sync"
      ) as HTMLInputElement;
      expect(autoSyncField.checked).toBe(true);
    });
  });

  it("submit calls POST /api/team/configure with correct body", async () => {
    vi.mocked(fetch).mockImplementation((input) => {
      const url = typeof input === "string" ? input : String(input);
      if (url === "/api/team") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ configured: false }),
        } as Response);
      }
      if (url === "/api/team/configure") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ ok: true }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({}),
      } as Response);
    });

    renderTeamSettings();

    await waitFor(() => {
      expect(screen.getByTestId("field-team-repo")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId("field-team-repo"), {
      target: { value: "/team/shared" },
    });
    fireEvent.change(screen.getByTestId("field-display-name"), {
      target: { value: "Bob" },
    });
    fireEvent.change(screen.getByTestId("field-member-id"), {
      target: { value: "bob@example.com" },
    });

    fireEvent.click(screen.getByTestId("save-button"));

    await waitFor(() => {
      const calls = vi.mocked(fetch).mock.calls;
      const configureCall = calls.find(([url]) => {
        const u = typeof url === "string" ? url : String(url);
        return u === "/api/team/configure";
      });
      expect(configureCall).toBeDefined();

      const [, opts] = configureCall!;
      const body = JSON.parse((opts as RequestInit).body as string);
      expect(body.team_repo).toBe("/team/shared");
      expect(body.display_name).toBe("Bob");
      expect(body.member_id).toBe("bob@example.com");
    });
  });

  it("shows success message after save", async () => {
    vi.mocked(fetch).mockImplementation((input) => {
      const url = typeof input === "string" ? input : String(input);
      if (url === "/api/team") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ configured: false }),
        } as Response);
      }
      if (url === "/api/team/configure") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ ok: true }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({}),
      } as Response);
    });

    renderTeamSettings();

    await waitFor(() => {
      expect(screen.getByTestId("save-button")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("save-button"));

    await waitFor(() => {
      expect(screen.getByTestId("success-banner")).toBeInTheDocument();
      expect(screen.getByText("Settings saved.")).toBeInTheDocument();
    });
  });
});
