"""
Variable extractor — parses SET/LET statements from the load script.
For -prj files, also parses Variables.xml if present.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from mcp_qlikview.models import Variable, VariablesBundle
from mcp_qlikview.parser.container import QvwContainer
from mcp_qlikview.parser.prj import try_prj
from mcp_qlikview.parser.script import extract_script

# Qlik reserved system variables (start with $)
_RESERVED_PREFIX = ("$", "ThousandSep", "DecimalSep", "MoneyFormat",
                    "TimeFormat", "DateFormat", "TimestampFormat",
                    "MonthNames", "LongMonthNames", "DayNames", "LongDayNames",
                    "MoneyThousandSep", "MoneyDecimalSep")

_SET_RE = re.compile(
    r"^[ \t]*(SET|LET)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(.+?)(?:;|$)",
    re.MULTILINE | re.IGNORECASE,
)


def _is_reserved(name: str) -> bool:
    return name.startswith("$") or name in _RESERVED_PREFIX


def _from_script(script: str) -> dict[str, Variable]:
    variables: dict[str, Variable] = {}
    for m in _SET_RE.finditer(script):
        name = m.group(2).strip()
        expression = m.group(3).strip().rstrip(";").strip()
        if name and expression:
            variables[name] = Variable(
                name=name,
                expression=expression,
                is_reserved=_is_reserved(name),
            )
    return variables


def _from_xml(xml_content: str) -> dict[str, Variable]:
    variables: dict[str, Variable] = {}
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return variables

    for var_elem in root.iter("Variable"):
        name_el = var_elem.find("Name")
        val_el = var_elem.find("RawValue")
        if name_el is None or val_el is None:
            continue
        name = (name_el.text or "").strip()
        expression = (val_el.text or "").strip()
        comment_el = var_elem.find("Comment")
        comment = (comment_el.text or "").strip() if comment_el is not None else None
        if name:
            variables[name] = Variable(
                name=name,
                expression=expression,
                is_reserved=_is_reserved(name),
                comment=comment or None,
            )
    return variables


def extract_variables(container: QvwContainer) -> VariablesBundle:
    """Extract variables from a QVW container."""
    qvw_name = container.path.stem
    variables: dict[str, Variable] = {}

    # Try -prj XML first
    prj = try_prj(container.path)
    if prj is not None:
        for fname, content in prj.xml_files.items():
            if "variable" in fname.lower():
                variables.update(_from_xml(content))

    # Fallback / supplement: parse SET/LET from script
    try:
        bundle = extract_script(container)
        variables.update(_from_script(bundle.script))
    except Exception:
        pass

    return VariablesBundle(qvw=qvw_name, variables=variables)
