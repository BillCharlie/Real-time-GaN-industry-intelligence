import re
from typing import List, Tuple


MACRO_CATEGORIES = ("industry", "stock", "academic")
TECH_CATEGORIES = ("low_power", "high_power", "high_frequency", "materials", "packaging", "other")


_MACRO_RULES = {
    "stock": [
        "stock",
        "shares",
        "nasdaq",
        "nyse",
        "earnings",
        "guidance",
        "investor",
        "q1",
        "q2",
        "q3",
        "q4",
    ],
    "academic": [
        "ieee",
        "nature",
        "apl",
        "applied physics letters",
        "journal",
        "conference",
        "paper",
        "doi",
        "arxiv",
        "preprint",
    ],
    "industry": [
        "press release",
        "launch",
        "shipment",
        "fab",
        "foundry",
        "factory",
        "product",
        "design win",
        "automotive",
        "charger",
        "data center",
    ],
}

_TECH_RULES = {
    "low_power": [
        "adapter",
        "consumer",
        "usb-c",
        "phone charger",
        "notebook",
        "portable",
        "below 100w",
        "below 200w",
    ],
    "high_power": [
        "inverter",
        "ev",
        "traction",
        "motor drive",
        "grid",
        "server psu",
        "3kw",
        "5kw",
        "10kw",
        "high power",
    ],
    "high_frequency": [
        "rf",
        "mmwave",
        "microwave",
        "5g",
        "6g",
        "radar",
        "satellite",
        "high-frequency",
        "high frequency",
        "ghz",
    ],
    "materials": [
        "epitaxy",
        "substrate",
        "wafer",
        "defect",
        "crystal",
        "gan-on-sic",
        "gan-on-si",
        "material",
    ],
    "packaging": [
        "package",
        "thermal",
        "co-packaged",
        "module",
        "integration",
        "parasitic",
    ],
}

_TAG_KEYWORDS = [
    "gan",
    "sic",
    "hemt",
    "mosfet",
    "power electronics",
    "fast charger",
    "automotive",
    "data center",
    "renewable",
    "grid",
]


def _pick_by_rules(text: str, rules: dict, default: str) -> str:
    lowered = text.lower()
    for label, keywords in rules.items():
        if any(keyword in lowered for keyword in keywords):
            return label
    return default


def classify_text(title: str, summary: str | None = None) -> Tuple[str, str, List[str]]:
    merged = f"{title or ''}\n{summary or ''}"
    normalized = re.sub(r"\s+", " ", merged)
    macro = _pick_by_rules(normalized, _MACRO_RULES, "industry")
    tech = _pick_by_rules(normalized, _TECH_RULES, "other")
    tags = [tag for tag in _TAG_KEYWORDS if tag in normalized.lower()]
    return macro, tech, tags

