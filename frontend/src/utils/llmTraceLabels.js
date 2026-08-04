const TASK_LABELS = {
  repair: "Repair",
  semantic_validation: "Semantic validation",
  refinement: "Chat refinement",
  ir_conversion: "IR conversion",
  summary: "Summary",
  simple_query: "Simple query",
};

export function llmTraceTitle(parentTitle, trace, index, total) {
  const taskLabel = TASK_LABELS[trace?.task] ?? parentTitle;
  const callNumber = total > 1 ? ` ${index + 1}` : "";
  return `${taskLabel} LLM call${callNumber}`;
}
