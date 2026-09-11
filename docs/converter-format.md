# converter format

This doc covers the IR layer: the canonical internal representation, the candidate formats used for LLM I/O and comparison experiments, and the protocol they share.

---

## the two-role model

The IR layer plays two roles:

1. **User I/O** — converting uploaded BPMN XML into an internal form the backend can validate, repair, and version, and back again on export.
2. **LLM I/O** — serialising that internal form into compact text for prompts, and parsing the model's response back into the same internal form.

Both roles use the same canonical IR, the Pydantic-typed `BpmnDiagram`. Only the converter reading and writing the bytes changes between them.

```
BPMN XML  ──[ PydanticConverter ]──▶   canonical IR  ──[ YamlConverter ]──▶ YAML
(user)                                       │                              (LLM in/out)
                                             │
                                    tier 1/2/3 validators
                                    repair + history
```

---

## the `DiagramConverter` protocol

`backend/app/model/protocol.py`:

```python
class DiagramConverter(Protocol):
    def parse(self, xml_bytes: bytes) -> BpmnDiagram: ...
    def serialize(self, diagram: BpmnDiagram) -> bytes: ...
```

Every converter, regardless of its on-wire format, produces and consumes the same canonical IR. This keeps validation, repair, and history format-agnostic.

`parse` returns `BpmnDiagram`. The protocol also exposes `parse_with_diagnostics`, which returns the diagram together with unsupported-element diagnostics.

---

## canonical IR

The canonical IR lives in `backend/app/model/schema.py`:

| Class | Role |
|---|---|
| `BpmnDiagram` | top-level container (definitions id, target namespace, namespaces, processes) |
| `BpmnProcess` | one per `<process>`; carries `is_executable`, flow nodes and sequence flows; also has a schema-only `pools` field |
| `FlowNode` | any gateway, task, or event; typed via `FlowNodeType` enum |
| `EventDefinition` | what makes an event a timer, message or error one; typed via `EventDefinitionType` |
| `SequenceFlow` | edge with optional `name` and `condition_expression` |
| `Pool`, `Lane` | schema-only containers; the BPMN XML converter does not parse or serialize pools or lanes |

`FlowNode.extra: dict` preserves additional attributes on supported flow nodes. It does not preserve arbitrary XML children or extend the supported element set. Unsupported children are reported by import diagnostics.

---

## canonical IR — BPMN 2.0 coverage matrix

Authoritative table for what the canonical IR and the default `PydanticConverter` support today. Other docs should link here rather than duplicate.

Legend: ✅ full · ◐ partial (schema or attributes preserved via `extra`, but not semantically modelled) · ☐ not supported

| Category | Element | Status | Notes |
|---|---|---|---|
| Process | `process` (id, name, `isExecutable`) | ✅ | |
| Events | `startEvent` | ✅ | |
| Events | `endEvent` | ✅ | |
| Events | `intermediateCatchEvent` | ◐ | element and its event definitions modelled; what the definition nests is reported as unsupported |
| Events | `intermediateThrowEvent` | ◐ | same as above |
| Events | `timerEventDefinition`, `messageEventDefinition` and the other eight | ◐ | kind and id kept, attributes via `extra`; `messageRef` is not dereferenced |
| Events | `boundaryEvent` | ☐ | not in `FlowNodeType`; `attachedToRef` not modelled |
| Tasks | `task`, `userTask`, `serviceTask`, `scriptTask`, `sendTask`, `receiveTask`, `manualTask` | ✅ | |
| Tasks | `businessRuleTask` | ☐ | not in `FlowNodeType` |
| Activities | `subProcess` | ◐ | outer element parsed; nested flow nodes / sequence flows **inside** the sub-process body are not recursively parsed |
| Activities | `callActivity` | ✅ | parsed as a task-shaped node; referenced process is not dereferenced |
| Gateways | `exclusiveGateway`, `parallelGateway`, `inclusiveGateway`, `eventBasedGateway`, `complexGateway` | ✅ | |
| Flows | `sequenceFlow` (id, source, target, name) | ✅ | |
| Flows | `conditionExpression` on a sequence flow | ✅ | |
| Flows | `messageFlow` | ☐ | not imported or serialized; its enclosing collaboration is reported as unsupported |
| Swimlanes | `laneSet` / `lane` / `flowNodeRef` | ☐ | lane sets are reported as unsupported; schema-only `Pool`/`Lane` classes do not provide XML coverage |
| Collaboration | `collaboration`, `participant` (pool) | ☐ | collaborations and their contents are not imported or serialized |
| Data | `dataObject`, `dataObjectReference`, `dataStoreReference` | ☐ | |
| Data | `dataInputAssociation`, `dataOutputAssociation` | ☐ | |
| Artifacts | `textAnnotation`, `group`, `association` | ☐ | |
| Visualization | `bpmndi:BPMNDiagram` (DI) | ✅ | node bounds, flow waypoints and label bounds are preserved for supported elements; missing geometry gets a fallback layout. XML wrapper IDs and formatting may change; pool and lane geometry is unsupported |

### closing the gaps (priority order)

1. **Sub-process recursion** — parse/serialise nested flow nodes so structural validation works inside sub-processes. Blocks meaningful validation of hierarchical models.
2. **Collaboration and swimlane coverage** — implement XML import/export together with explicit formal-checker limits. The current dataset probe excludes unsupported collaborations and lanes at the lossless-import gate (`lossy_import`); it does not assign a `multi_process` verdict.
3. **Data objects / artifacts** — useful for tier-2 data-flow checks (PM4Py); lower priority for control-flow validation.
4. **Event definition payloads** — the trigger's kind is modelled, its schedule is not.

---

## IR–validation decoupling

Validation, repair, and history all operate on `BpmnDiagram`, never on whichever format was loaded. That is the commitment the canonical-IR pattern makes, and it has three consequences:

- Adding a candidate IR such as YAML does not fragment the validation code. The same R001–R008 rules, the same repair dispatcher, and the same history service work regardless of which converter ingested the bytes.
- A candidate IR that cannot round-trip some element through the canonical IR is a format with reduced coverage, not a reason to branch validation.
- `FlowNode.extra` is the escape hatch: unknown XML attributes survive a round-trip even through converters that do not understand them.

The trade is deliberate — less flexibility inside the model layer, more outside it.

---

## candidate IRs

Additional formats used primarily for LLM I/O and for thesis-level comparison experiments. Each candidate is still a `DiagramConverter` — it reads/writes bytes to/from the canonical IR.

| Key | Status | Purpose | Notes |
|---|---|---|---|
| `pydantic` (BPMN XML) | ✅ built | user upload/export; canonical-parity baseline | the converter connecting the user's world (XML) to the canonical IR |
| `pydantic_json` | ✅ built | canonical IR as JSON for LLM I/O | 1:1 with the Pydantic schema; the "no information loss" baseline |
| `yaml` | ✅ built | **novel thesis contribution** | canonical IR as deterministic YAML; measures token cost and edit success vs JSON |
| `mermaid` | ✅ built | token-efficiency reference | Mermaid flowchart plus compact IR metadata for lossless supported-subset round-trip |
| `compact_json` | ✅ built | ablation | minimal-key JSON; isolates whether verbose keys hurt LLM accuracy |

The active candidate IR is selected per request via `ExperimentConfig.ir_format`.

### comparison axes

Each candidate IR is evaluated along:

- **Token cost** — input and output tokens for a fixed corpus of diagrams, with `pydantic` as baseline.
- **Round-trip fidelity** — fraction of canonical-IR elements that survive a round trip through the candidate.
- **Edit success** — under `repair_mode = atomic`, the percentage of LLM-emitted `EditOp` plans that apply cleanly.
- **Generation quality** — GED / PME similarity to ground-truth when the LLM generates a diagram from a textual description.

Detailed protocol and results live in [`experiments.md`](experiments.md).

---

## round-trip invariant

Every converter must satisfy:

```
serialize(parse(x)) ≡ x   for every valid input x in the converter's supported subset
```

For the `pydantic` (BPMN XML) converter, BPMN XML -> `BpmnDiagram` -> BPMN XML preserves semantics within the supported subset, modulo XML formatting and generated wrapper identifiers. Existing node, edge and label geometry is preserved; missing geometry is generated.

For a candidate IR (say `yaml`) the invariant is two-sided:

- `yaml -> canonical -> yaml` round-trips cleanly.
- `canonical -> yaml -> canonical` preserves every element in the candidate's supported subset. Elements outside the subset are lost; this is a property of the candidate, not a bug in the protocol.

The round-trip test is a hard gate on every `DiagramConverter` PR.

---

## registry

`backend/app/model/registry.py` maps short names to converter instances:

```python
from app.model.registry import get_converter

converter = get_converter()             # uses DIAGRAM_CONVERTER env var (default: "pydantic")
converter = get_converter("pydantic")   # explicit
```

All five built-in converters are registered when the registry module is imported. Custom converters use explicit `register()` calls.

For user upload/export, the active converter is still resolved from `DIAGRAM_CONVERTER` via `get_converter()`.

For LLM-facing validation, chat, and repair payloads,
`ExperimentConfig.ir_format` selects the active candidate per request.
`pydantic` keeps the JSON-object input shape; `pydantic_json`, `yaml`,
`mermaid`, and `compact_json` serialize the input `diagram` field as a string.
LLM responses use the common structured envelope and carry every complete
replacement diagram as a string in `result.ir` or `result.diagram`. The
selected converter parses that string before it can become canonical state.

---

## adding a new converter

1. Create `backend/app/model/formats/my_format.py`.
2. Implement `parse(self, xml_bytes: bytes) -> BpmnDiagram` and `serialize(self, diagram: BpmnDiagram) -> bytes`. Both sides must be the canonical `BpmnDiagram` — not a format-specific type.
3. Register it (in `backend/app/main.py` or a dedicated startup module):
   ```python
   from app.model.registry import register
   from app.model.formats.my_format import MyFormatConverter
   register("my_format", MyFormatConverter())
   ```
4. Add round-trip tests: canonical -> my_format -> canonical for every element the format claims to support.
5. If the format declares reduced coverage, extend the **candidate IRs** table above with the specifics.
6. Select it per request via `ExperimentConfig.ir_format`, or globally via `DIAGRAM_CONVERTER=my_format` in `.env`.
