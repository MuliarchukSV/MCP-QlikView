"""
QVW -prj folder fast-path.

If a sibling '<name>-prj/' folder exists next to '<name>.qvw', QlikView
stores the load script as plain text and metadata as XML files there.
This is faster and more reliable than binary extraction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PrjBundle:
    script_text: str
    xml_files: dict[str, str] = field(default_factory=dict)  # filename → content


def try_prj(qvw_path: Path) -> PrjBundle | None:
    """
    Check for a sibling -prj folder and read its contents.
    Returns None if the folder does not exist.
    """
    prj_dir = qvw_path.parent / (qvw_path.stem + "-prj")
    if not prj_dir.is_dir():
        return None

    script_file = prj_dir / "LoadScript.txt"
    if not script_file.exists():
        return None

    try:
        script_text = script_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    xml_files: dict[str, str] = {}
    for xml_path in prj_dir.glob("*.xml"):
        try:
            xml_files[xml_path.name] = xml_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

    return PrjBundle(script_text=script_text, xml_files=xml_files)
