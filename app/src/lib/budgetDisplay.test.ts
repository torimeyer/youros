import { describe, it, expect } from "vitest";
import {
  budgetToTokens,
  formatTokenBudget,
  formatTokenBudgetApprox,
} from "./budgetDisplay";

describe("budgetDisplay", () => {
  // Conversion math: budget dollars / price-per-1M * 1M = tokens.
  // Sonnet $6/M: $2 -> ~333k, $5 -> ~833k (rounds to ~1M).
  // Haiku  $2/M: $2 -> 1M, $5 -> 2.5M.
  // Opus  $30/M: $10 -> ~333k, $30 -> 1M.

  describe("budgetToTokens", () => {
    it("converts sonnet budget using $6/M rate", () => {
      expect(budgetToTokens(2, "claude-sonnet-4-6")).toBeCloseTo(333_333, -1);
      expect(budgetToTokens(6, "sonnet")).toBeCloseTo(1_000_000, -1);
    });

    it("converts haiku budget using $2/M rate", () => {
      expect(budgetToTokens(2, "claude-haiku-4-5")).toBeCloseTo(1_000_000, -1);
      expect(budgetToTokens(5, "haiku")).toBeCloseTo(2_500_000, -1);
    });

    it("converts opus budget using $30/M rate", () => {
      expect(budgetToTokens(30, "claude-opus-4-6")).toBeCloseTo(1_000_000, -1);
      expect(budgetToTokens(15, "opus")).toBeCloseTo(500_000, -1);
    });

    it("defaults to sonnet rate when model is unknown or missing", () => {
      expect(budgetToTokens(6, undefined)).toBeCloseTo(1_000_000, -1);
      expect(budgetToTokens(6, "mystery-model")).toBeCloseTo(1_000_000, -1);
    });

    it("accepts string budgets and invalid values", () => {
      expect(budgetToTokens("2.00", "sonnet")).toBeCloseTo(333_333, -1);
      expect(budgetToTokens(null, "sonnet")).toBe(0);
      expect(budgetToTokens(0, "sonnet")).toBe(0);
      expect(budgetToTokens("not a number", "sonnet")).toBe(0);
    });
  });

  describe("formatTokenBudget", () => {
    it("rounds to a human-readable token count for each model", () => {
      expect(formatTokenBudget(6, "sonnet")).toBe("1M tokens");
      expect(formatTokenBudget(5, "haiku")).toBe("3M tokens");
      expect(formatTokenBudget(30, "opus")).toBe("1M tokens");
    });

    it("returns empty string when there is no budget", () => {
      expect(formatTokenBudget(0, "sonnet")).toBe("");
      expect(formatTokenBudget(null, "sonnet")).toBe("");
      expect(formatTokenBudget(undefined, "sonnet")).toBe("");
    });

    it("uses k units for sub-million counts", () => {
      // Sonnet $1/M -> 166k tokens, rounds to 170k.
      const out = formatTokenBudget(1, "sonnet");
      expect(out.endsWith("k tokens")).toBe(true);
    });
  });

  describe("formatTokenBudgetApprox", () => {
    it("prefixes with ~ for the detail panel", () => {
      expect(formatTokenBudgetApprox(6, "sonnet")).toBe("~1M tokens");
      expect(formatTokenBudgetApprox(2, "haiku")).toBe("~1M tokens");
    });

    it("returns empty string when there is no budget", () => {
      expect(formatTokenBudgetApprox(null, "sonnet")).toBe("");
    });
  });
});
