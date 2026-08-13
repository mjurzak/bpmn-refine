import { describe, expect, it } from "vitest";
import { PROVIDER_OPTIONS } from "./llmProviders.js";

describe("CLI LLM provider choices", () => {
  it("keeps the existing API providers and adds only the requested CLI harnesses", () => {
    expect(PROVIDER_OPTIONS.map((provider) => provider.value)).toEqual([
      "openai",
      "anthropic",
      "codex_cli",
      "claude_cli",
      "gemini",
      "ollama",
    ]);
    expect(PROVIDER_OPTIONS.find((provider) => provider.value === "codex_cli")).toMatchObject({
      label: "Codex CLI",
      customOnly: true,
      reasoningEfforts: ["low", "medium", "high", "xhigh"],
    });
    expect(PROVIDER_OPTIONS.find((provider) => provider.value === "claude_cli")).toMatchObject({
      label: "Claude Code",
      customOnly: true,
      reasoningEfforts: ["low", "medium", "high", "xhigh"],
    });
    expect(PROVIDER_OPTIONS.some((provider) => provider.value === "gemini_cli")).toBe(false);
    expect(PROVIDER_OPTIONS.some((provider) => provider.value === "agy")).toBe(false);
  });
});
