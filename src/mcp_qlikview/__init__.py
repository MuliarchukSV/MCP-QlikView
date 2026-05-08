"""mcp-qlikview — MCP server for QlikView QVW files."""

from __future__ import annotations

import warnings

# Spec §4.3 mandates ``TableSummary.schema`` and ``SearchHit.schema`` as wire
# field names; pydantic v2 warns when these shadow ``BaseModel.schema()``.
# Suppressing the warning on import keeps the runtime log clean — the wire
# contract is non-negotiable.
warnings.filterwarnings(
    "ignore",
    message=r"Field name \"schema\".*",
    category=UserWarning,
)

__version__ = "0.1.0"
