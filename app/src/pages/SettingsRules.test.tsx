import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import SettingsRules from "./SettingsRules";

// Mock the api client.
vi.mock("../lib/api", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

// matchMedia is required by TopBar.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: true,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

import { api } from "../lib/api";

const mockedGet = vi.mocked(api.get);
const mockedPut = vi.mocked(api.put);
const mockedPost = vi.mocked(api.post);

const sampleRules = [
  {
    key: "saa_must_spawn",
    enabled: false,
    params: {
      trigger_words: ["saa", "diagnose", "fix"],
    },
    meta: {
      title: "Always spawn agents for fix/diagnose/saa",
      description: "Block inline edits after the trigger word.",
      category: "agent_flow",
      params: {
        trigger_words: {
          type: "list[str]",
          label: "Trigger words",
          help: "Message prefixes that require an Agent call.",
        },
      },
    },
    default_enabled: false,
    source: "default",
  },
  {
    key: "needle_hygiene",
    enabled: true,
    params: {
      min_title_length: 5,
    },
    meta: {
      title: "Block garbage-titled tasks",
      description: "Stops vague task titles from being created.",
      category: "quality",
      params: {
        min_title_length: {
          type: "int",
          label: "Minimum title length",
          help: "Titles shorter than this are blocked.",
        },
      },
    },
    default_enabled: true,
    source: "default",
  },
  {
    key: "ghost_reaper_live_pid_check",
    enabled: true,
    params: {},
    meta: {
      title: "Check process is actually dead before reaping it",
      description: "Prevents reaping agents whose process is still alive.",
      category: "infrastructure",
      params: {},
    },
    default_enabled: true,
    source: "default",
  },
];

function renderPage() {
  return render(
    <MemoryRouter>
      <SettingsRules />
    </MemoryRouter>,
  );
}

describe("SettingsRules", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedGet.mockResolvedValue(structuredClone(sampleRules));
    mockedPut.mockResolvedValue(structuredClone(sampleRules[0]));
    mockedPost.mockResolvedValue({});
  });

  it("renders a card for each rule from /api/rules", async () => {
    renderPage();
    await waitFor(() => {
      expect(
        screen.getByTestId("rule-card-saa_must_spawn"),
      ).toBeInTheDocument();
    });
    // Default category is agent_flow (first in CATEGORY_ORDER with rules).
    expect(
      screen.getByText("Always spawn agents for fix/diagnose/saa"),
    ).toBeInTheDocument();
  });

  it("groups rules by meta.category and switches with the left nav", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("rule-category-agent_flow")).toBeInTheDocument();
    });
    // Quality category should also appear.
    const qualityBtn = screen.getByTestId("rule-category-quality");
    expect(qualityBtn).toBeInTheDocument();
    // Initially only agent_flow rules are visible.
    expect(
      screen.queryByTestId("rule-card-needle_hygiene"),
    ).not.toBeInTheDocument();
    fireEvent.click(qualityBtn);
    await waitFor(() => {
      expect(
        screen.getByTestId("rule-card-needle_hygiene"),
      ).toBeInTheDocument();
    });
  });

  it("toggles a rule's enabled state and PUTs with optimistic update", async () => {
    renderPage();
    await waitFor(() => {
      expect(
        screen.getByTestId("rule-toggle-saa_must_spawn"),
      ).toBeInTheDocument();
    });
    const toggle = screen.getByTestId("rule-toggle-saa_must_spawn");
    expect(toggle).toHaveAttribute("aria-checked", "false");

    // Backend responds successfully with the updated row.
    mockedPut.mockResolvedValueOnce({
      ...sampleRules[0],
      enabled: true,
      source: "user",
    });

    fireEvent.click(toggle);

    // Optimistic update flips the UI immediately.
    expect(toggle).toHaveAttribute("aria-checked", "true");

    await waitFor(() => {
      expect(mockedPut).toHaveBeenCalledWith("/rules/saa_must_spawn", {
        enabled: true,
      });
    });
    // Success toast appears.
    await waitFor(() => {
      expect(screen.getByTestId("toast-success")).toBeInTheDocument();
    });
  });

  it("rolls back optimistic update and shows error toast on PUT failure", async () => {
    renderPage();
    await waitFor(() => {
      expect(
        screen.getByTestId("rule-toggle-saa_must_spawn"),
      ).toBeInTheDocument();
    });
    const toggle = screen.getByTestId("rule-toggle-saa_must_spawn");

    mockedPut.mockRejectedValueOnce(new Error("boom"));

    fireEvent.click(toggle);
    // Optimistic flip first.
    expect(toggle).toHaveAttribute("aria-checked", "true");

    await waitFor(() => {
      // Rolled back.
      expect(toggle).toHaveAttribute("aria-checked", "false");
    });
    await waitFor(() => {
      expect(screen.getByTestId("toast-error")).toBeInTheDocument();
    });
  });

  it("commits a list[str] param on blur with the parsed array", async () => {
    renderPage();
    await waitFor(() => {
      expect(
        screen.getByTestId("rule-saa_must_spawn-param-trigger_words"),
      ).toBeInTheDocument();
    });
    const input = screen.getByTestId(
      "rule-saa_must_spawn-param-trigger_words",
    ) as HTMLInputElement;
    expect(input.value).toBe("saa, diagnose, fix");

    fireEvent.change(input, { target: { value: "saa, fix, debug" } });
    fireEvent.blur(input);

    await waitFor(() => {
      expect(mockedPut).toHaveBeenCalledWith("/rules/saa_must_spawn", {
        params: { trigger_words: ["saa", "fix", "debug"] },
      });
    });
  });

  it("does not PUT when the param value is unchanged on blur", async () => {
    renderPage();
    await waitFor(() => {
      expect(
        screen.getByTestId("rule-saa_must_spawn-param-trigger_words"),
      ).toBeInTheDocument();
    });
    const input = screen.getByTestId(
      "rule-saa_must_spawn-param-trigger_words",
    ) as HTMLInputElement;
    fireEvent.focus(input);
    fireEvent.blur(input);
    expect(mockedPut).not.toHaveBeenCalled();
  });

  it("resets a single rule to default and refreshes the card", async () => {
    renderPage();
    await waitFor(() => {
      expect(
        screen.getByTestId("rule-reset-saa_must_spawn"),
      ).toBeInTheDocument();
    });
    mockedPost.mockResolvedValueOnce({});
    // After reset, the backend returns the rule with enabled=false and source=default.
    mockedGet.mockResolvedValueOnce(structuredClone(sampleRules));

    fireEvent.click(screen.getByTestId("rule-reset-saa_must_spawn"));

    await waitFor(() => {
      expect(mockedPost).toHaveBeenCalledWith("/rules/reset", {
        key: "saa_must_spawn",
      });
    });
    // GET fires again to reload the list.
    await waitFor(() => {
      expect(mockedGet).toHaveBeenCalledTimes(2);
    });
  });

  it("reset-all asks for confirmation and POSTs an empty body", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("reset-all-rules")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("reset-all-rules"));
    // Confirm dialog appears.
    expect(screen.getByText("Reset all rules?")).toBeInTheDocument();
    // Confirm.
    fireEvent.click(screen.getByTestId("confirm-modal-confirm"));
    await waitFor(() => {
      expect(mockedPost).toHaveBeenCalledWith("/rules/reset", {});
    });
  });

  it("shows the source badge based on `source` field", async () => {
    const overridden = structuredClone(sampleRules);
    overridden[0].source = "user";
    overridden[0].enabled = true;
    mockedGet.mockResolvedValueOnce(overridden);
    renderPage();
    await waitFor(() => {
      expect(
        screen.getByTestId("rule-source-saa_must_spawn"),
      ).toHaveTextContent("user override");
    });
  });

  it("surfaces a load error when GET fails", async () => {
    mockedGet.mockRejectedValueOnce(new Error("nope"));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("rules-load-error")).toBeInTheDocument();
    });
  });
});
