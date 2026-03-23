# validation rules

All deterministic rules live in `backend/app/validation/rules.py`. They run synchronously before any LLM call and require no external dependencies.

## severity levels

| Level | Meaning |
|---|---|
| `error` | Diagram is structurally invalid; must be fixed before repair/export |
| `warning` | Potentially problematic but not necessarily wrong |
| `info` | Informational; no action required |

## rules

| ID | Severity | Condition |
|---|---|---|
| R001 | error | process has no start event |
| R002 | warning | process has more than one start event |
| R003 | error | process has no end event |
| R004 | error | start event has no outgoing sequence flow |
| R005 | error | end event has no incoming sequence flow |
| R006 | warning | element has neither incoming nor outgoing flows (fully disconnected) |
| R007 | warning | gateway has fewer than 2 outgoing flows |
| R008 | warning | exclusive gateway has more than one outgoing flow without a condition expression |
| R009 | error | sequence flow references an unknown source element ID |
| R010 | error | sequence flow references an unknown target element ID |
| R011 | error | duplicate element ID within a process |

## adding a new rule

1. Add a `_check_*` function in `rules.py` following the existing pattern
2. Call it from `validate()`
3. Assign the next available rule ID (R012, …)
4. Add a test case in `tests/test_validation_rules.py`
