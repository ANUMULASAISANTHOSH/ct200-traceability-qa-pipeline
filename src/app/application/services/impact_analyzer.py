"""Deterministic heuristic impact analyzer for regulatory manual changes."""

import re
from typing import List, Set

IMPACT_DISCLAIMER = "Impact tags are automated heuristic review hints, not medical or regulatory conclusions."

# Keywords signaling safety thresholds, alarms, hazards, or critical conditions
SAFETY_KEYWORDS: Set[str] = {
    "alarm",
    "alarms",
    "warning",
    "warnings",
    "caution",
    "cautions",
    "hazard",
    "hazards",
    "danger",
    "safety",
    "overpressure",
    "error",
    "errors",
    "fault",
    "faults",
    "fail",
    "failure",
    "cutoff",
    "deflation",
    "deflate",
    "emergency",
    "stop",
    "interlock",
    "abort",
    "aborts",
    "threshold",
    "thresholds",
    "critical",
    "safeguard",
    "limit",
    "limits",
    "tolerance",
}

# Keywords signaling new or altered features, communication protocols, data export, or error codes
FEATURE_KEYWORDS: Set[str] = {
    "bluetooth",
    "sync",
    "export",
    "csv",
    "firmware",
    "protocol",
    "interface",
    "profile",
    "profiles",
    "mode",
    "modes",
    "communication",
    "usb",
    "wireless",
    "feature",
    "features",
    "capability",
    "capabilities",
    "added",
    "removed",
    "support",
    "supports",
}


def extract_numbers(text: str) -> Set[str]:
    """Extract normalized numeric tokens from text."""
    # Matches integers, decimals, and percentages
    return set(re.findall(r"\b\d+(?:\.\d+)?%?\b", text))


def classify_impact(pinned_text: str, latest_text: str, diff_text: str) -> List[str]:
    """Deterministically classify the nature of text changes using transparent heuristics.
    
    Categories:
    - NUMERICAL_SPECIFICATION_CHANGED: Numeric values, tolerances, units, or percentages differ.
    - SAFETY_OR_ALARM_MODIFIED: Wording involving alarms, warnings, error codes, or safety limits changed.
    - FEATURE_OR_BEHAVIOR_CHANGED: Functional features, interfaces, data export, or communication altered.
    - WORDING_ONLY_CHANGED: Editorial or phrasing adjustments without detected specification/safety changes.
    """
    tags: List[str] = []

    # Extract diff delta lines (only additions and deletions)
    diff_lines = [
        line[1:].strip()
        for line in diff_text.splitlines()
        if (line.startswith("+") or line.startswith("-"))
        and not line.startswith("+++")
        and not line.startswith("---")
    ]
    delta_text = " ".join(diff_lines).lower()

    # 1. Check for numeric / specification changes
    nums_pinned = extract_numbers(pinned_text)
    nums_latest = extract_numbers(latest_text)
    has_number_in_diff = bool(re.search(r"\b\d+(?:\.\d+)?%?\b", delta_text))
    if nums_pinned != nums_latest or has_number_in_diff:
        tags.append("NUMERICAL_SPECIFICATION_CHANGED")

    # 2. Check for safety / alarm wording changes
    has_safety = any(
        re.search(rf"\b{re.escape(kw)}\b", delta_text) for kw in SAFETY_KEYWORDS
    )
    if has_safety:
        tags.append("SAFETY_OR_ALARM_MODIFIED")

    # 3. Check for feature / behavior changes (keywords or error code pattern like E6)
    has_feature_kw = any(
        re.search(rf"\b{re.escape(kw)}\b", delta_text) for kw in FEATURE_KEYWORDS
    )
    has_error_code = bool(re.search(r"\be\d+\b", delta_text))
    if has_feature_kw or has_error_code:
        tags.append("FEATURE_OR_BEHAVIOR_CHANGED")

    # 4. Wording-only fallback
    if not tags:
        tags.append("WORDING_ONLY_CHANGED")

    return tags
