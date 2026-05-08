"""``-prj`` fast-path: read script + variables + sheets from sibling folder.

QlikView optionally exports ``<basename>-prj/`` next to ``<basename>.qvw``
when "Save as project" is enabled. The folder contains ``LoadScript.txt``
plus per-object XML files. When present, this is much faster and more
reliable than container parsing — text files instead of zlib-compressed
custom binary blocks.

Phase 1 implementation: detect ``LoadScript.txt`` and return its contents
with a recorded encoding. XML object decoding (variables, sheets) lands in
Phase 1.5 since the wire format is straightforward but voluminous.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mcp_qlikview.parser.blocks.script import ScriptDecodeResult, _decode_script_bytes


@dataclass(frozen=True)
class PrjBundle:
    """Outputs from a ``-prj`` sibling directory."""

    script: ScriptDecodeResult
    """Load-script text + actual encoding used."""

    prj_dir: Path
    """Resolved path to the ``-prj`` folder (absolute)."""


def try_prj(qvw_path: Path) -> PrjBundle | None:
    """Return a :class:`PrjBundle` when the ``-prj`` sibling exists, else ``None``.

    Args:
        qvw_path: Path to a ``.qvw`` file (need not be absolute).

    Returns:
        ``PrjBundle`` when both the sibling directory and ``LoadScript.txt``
        are present; ``None`` when the fast path is unavailable. Callers fall
        back to container parsing in the ``None`` case — the QVW always
        contains an authoritative copy of the same data.

    Notes:
        - We do **not** raise on a malformed ``-prj`` folder (e.g. missing
          ``LoadScript.txt``). The fast path is opportunistic; the container
          path is the source of truth.
        - The ``LoadScript.txt`` encoding follows the same §4.3 chain as
          binary script extraction. ``ScriptDecodeResult.source`` is set
          to ``"prj"`` by callers.
    """
    prj_dir = qvw_path.with_name(f"{qvw_path.stem}-prj")
    if not prj_dir.is_dir():
        return None

    script_path = prj_dir / "LoadScript.txt"
    if not script_path.is_file():
        return None

    raw = script_path.read_bytes()
    decoded = _decode_script_bytes(raw)
    return PrjBundle(script=decoded, prj_dir=prj_dir.resolve())
