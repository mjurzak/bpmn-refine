import React, { useState, useCallback, useRef, useEffect } from "react";
import { layoutProcess } from "bpmn-auto-layout";
import BpmnEditor from "./components/BpmnEditor.jsx";
import CodeView from "./components/CodeView.jsx";
import ValidationPanel from "./components/ValidationPanel.jsx";
import ChatPanel from "./components/ChatPanel.jsx";
import HistoryPanel from "./components/HistoryPanel.jsx";
import LogsPanel from "./components/LogsPanel.jsx";
import {
  uploadDiagram,
  validateDiagram,
  exportDiagram,
  parseDiagramXml,
  repairDiagram,
  repairXml,
  applyEditOps,
  commitRevision,
} from "./api/client.js";
import { summarizeDiagramDiff } from "./utils/irDiff.js";
import useResize from "./hooks/useResize.js";
import "./app.css";

const UI_SESSION_KEYS = {
  darkMode: "bpmn-ai-validator.dark-mode",
  chatOpen: "bpmn-ai-validator.chat-open",
  leftPanelOpen: "bpmn-ai-validator.left-panel-open",
  historyOpen: "bpmn-ai-validator.history-open",
  approvalMode: "bpmn-ai-validator.approval-mode",
  llmSettings: "bpmn-ai-validator.llm-settings",
  assistantTab: "bpmn-ai-validator.assistant-tab",
  editorTab: "bpmn-ai-validator.editor-tab",
};

const EDITOR_TABS = {
  DIAGRAM: "diagram",
  CODE: "code",
};

const APPROVAL_MODES = {
  MANUAL: "manual",
  AUTO: "autoapprove",
};

const VALIDATION_MODES = {
  STRUCTURAL: "structural",
  DEEP: "deep",
  SEMANTIC: "semantic",
};

const DEEP_VALIDATE_CONFIG = {
  tiers_enabled: { t1: true, t2: true, t3: false },
  t2_tools: ["woflan"],
};

const SEMANTIC_VALIDATE_CONFIG = {
  tiers_enabled: { t1: true, t2: false, t3: true },
};

// which tiers each button turns on. the repair loop re-validates with whatever
// the last validation used, so a tier that found an issue is a tier that can
// confirm the fix
const VALIDATION_MODE_CONFIG = {
  [VALIDATION_MODES.STRUCTURAL]: {},
  [VALIDATION_MODES.DEEP]: DEEP_VALIDATE_CONFIG,
  [VALIDATION_MODES.SEMANTIC]: SEMANTIC_VALIDATE_CONFIG,
};

const PROVIDER_OPTIONS = [
  {
    value: "openai",
    label: "OpenAI",
    models: ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5"],
    reasoningEfforts: ["none", "low", "medium", "high", "xhigh"],
  },
  {
    value: "anthropic",
    label: "Anthropic",
    models: [
      "claude-opus-5",
      "claude-fable-5",
      "claude-sonnet-5",
      "claude-haiku-4-5",
      "claude-opus-4-8",
    ],
    reasoningEfforts: ["low", "medium", "high"],
  },
  {
    value: "gemini",
    label: "Gemini",
    models: [
      "gemini-3.1-pro-preview",
      "gemini-3.5-flash",
      "gemini-3-flash-preview",
      "gemini-3.1-flash-lite",
    ],
    reasoningEfforts: [],
  },
  {
    value: "ollama",
    label: "Ollama",
    models: [],
    customOnly: true,
    customPlaceholder: "llama3.2",
    reasoningEfforts: [],
  },
];

const INTERACTION_OPTIONS = [
  { key: "validation", label: "Validation" },
  { key: "chat", label: "Chat" },
  { key: "repair", label: "Repair" },
];

const DEFAULT_LLM_SETTINGS = {
  validation: {
    provider: "openai",
    model: "gpt-5.6-sol",
    reasoning_effort: "medium",
  },
  chat: { provider: "openai", model: "gpt-5.6-sol", reasoning_effort: "medium" },
  repair: { provider: "openai", model: "gpt-5.6-sol", reasoning_effort: "high" },
};

const VALIDATION_LOADING_LABELS = {
  [VALIDATION_MODES.STRUCTURAL]: "Verifying rules...",
  [VALIDATION_MODES.DEEP]: "Running formal validation...",
  [VALIDATION_MODES.SEMANTIC]: "Running semantic LLM validation...",
};

const ASSISTANT_TABS = {
  CHAT: "chat",
  LOGS: "logs",
};

function newLogId(prefix = "log") {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function apiTraceEntries(title, traces = []) {
  return traces.map((trace, index) => ({
    id: trace.trace_id ?? newLogId("llm"),
    type: "llm",
    status: trace.error ? "error" : "success",
    title: `${title} LLM call ${traces.length > 1 ? index + 1 : ""}`.trim(),
    summary: `${trace.model}${trace.reasoning_effort ? ` / ${trace.reasoning_effort}` : ""}`,
    timestamp: trace.started_at ?? new Date().toISOString(),
    durationMs: trace.duration_ms,
    trace,
  }));
}

function readSessionBoolean(key, fallback) {
  try {
    const value = window.sessionStorage.getItem(key);
    if (value === "true") return true;
    if (value === "false") return false;
  } catch {
    return fallback;
  }
  return fallback;
}

function writeSessionBoolean(key, value) {
  try {
    window.sessionStorage.setItem(key, String(value));
  } catch {
    // storage can be unavailable in private or locked-down browser sessions
  }
}

function readSessionString(key, fallback, allowedValues) {
  try {
    const value = window.sessionStorage.getItem(key);
    if (!allowedValues || allowedValues.includes(value))
      return value ?? fallback;
  } catch {
    return fallback;
  }
  return fallback;
}

function writeSessionString(key, value) {
  try {
    window.sessionStorage.setItem(key, value);
  } catch {
    // storage can be unavailable in private or locked-down browser sessions
  }
}

function readSessionJson(key, fallback) {
  try {
    const raw = window.sessionStorage.getItem(key);
    if (!raw) return fallback;
    return { ...fallback, ...JSON.parse(raw) };
  } catch {
    return fallback;
  }
}

function writeSessionJson(key, value) {
  try {
    window.sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    // storage can be unavailable in private or locked-down browser sessions
  }
}

function providerByValue(value) {
  return (
    PROVIDER_OPTIONS.find((provider) => provider.value === value) ??
    PROVIDER_OPTIONS[0]
  );
}

function normaliseLlmSettings(settings) {
  return INTERACTION_OPTIONS.reduce((normalised, interaction) => {
    const fallback = DEFAULT_LLM_SETTINGS[interaction.key];
    const raw = { ...fallback, ...(settings?.[interaction.key] ?? {}) };
    const provider = providerByValue(raw.provider);
    const model = raw.model;
    const knownModel = provider.models.includes(model);
    const reasoningEfforts = provider.reasoningEfforts ?? [];
    normalised[interaction.key] = {
      provider: provider.value,
      model: provider.customOnly
        ? model?.trim() || provider.customPlaceholder || ""
        : knownModel
          ? model
          : model?.trim() || provider.models[0],
      reasoning_effort: reasoningEfforts.includes(raw.reasoning_effort)
        ? raw.reasoning_effort
        : fallback.reasoning_effort,
    };
    if (!reasoningEfforts.length) {
      normalised[interaction.key].reasoning_effort = null;
    }
    return normalised;
  }, {});
}

function interactionConfig(settings) {
  const provider = providerByValue(settings.provider);
  const model =
    settings.model?.trim() || provider.models[0] || provider.customPlaceholder;
  const config = {
    provider_override: provider.value,
    model_tier: "custom",
    model_override: model,
  };
  if ((provider.reasoningEfforts ?? []).includes(settings.reasoning_effort)) {
    config.reasoning_effort = settings.reasoning_effort;
  }
  return config;
}

function mergeExperimentConfig(...configs) {
  return configs.reduce(
    (merged, config) => ({
      ...merged,
      ...config,
      tiers_enabled: {
        ...(merged.tiers_enabled ?? {}),
        ...(config.tiers_enabled ?? {}),
      },
    }),
    {},
  );
}

// default the validation/history split so the history panel starts as the
// shorter section — its top edge sits below the screen midpoint — and scales
// with viewport height instead of a fixed pixel guess
function verticalSplitConfig() {
  const fallback = { initial: 320, min: 60, max: 500 };
  if (typeof window === "undefined") return fallback;
  const body = window.innerHeight - 95; // toolbar + sidebar header + resize handle
  if (body <= 200) return fallback;
  const max = Math.max(500, body - 150); // keep ~150px for history on tall screens
  const initial = Math.min(Math.round(body * 0.58), max);
  return { initial, min: 60, max };
}

// the IR carries the author's BPMNDI now, so a diagram that arrived with a
// layout keeps it — re-laying out would move every element and turn a one-flow
// repair into a 100% visual diff. Only diagrams with no geometry at all (hand
// written XML, some exports) get laid out, and new nodes are placed clear of the
// existing shapes by the serialiser.
async function autoLayout(xmlString) {
  if (xmlString?.includes("<bpmndi:BPMNShape") || xmlString?.includes("BPMNShape")) {
    return xmlString;
  }
  try {
    return await layoutProcess(xmlString);
  } catch (err) {
    console.warn("auto-layout failed, using raw XML:", err);
    return xmlString;
  }
}

function SendIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <line x1="22" y1="2" x2="11" y2="13" />
      <polygon points="22 2 15 22 11 13 2 9 22 2" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  );
}

function SunIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="12" cy="12" r="5" />
      <line x1="12" y1="1" x2="12" y2="3" />
      <line x1="12" y1="21" x2="12" y2="23" />
      <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
      <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
      <line x1="1" y1="12" x2="3" y2="12" />
      <line x1="21" y1="12" x2="23" y2="12" />
      <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
      <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
    </svg>
  );
}

function DownloadIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="7 10 12 15 17 10" />
      <line x1="12" y1="15" x2="12" y2="3" />
    </svg>
  );
}

function UploadIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="17 8 12 3 7 8" />
      <line x1="12" y1="3" x2="12" y2="15" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function SparkIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M13 2 3 14h8l-1 8 10-12h-8l1-8z" />
    </svg>
  );
}

function RepairIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18v3h3l6.3-6.3a4 4 0 0 0 5.4-5.4l-2.8 2.8-3-3 2.8-2.8z" />
    </svg>
  );
}

function ChatIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z" />
    </svg>
  );
}

function SidePanelIcon({ side = "right" }) {
  const dividerX = side === "left" ? 8 : 16;
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <line x1={dividerX} y1="4" x2={dividerX} y2="20" />
    </svg>
  );
}

function SettingsIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6V21a2 2 0 1 1-4 0v-.2a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.6-1H3a2 2 0 1 1 0-4h.2a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3h.1a1.7 1.7 0 0 0 .9-1.5V3a2 2 0 1 1 4 0v.2a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9v.1a1.7 1.7 0 0 0 1.5.9h.2a2 2 0 1 1 0 4H21a1.7 1.7 0 0 0-1.6 1z" />
    </svg>
  );
}

function LlmSettingsPanel({ settings, onChange, onClose }) {
  function updateInteraction(interaction, patch) {
    onChange((current) => ({
      ...current,
      [interaction]: {
        ...DEFAULT_LLM_SETTINGS[interaction],
        ...current[interaction],
        ...patch,
      },
    }));
  }

  function handleProviderChange(interaction, providerValue) {
    const provider = providerByValue(providerValue);
    const defaultEffort = DEFAULT_LLM_SETTINGS[interaction]?.reasoning_effort;
    const reasoningEffort = provider.reasoningEfforts?.includes(defaultEffort)
      ? defaultEffort
      : (provider.reasoningEfforts?.[0] ?? null);
    updateInteraction(interaction, {
      provider: provider.value,
      model: provider.customOnly
        ? (provider.customPlaceholder ?? "")
        : provider.models[0],
      reasoning_effort: reasoningEffort,
    });
  }

  return (
    <div
      className="llm-settings-popover"
      role="dialog"
      aria-label="LLM model settings"
    >
      <div className="llm-settings-header">
        <div>
          <div className="llm-settings-title">LLM settings</div>
          <div className="llm-settings-subtitle">
            Provider and model per interaction
          </div>
        </div>
        <button
          className="panel-toggle-btn"
          onClick={onClose}
          type="button"
          aria-label="Close LLM settings"
        >
          x
        </button>
      </div>

      <div className="llm-settings-body">
        {INTERACTION_OPTIONS.map((interaction) => {
          const selected =
            settings[interaction.key] ?? DEFAULT_LLM_SETTINGS[interaction.key];
          const provider = providerByValue(selected.provider);
          const modelInList = provider.models.includes(selected.model);
          const usesCustomModel = provider.customOnly || !modelInList;
          const reasoningEfforts = provider.reasoningEfforts ?? [];
          return (
            <section className="llm-settings-row" key={interaction.key}>
              <div className="llm-settings-row-title">{interaction.label}</div>
              <div className="llm-settings-controls">
                <label>
                  <span>Provider</span>
                  <select
                    value={selected.provider}
                    onChange={(event) =>
                      handleProviderChange(interaction.key, event.target.value)
                    }
                  >
                    {PROVIDER_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Model</span>
                  <select
                    value={usesCustomModel ? "__custom" : selected.model}
                    disabled={provider.customOnly}
                    onChange={(event) => {
                      if (event.target.value === "__custom") {
                        updateInteraction(interaction.key, {
                          model: modelInList ? "" : selected.model,
                        });
                      } else {
                        updateInteraction(interaction.key, {
                          model: event.target.value,
                        });
                      }
                    }}
                  >
                    {provider.models.map((model) => (
                      <option key={model} value={model}>
                        {model}
                      </option>
                    ))}
                    <option value="__custom">Custom</option>
                  </select>
                </label>
                {usesCustomModel && (
                  <label className="llm-settings-custom-model">
                    <span>Custom model</span>
                    <input
                      value={selected.model}
                      placeholder={
                        provider.customPlaceholder ?? provider.models[0]
                      }
                      onChange={(event) =>
                        updateInteraction(interaction.key, {
                          model: event.target.value,
                        })
                      }
                    />
                  </label>
                )}
                {reasoningEfforts.length > 0 && (
                  <label>
                    <span>Reasoning effort</span>
                    <select
                      value={selected.reasoning_effort ?? reasoningEfforts[0]}
                      onChange={(event) =>
                        updateInteraction(interaction.key, {
                          reasoning_effort: event.target.value,
                        })
                      }
                    >
                      {reasoningEfforts.map((effort) => (
                        <option key={effort} value={effort}>
                          {effort}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}

function proposalOpElementIds(op) {
  if (!op) return [];
  switch (op.op) {
    case "add_node":
    case "remove_node":
    case "rename_element":
    case "change_node_type":
    case "change_gateway_type":
      return [op.id];
    case "add_flow":
      return [op.source_ref, op.target_ref].filter(Boolean);
    case "remove_flow":
      return [op.id];
    case "set_condition":
      return [op.flow_id];
    default:
      return [];
  }
}

export default function App() {
  const [xml, setXml] = useState(null);
  const [diagram, setDiagram] = useState(null);
  const [validationSourceDiagram, setValidationSourceDiagram] = useState(null);
  const [canvasDirty, setCanvasDirty] = useState(false);
  const [sessionId, setSessionId] = useState(null);
  const [currentRevId, setCurrentRevId] = useState(null);
  const [previewRev, setPreviewRev] = useState(null); // { rev_id, diagram, message, index }
  const [validationResult, setValidationResult] = useState(null);
  const [validating, setValidating] = useState(false);
  const [repairing, setRepairing] = useState(false);
  const [validationMode, setValidationMode] = useState(null);
  // survives the run, unlike validationMode — repair needs to know which tiers
  // produced the issues it is about to fix
  const [lastValidationTiers, setLastValidationTiers] = useState(null);
  const [pendingProposal, setPendingProposal] = useState(null);
  const [darkMode, setDarkMode] = useState(() =>
    readSessionBoolean(UI_SESSION_KEYS.darkMode, false),
  );
  const [chatOpen, setChatOpen] = useState(() =>
    readSessionBoolean(UI_SESSION_KEYS.chatOpen, true),
  );
  const [leftPanelOpen, setLeftPanelOpen] = useState(() =>
    readSessionBoolean(UI_SESSION_KEYS.leftPanelOpen, true),
  );
  const [historyOpen, setHistoryOpen] = useState(() =>
    readSessionBoolean(UI_SESSION_KEYS.historyOpen, true),
  );
  const [approvalMode, setApprovalMode] = useState(() =>
    readSessionString(
      UI_SESSION_KEYS.approvalMode,
      APPROVAL_MODES.MANUAL,
      Object.values(APPROVAL_MODES),
    ),
  );
  const [assistantTab, setAssistantTab] = useState(() =>
    readSessionString(
      UI_SESSION_KEYS.assistantTab,
      ASSISTANT_TABS.CHAT,
      Object.values(ASSISTANT_TABS),
    ),
  );
  const [editorTab, setEditorTab] = useState(() =>
    readSessionString(
      UI_SESSION_KEYS.editorTab,
      EDITOR_TABS.DIAGRAM,
      Object.values(EDITOR_TABS),
    ),
  );
  // set when an imported file cannot be parsed into the IR (e.g. duplicate ids);
  // the diagram falls back to a code-only view with LLM repair as the way out
  const [parseError, setParseError] = useState(null);
  const [logEntries, setLogEntries] = useState([]);
  const [llmSettingsOpen, setLlmSettingsOpen] = useState(false);
  const [llmSettings, setLlmSettings] = useState(() =>
    normaliseLlmSettings(
      readSessionJson(UI_SESSION_KEYS.llmSettings, DEFAULT_LLM_SETTINGS),
    ),
  );

  useEffect(() => {
    document.documentElement.setAttribute(
      "data-theme",
      darkMode ? "dark" : "light",
    );
    writeSessionBoolean(UI_SESSION_KEYS.darkMode, darkMode);
  }, [darkMode]);

  useEffect(() => {
    writeSessionBoolean(UI_SESSION_KEYS.chatOpen, chatOpen);
  }, [chatOpen]);

  useEffect(() => {
    writeSessionBoolean(UI_SESSION_KEYS.leftPanelOpen, leftPanelOpen);
  }, [leftPanelOpen]);

  useEffect(() => {
    writeSessionBoolean(UI_SESSION_KEYS.historyOpen, historyOpen);
  }, [historyOpen]);

  useEffect(() => {
    writeSessionString(UI_SESSION_KEYS.approvalMode, approvalMode);
  }, [approvalMode]);

  useEffect(() => {
    writeSessionString(UI_SESSION_KEYS.assistantTab, assistantTab);
  }, [assistantTab]);

  useEffect(() => {
    writeSessionString(UI_SESSION_KEYS.editorTab, editorTab);
  }, [editorTab]);

  useEffect(() => {
    writeSessionJson(UI_SESSION_KEYS.llmSettings, llmSettings);
  }, [llmSettings]);

  const editorRef = useRef(null);

  // bpmn-js measures zero while hidden, so refit the canvas when its tab returns
  useEffect(() => {
    if (editorTab === EDITOR_TABS.DIAGRAM) {
      requestAnimationFrame(() => editorRef.current?.resize());
    }
  }, [editorTab]);

  const sidebar = useResize({
    initial: 340,
    min: 260,
    max: 520,
    axis: "horizontal",
  });
  const validationSplit = useResize({
    ...verticalSplitConfig(),
    axis: "vertical",
  });
  const assistantPanel = useResize({
    initial: 360,
    min: 280,
    max: 560,
    axis: "horizontal",
    direction: -1,
  });

  const appendLogEntries = useCallback((entries) => {
    const normalised = entries.map((entry) => ({
      ...entry,
      id: entry.id ?? newLogId(entry.type ?? "log"),
      timestamp: entry.timestamp ?? new Date().toISOString(),
      status: entry.status ?? "info",
    }));
    setLogEntries((current) => [...current, ...normalised].slice(-300));
  }, []);

  const recordApiActivity = useCallback(
    ({
      title,
      summary,
      startedAt,
      response = null,
      error = null,
      details = null,
    }) => {
      const traces = response?.llm_traces ?? error?.payload?.llm_traces ?? [];
      const durationMs = startedAt
        ? Math.round(performance.now() - startedAt)
        : undefined;
      appendLogEntries([
        {
          type: "action",
          status: error ? "error" : "success",
          title,
          summary: error ? error.message : summary,
          durationMs,
          details: {
            ...(details ?? {}),
            run: response?.run ?? error?.payload?.run ?? null,
            error: error?.message ?? null,
          },
        },
        ...apiTraceEntries(title, traces),
      ]);
    },
    [appendLogEntries],
  );

  const handleXmlChange = useCallback(
    (updatedXml) => {
      // ignore canvas changes while previewing a historical snapshot
      if (previewRev) return;
      if (pendingProposal) setPendingProposal(null);
      setXml(updatedXml);
      setCanvasDirty(true);
    },
    [previewRev, pendingProposal],
  );

  async function applyDiagram(updatedDiagram) {
    setDiagram(updatedDiagram);
    setValidationSourceDiagram(updatedDiagram);
    setCanvasDirty(false);
    setParseError(null);
    try {
      const exported = await exportDiagram(updatedDiagram);
      const laid = await autoLayout(exported.xml);
      setXml(laid);
    } catch (err) {
      console.error("failed to apply diagram update:", err);
    }
  }

  async function handleUpload(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    const startedAt = performance.now();
    dismissPreview();
    // keep the raw text so the file is always viewable, even if it cannot parse
    const rawText = await file.text().catch(() => null);
    // allow re-importing the same file after a fix by clearing the input value
    e.target.value = "";
    try {
      const res = await uploadDiagram(file);
      setDiagram(res.diagram);
      setValidationSourceDiagram(res.diagram);
      setCanvasDirty(false);
      setSessionId(res.session_id);
      setCurrentRevId("0000");
      setPendingProposal(null);
      setParseError(null);
      setValidationResult(null);
      setEditorTab(EDITOR_TABS.DIAGRAM);
      const exported = await exportDiagram(res.diagram);
      const laid = await autoLayout(exported.xml);
      setXml(laid);
      recordApiActivity({
        title: "Import BPMN",
        summary: file.name,
        startedAt,
        details: { file: file.name, session_id: res.session_id },
      });
    } catch (err) {
      // unparseable file (e.g. duplicate ids): fall back to a code-only view so
      // the user can still inspect the source and run free-form LLM repair
      if (rawText) {
        setDiagram(null);
        setValidationSourceDiagram(null);
        setCanvasDirty(false);
        setSessionId(null);
        setCurrentRevId(null);
        setPendingProposal(null);
        setValidationResult(null);
        setParseError(err.message);
        setXml(rawText);
        setEditorTab(EDITOR_TABS.CODE);
      }
      recordApiActivity({
        title: "Import BPMN",
        startedAt,
        error: err,
        details: { file: file.name, code_only: Boolean(rawText) },
      });
      if (!rawText) alert(`Upload failed: ${err.message}`);
    }
  }

  async function handleValidate(mode = VALIDATION_MODES.STRUCTURAL) {
    if (!xml) return alert("Upload a diagram first.");
    const includeSemanticPass = mode === VALIDATION_MODES.SEMANTIC;
    const modeConfig = VALIDATION_MODE_CONFIG[mode] ?? {};
    const config = mergeExperimentConfig(
      interactionConfig(llmSettings.validation),
      modeConfig,
    );
    setLastValidationTiers(modeConfig.tiers_enabled ?? null);
    setValidating(true);
    setValidationMode(mode);
    const startedAt = performance.now();
    try {
      let diagramForValidation = validationSourceDiagram ?? diagram;
      let validationSource = "source_diagram";
      if (!diagramForValidation || canvasDirty) {
        const currentXml = await editorRef.current?.getXml();
        const parsed = await parseDiagramXml(currentXml ?? xml);
        if (currentXml) setXml(currentXml);
        setDiagram(parsed.diagram);
        setValidationSourceDiagram(parsed.diagram);
        setCanvasDirty(false);
        diagramForValidation = parsed.diagram;
        validationSource = "canvas_xml";
      }
      const res = await validateDiagram(
        diagramForValidation,
        includeSemanticPass,
        config,
      );
      setValidationResult(res);
      const issueTotal =
        (res.issues?.length ?? 0) + (res.semantic_issues?.length ?? 0);
      recordApiActivity({
        title:
          mode === VALIDATION_MODES.SEMANTIC
            ? "Semantic validation"
            : mode === VALIDATION_MODES.DEEP
              ? "Deep validation"
              : "Structural validation",
        summary:
          issueTotal === 0
            ? "no issues found"
            : `${issueTotal} issue${issueTotal === 1 ? "" : "s"} found`,
        startedAt,
        response: res,
        details: {
          mode,
          is_valid: res.is_valid,
          issues: res.issues?.length ?? 0,
          semantic_issues: res.semantic_issues?.length ?? 0,
          source: validationSource,
        },
      });
    } catch (err) {
      recordApiActivity({
        title:
          mode === VALIDATION_MODES.SEMANTIC
            ? "Semantic validation"
            : mode === VALIDATION_MODES.DEEP
              ? "Deep validation"
              : "Structural validation",
        startedAt,
        error: err,
        details: {
          mode,
          source: canvasDirty ? "canvas_xml" : "source_diagram",
        },
      });
      alert(`Validation failed: ${err.message}`);
    } finally {
      setValidating(false);
      setValidationMode(null);
    }
  }

  async function handleChatUpdate(updatedDiagram, newRevId, newSessionId) {
    dismissPreview();
    await applyDiagram(updatedDiagram);
    if (newSessionId) setSessionId(newSessionId);
    if (newRevId) setCurrentRevId(newRevId);
  }

  function handleDiagramProposal({
    source,
    diagram: proposedDiagram,
    baseDiagram = diagram,
    message,
    remainingIssues = null,
    ops = [],
  }) {
    if (!baseDiagram || !proposedDiagram) return;
    setPendingProposal({
      source,
      baseDiagram,
      diagram: proposedDiagram,
      message,
      remainingIssues,
      ops,
      diff: summarizeDiagramDiff(baseDiagram, proposedDiagram),
    });
    setChatOpen(true);
  }

  async function applyAcceptedProposal(selectedOps = null) {
    if (!pendingProposal) return;
    const startedAt = performance.now();
    try {
      let acceptedDiagram = pendingProposal.diagram;
      let acceptedRemainingIssues = pendingProposal.remainingIssues;
      if (pendingProposal.ops?.length > 0 && selectedOps !== null) {
        if (selectedOps.length === 0) {
          alert("Select at least one operation to apply.");
          return;
        }
        const applied = await applyEditOps(
          pendingProposal.baseDiagram,
          selectedOps,
        );
        const failed = applied.op_results.filter((result) => !result.applied);
        if (failed.length > 0) {
          alert(
            `Could not apply selected operation: ${failed[0].error ?? "unknown error"}`,
          );
          return;
        }
        acceptedDiagram = applied.updated_diagram;
        if (selectedOps.length !== pendingProposal.ops.length) {
          acceptedRemainingIssues = null;
        }
      }
      const commit = await commitRevision(
        acceptedDiagram,
        sessionId,
        pendingProposal.message,
        "llm",
      );
      dismissPreview();
      await applyDiagram(commit.diagram);
      setSessionId(commit.session_id);
      setCurrentRevId(commit.new_rev_id);
      if (pendingProposal.source === "repair" && acceptedRemainingIssues) {
        setValidationResult({
          is_valid: acceptedRemainingIssues.length === 0,
          issues: acceptedRemainingIssues,
          semantic_issues: [],
        });
      } else {
        setValidationResult(null);
      }
      editorRef.current?.clearDiff();
      recordApiActivity({
        title:
          pendingProposal.source === "repair"
            ? "Apply repair proposal"
            : "Apply chat proposal",
        summary:
          pendingProposal.ops?.length > 0
            ? `${selectedOps?.length ?? pendingProposal.ops.length} operation${(selectedOps?.length ?? pendingProposal.ops.length) === 1 ? "" : "s"} applied`
            : "proposal accepted",
        startedAt,
        response: commit,
        details: {
          source: pendingProposal.source,
          selected_ops: selectedOps?.length ?? null,
          new_rev_id: commit.new_rev_id,
        },
      });
      setPendingProposal(null);
    } catch (err) {
      recordApiActivity({
        title:
          pendingProposal.source === "repair"
            ? "Apply repair proposal"
            : "Apply chat proposal",
        startedAt,
        error: err,
        details: { source: pendingProposal.source },
      });
      alert(`Applying proposal failed: ${err.message}`);
    }
  }

  function rejectProposal() {
    editorRef.current?.clearDiff();
    recordApiActivity({
      title:
        pendingProposal?.source === "repair"
          ? "Reject repair proposal"
          : "Reject chat proposal",
      summary: "proposal discarded",
      details: { source: pendingProposal?.source ?? null },
    });
    setPendingProposal(null);
  }

  function focusProposalOperation(op) {
    if (!op) {
      editorRef.current?.clearDiff();
      return;
    }
    const ids = proposalOpElementIds(op);
    editorRef.current?.highlightElements(ids);
  }

  async function handleRevert(revertedDiagram, newRevId) {
    dismissPreview();
    await applyDiagram(revertedDiagram);
    setCurrentRevId(newRevId);
    setValidationResult(null);
    setPendingProposal(null);
  }

  // called when user clicks a history card — loads it on canvas without committing
  function handlePreview(rev) {
    setPreviewRev(rev);
  }

  function dismissPreview() {
    if (!previewRev) return;
    setPreviewRev(null);
    // restore the current XML on the canvas
    if (xml) editorRef.current?.importXml(xml);
  }

  function handleExport() {
    if (!xml) return;
    const startedAt = performance.now();
    const blob = new Blob([xml], { type: "application/xml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "diagram.bpmn";
    a.click();
    URL.revokeObjectURL(url);
    recordApiActivity({
      title: "Export BPMN",
      summary: "diagram.bpmn",
      startedAt,
      details: { bytes: blob.size },
    });
  }

  // free-form repair for files that could not be parsed into the IR — the LLM
  // rewrites the raw XML (e.g. to dedupe ids); on success we re-enter normal mode
  async function handleXmlRepair() {
    if (!xml) return alert("Import a file first.");
    setRepairing(true);
    const startedAt = performance.now();
    try {
      const res = await repairXml(
        xml,
        null,
        interactionConfig(llmSettings.repair),
      );
      if (res.parseable && res.diagram) {
        const commit = await commitRevision(
          res.diagram,
          sessionId,
          "llm xml repair",
          "llm",
        );
        await applyDiagram(commit.diagram);
        setSessionId(commit.session_id);
        setCurrentRevId(commit.new_rev_id);
        setValidationResult(null);
        setEditorTab(EDITOR_TABS.DIAGRAM);
      } else {
        // still broken — keep the corrected source in the code view for inspection
        setXml(res.updated_xml);
        setParseError(
          res.parse_error ?? "Repaired XML still could not be parsed.",
        );
        setEditorTab(EDITOR_TABS.CODE);
      }
      recordApiActivity({
        title: "Repair XML",
        summary: res.parseable
          ? "file now parses as a diagram"
          : "still unparseable",
        startedAt,
        response: res,
        details: {
          parseable: res.parseable,
          parse_error: res.parse_error ?? null,
        },
      });
    } catch (err) {
      recordApiActivity({
        title: "Repair XML",
        startedAt,
        error: err,
      });
      alert(`XML repair failed: ${err.message}`);
    } finally {
      setRepairing(false);
    }
  }

  async function handleRepair() {
    if (!xml) return alert("Upload a diagram first.");
    if (allIssues.length === 0)
      return alert("Run validation first and select a diagram with issues.");
    setRepairing(true);
    const startedAt = performance.now();
    try {
      const res = await repairDiagram(
        xml,
        allIssues,
        mergeExperimentConfig(
          interactionConfig(llmSettings.repair),
          lastValidationTiers ? { tiers_enabled: lastValidationTiers } : {},
        ),
      );
      const proposedDiagram = res.updated_diagram;
      if (approvalMode === APPROVAL_MODES.AUTO) {
        const commit = await commitRevision(
          proposedDiagram,
          sessionId,
          "auto-approved repair proposal",
          "llm",
        );
        dismissPreview();
        await applyDiagram(commit.diagram);
        setSessionId(commit.session_id);
        setCurrentRevId(commit.new_rev_id);
        setValidationResult({
          is_valid: res.remaining_issues.length === 0,
          issues: res.remaining_issues,
          semantic_issues: [],
        });
        setPendingProposal(null);
      } else {
        handleDiagramProposal({
          source: "repair",
          baseDiagram: res.input_diagram,
          diagram: proposedDiagram,
          message: "accepted repair proposal",
          remainingIssues: res.remaining_issues,
          ops: res.applied_ops,
        });
      }
      recordApiActivity({
        title: "Repair proposal",
        summary: res.converged
          ? `converged in ${res.iterations} iteration${res.iterations === 1 ? "" : "s"}`
          : `${res.remaining_issues.length} issue${res.remaining_issues.length === 1 ? "" : "s"} remaining`,
        startedAt,
        response: res,
        details: {
          iterations: res.iterations,
          converged: res.converged,
          applied_ops: res.applied_ops?.length ?? 0,
          remaining_issues: res.remaining_issues?.length ?? 0,
          approval_mode: approvalMode,
        },
      });
    } catch (err) {
      recordApiActivity({
        title: "Repair proposal",
        startedAt,
        error: err,
        details: {
          issue_count: allIssues.length,
          approval_mode: approvalMode,
        },
      });
      alert(`Repair failed: ${err.message}`);
    } finally {
      setRepairing(false);
    }
  }

  const allIssues = [
    ...(validationResult?.issues ?? []),
    ...(validationResult?.semantic_issues ?? []),
  ];
  const errorCount = allIssues.filter((i) => i.severity === "error").length;
  // code-only mode: file loaded but not parseable into the IR
  const isUnparsed = parseError !== null;

  return (
    <div className="app-layout">
      {/* toolbar */}
      <div className="toolbar">
        <div className="toolbar-title">
          BPMN <span>AI</span> Validator
        </div>
        <div className="toolbar-caption">
          interactive BPMN validation and refinement
        </div>
        <div className="toolbar-spacer" />
        <div className="toolbar-settings">
          <button
            onClick={() => setLlmSettingsOpen((open) => !open)}
            className="toolbar-btn"
            title="Choose LLM providers and models"
            type="button"
          >
            <SettingsIcon /> Models
          </button>
          {llmSettingsOpen && (
            <LlmSettingsPanel
              settings={llmSettings}
              onChange={setLlmSettings}
              onClose={() => setLlmSettingsOpen(false)}
            />
          )}
        </div>
        <button
          onClick={() => setDarkMode((d) => !d)}
          className="toolbar-btn toolbar-btn--icon"
          title={darkMode ? "Switch to light mode" : "Switch to dark mode"}
        >
          {darkMode ? <SunIcon /> : <MoonIcon />}
        </button>
      </div>

      {/* main area */}
      <div className="main-area">
        {/* left sidebar */}
        <div
          className={`sidebar${!leftPanelOpen ? " sidebar--hidden" : ""}`}
          style={leftPanelOpen ? { width: sidebar.size } : undefined}
        >
          <div className="sidebar-header">
            <span>Diagram panels</span>
            <button
              className="panel-toggle-btn"
              onClick={() => setLeftPanelOpen(false)}
              title="Hide left panel"
              aria-label="Hide left panel"
            >
              <SidePanelIcon side="left" />
            </button>
          </div>

          <div
            className="panel-section"
            style={historyOpen ? { height: validationSplit.size } : { flex: 1 }}
          >
            <ValidationPanel
              issues={validationResult?.issues ?? []}
              semanticIssues={validationResult?.semantic_issues ?? []}
              isValid={validationResult?.is_valid}
              loading={validating}
              loadingLabel={
                VALIDATION_LOADING_LABELS[validationMode] ??
                "Running validation..."
              }
              errorCount={errorCount}
              modelUsed={validationResult?.model_used}
            />
          </div>

          {historyOpen && (
            <div
              className={`resize-handle-v${validationSplit.isDragging ? " dragging" : ""}`}
              onMouseDown={validationSplit.handleMouseDown}
            />
          )}

          <div
            className="panel-section"
            style={
              historyOpen ? { flex: 1 } : { flex: "0 0 auto", minHeight: 0 }
            }
          >
            <HistoryPanel
              sessionId={sessionId}
              currentRevId={currentRevId}
              viewDiagram={previewRev?.diagram ?? diagram}
              editorRef={editorRef}
              previewRevId={previewRev?.rev_id ?? null}
              onPreview={handlePreview}
              onRevert={handleRevert}
              onDismissPreview={dismissPreview}
              collapsed={!historyOpen}
              onToggleCollapse={() => setHistoryOpen((open) => !open)}
            />
          </div>
        </div>

        {!leftPanelOpen && (
          <div className="left-panel-rail">
            <button
              className="left-panel-rail-btn"
              onClick={() => setLeftPanelOpen(true)}
              title="Show validation and history panels"
            >
              <SidePanelIcon side="left" />
              <span>Panels</span>
            </button>
          </div>
        )}

        {/* horizontal resize handle */}
        {leftPanelOpen && (
          <div
            className={`resize-handle-h${sidebar.isDragging ? " dragging" : ""}`}
            onMouseDown={sidebar.handleMouseDown}
          />
        )}

        {/* editor pane — canvas actions and BPMN modeler */}
        <div className="editor-pane">
          <div className="diagram-action-bar">
            <div className="diagram-action-group diagram-action-group--file">
              <label className="diagram-action diagram-action--primary">
                <UploadIcon /> Import BPMN
                <input
                  type="file"
                  accept=".bpmn"
                  onChange={handleUpload}
                  style={{ display: "none" }}
                />
              </label>
              <button
                onClick={handleExport}
                disabled={!xml}
                className="diagram-action diagram-action--secondary"
                title="Export current diagram as .bpmn file"
              >
                <DownloadIcon /> Export
              </button>
            </div>
            <div className="diagram-action-spacer" />
            <div className="diagram-action-group diagram-action-group--validation">
              <button
                onClick={() => handleValidate(VALIDATION_MODES.STRUCTURAL)}
                disabled={!xml || validating || isUnparsed}
                className="diagram-action diagram-action--validate"
                title={
                  isUnparsed
                    ? "Unavailable: file is not a parseable diagram"
                    : "Check the diagram against the deterministic rule set"
                }
              >
                <CheckIcon /> Verify Rules
              </button>
              <button
                onClick={() => handleValidate(VALIDATION_MODES.DEEP)}
                disabled={!xml || validating || isUnparsed}
                className="diagram-action diagram-action--deep"
                title={
                  isUnparsed
                    ? "Unavailable: file is not a parseable diagram"
                    : "Check the rule set plus PM4Py Woflan formal soundness verification"
                }
              >
                <SparkIcon /> Formal Validate
              </button>
              <button
                onClick={() => handleValidate(VALIDATION_MODES.SEMANTIC)}
                disabled={!xml || validating || isUnparsed}
                className="diagram-action diagram-action--ai"
                title={
                  isUnparsed
                    ? "Unavailable: file is not a parseable diagram"
                    : "Run structural validation plus LLM semantic analysis"
                }
              >
                <SparkIcon /> Semantic LLM
              </button>
              <button
                onClick={isUnparsed ? handleXmlRepair : handleRepair}
                disabled={
                  !xml || repairing || (!isUnparsed && allIssues.length === 0)
                }
                className="diagram-action diagram-action--repair"
                title={
                  isUnparsed
                    ? "Ask the LLM to fix the broken XML so it parses as a diagram"
                    : "Propose repairs for current validation issues"
                }
              >
                <RepairIcon />{" "}
                {repairing ? "Repairing..." : isUnparsed ? "Fix XML" : "Repair"}
              </button>
            </div>
          </div>
          <div className="editor-tabs" role="tablist" aria-label="Editor views">
            <button
              type="button"
              role="tab"
              aria-selected={editorTab === EDITOR_TABS.DIAGRAM}
              className={`editor-tab${editorTab === EDITOR_TABS.DIAGRAM ? " editor-tab--active" : ""}`}
              onClick={() => setEditorTab(EDITOR_TABS.DIAGRAM)}
            >
              Diagram
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={editorTab === EDITOR_TABS.CODE}
              className={`editor-tab${editorTab === EDITOR_TABS.CODE ? " editor-tab--active" : ""}`}
              onClick={() => setEditorTab(EDITOR_TABS.CODE)}
            >
              Code
              {isUnparsed && (
                <span
                  className="editor-tab-dot"
                  title="File is not a parseable diagram"
                />
              )}
            </button>
          </div>
          <div className="editor-body">
            <div
              className={`editor-tab-pane${editorTab !== EDITOR_TABS.DIAGRAM ? " editor-tab-pane--hidden" : ""}`}
            >
              <BpmnEditor
                ref={editorRef}
                xml={isUnparsed ? null : xml}
                onXmlChange={handleXmlChange}
              />
              {isUnparsed && (
                <div className="diagram-unparsed-overlay">
                  <div className="diagram-unparsed-card">
                    <div className="diagram-unparsed-title">
                      This file can&apos;t be shown as a diagram
                    </div>
                    <div className="diagram-unparsed-detail">{parseError}</div>
                    <div className="diagram-unparsed-hint">
                      Open the{" "}
                      <button
                        type="button"
                        className="link-button"
                        onClick={() => setEditorTab(EDITOR_TABS.CODE)}
                      >
                        Code
                      </button>{" "}
                      tab to inspect the source, or run <strong>Fix XML</strong>{" "}
                      to let the LLM repair it.
                    </div>
                  </div>
                </div>
              )}
            </div>
            <div
              className={`editor-tab-pane${editorTab !== EDITOR_TABS.CODE ? " editor-tab-pane--hidden" : ""}`}
            >
              <CodeView code={xml} language="xml" error={parseError} />
            </div>
          </div>
        </div>

        <>
          {chatOpen && (
            <div
              className={`resize-handle-h resize-handle-h--right${assistantPanel.isDragging ? " dragging" : ""}`}
              onMouseDown={assistantPanel.handleMouseDown}
            />
          )}
          <aside
            className={`assistant-panel${!chatOpen ? " assistant-panel--hidden" : ""}`}
            style={chatOpen ? { width: assistantPanel.size } : undefined}
          >
            <div className="assistant-panel-header">
              <div>
                <div className="assistant-panel-title">
                  <ChatIcon /> AI diagram assistant
                </div>
                <div className="assistant-panel-subtitle">
                  Ask for BPMN improvements or refinements
                </div>
              </div>
              <button
                className="panel-toggle-btn"
                onClick={() => setChatOpen(false)}
                title="Hide AI chat"
                aria-label="Hide AI chat"
              >
                <SidePanelIcon side="right" />
              </button>
            </div>
            <div
              className="assistant-tabs"
              role="tablist"
              aria-label="Assistant views"
            >
              <button
                type="button"
                role="tab"
                aria-selected={assistantTab === ASSISTANT_TABS.CHAT}
                className={`assistant-tab${assistantTab === ASSISTANT_TABS.CHAT ? " assistant-tab--active" : ""}`}
                onClick={() => setAssistantTab(ASSISTANT_TABS.CHAT)}
              >
                Chat
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={assistantTab === ASSISTANT_TABS.LOGS}
                className={`assistant-tab${assistantTab === ASSISTANT_TABS.LOGS ? " assistant-tab--active" : ""}`}
                onClick={() => setAssistantTab(ASSISTANT_TABS.LOGS)}
              >
                Logs
                {logEntries.length > 0 && <span>{logEntries.length}</span>}
              </button>
            </div>
            <div
              className={`assistant-tab-pane${assistantTab !== ASSISTANT_TABS.CHAT ? " assistant-tab-pane--hidden" : ""}`}
            >
              <ChatPanel
                ir={diagram}
                issues={allIssues}
                sessionId={sessionId}
                onIrUpdate={handleChatUpdate}
                onDiagramProposal={handleDiagramProposal}
                SendIcon={SendIcon}
                showHeader={false}
                approvalMode={approvalMode}
                onApprovalModeChange={setApprovalMode}
                pendingProposal={pendingProposal}
                onApplyProposal={applyAcceptedProposal}
                onRejectProposal={rejectProposal}
                onFocusProposalOp={focusProposalOperation}
                config={interactionConfig(llmSettings.chat)}
                onActivity={recordApiActivity}
                disabled={isUnparsed}
                disabledReason="Fix the file (Fix XML) so it parses before chatting about the diagram."
              />
            </div>
            <div
              className={`assistant-tab-pane${assistantTab !== ASSISTANT_TABS.LOGS ? " assistant-tab-pane--hidden" : ""}`}
            >
              <LogsPanel
                entries={logEntries}
                onClear={() => setLogEntries([])}
              />
            </div>
          </aside>
        </>

        {!chatOpen && (
          <div className="assistant-rail">
            <button
              className="assistant-rail-btn"
              onClick={() => setChatOpen(true)}
              title="Show AI diagram assistant"
            >
              <ChatIcon />
              <span>AI Chat</span>
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
