"""
Sheet extractor — reads sheet/object metadata from -prj XML files.
For QVW files without -prj, returns an empty list (sheets are
embedded in block 0 binary; Phase 1 does not decode them).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from mcp_qlikview.models import Sheet, SheetObject
from mcp_qlikview.parser.container import QvwContainer
from mcp_qlikview.parser.prj import try_prj

_OBJECT_TYPE_MAP = {
    "lb": "table",
    "ch": "chart",
    "tb": "table",
    "tx": "text",
    "mb": "button",
    "sl": "input",
}


def _parse_sheet_xml(xml_content: str) -> list[Sheet]:
    sheets: list[Sheet] = []
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return sheets

    for order, sheet_el in enumerate(root.iter("Sheet")):
        sheet_id = sheet_el.get("ID", f"sheet_{order}")
        title_el = sheet_el.find("Caption/v")
        title = (title_el.text or sheet_id) if title_el is not None else sheet_id

        objects: list[SheetObject] = []
        for obj_el in sheet_el.iter("Object"):
            obj_id = obj_el.get("ID", "")
            obj_type_raw = obj_el.get("Type", "other").lower()[:2]
            obj_type = _OBJECT_TYPE_MAP.get(obj_type_raw, "other")
            caption_el = obj_el.find("Caption/v")
            caption = (caption_el.text or "").strip() if caption_el is not None else None

            objects.append(SheetObject(
                id=obj_id,
                type=obj_type,  # type: ignore[arg-type]
                caption=caption or None,
            ))

        sheets.append(Sheet(
            id=sheet_id,
            title=title,
            order=order,
            objects=objects,
        ))

    return sheets


def extract_sheets(container: QvwContainer) -> list[Sheet]:
    """
    Extract sheets from -prj XML if available.
    Returns empty list for binary-only QVW files (Phase 1 limitation).
    """
    prj = try_prj(container.path)
    if prj is None:
        return []

    sheets: list[Sheet] = []
    for fname, content in prj.xml_files.items():
        if "sheet" in fname.lower() or "layout" in fname.lower():
            sheets.extend(_parse_sheet_xml(content))

    return sheets
