# converter format

## the DiagramConverter protocol

`backend/app/model/protocol.py` defines the contract every representation format must satisfy:

```python
class DiagramConverter(Protocol):
    def parse(self, xml_bytes: bytes) -> Any: ...
    def serialize(self, diagram: Any) -> bytes: ...
```

`parse` turns raw BPMN XML into whatever internal object this format uses.
`serialize` turns that object back into valid BPMN XML bytes.
The round-trip property must hold: `serialize(parse(xml))` must produce semantically equivalent BPMN.

## built-in converter: `pydantic`

`backend/app/model/formats/pydantic_ir.py` — `PydanticConverter`

Parses BPMN XML into typed Pydantic models (`BpmnDiagram`, `BpmnProcess`, `FlowNode`, `SequenceFlow`). This is the default and is what the validation rules and API endpoints work with.

## registry

`backend/app/model/registry.py` maps short names to converter instances.

```python
from app.model.registry import get_converter

converter = get_converter()           # uses DIAGRAM_CONVERTER from .env (default: "pydantic")
converter = get_converter("pydantic") # explicit
```

The pydantic converter is auto-registered at import time. Additional converters can be registered at application startup.

## adding a new converter

1. Create `backend/app/model/formats/my_format.py`
2. Implement `parse(self, xml_bytes: bytes)` and `serialize(self, diagram: Any) -> bytes`
3. Register it in `backend/app/main.py` (or its own module imported at startup):
   ```python
   from app.model.registry import register
   from app.model.formats.my_format import MyConverter
   register("my_format", MyConverter())
   ```
4. Set `DIAGRAM_CONVERTER=my_format` in `.env`

No other code needs to change — the API routes and validation layer call `get_converter()` and are format-agnostic.

## note on schema coupling

The validation rules in `validation/rules.py` currently depend on `BpmnDiagram` from `model/schema.py`. If you introduce a converter that produces a different object type, you will also need a corresponding validation adapter. Keep this in mind when designing new formats.
