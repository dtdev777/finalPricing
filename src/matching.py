"""Product and store identity helpers shared by the pipeline and the assistant."""
import re
import unicodedata

# Canonical store names mapped to the spellings people type (accents removed).
STORE_ALIASES = {
    "FPT Shop": ("fpt shop", "fptshop", "fpt"),
    "CellphoneS": ("cellphones", "cellphone s", "cellphone", "cps"),
    "Hoang Ha Mobile": ("hoang ha mobile", "hoanghamobile", "hoang ha"),
    "MobileCity": ("mobilecity", "mobile city"),
    "TGDD": ("the gioi di dong", "thegioididong", "tgdd"),
    "TopZone": ("topzone", "top zone"),
    "Viettel Store": ("viettel store", "viettelstore", "viettel"),
    "Apple Store": ("apple store", "applestore", "apple"),
}

_MODEL = re.compile(
    r"(?:\b(?:iphone|ip)\s*|\b)(?:(1[1-9])(e)?(?![0-9])|(air)\b)\s*(pro\s*max|pro|plus|mini)?"
)
_CAPACITY = re.compile(r"(?<![0-9])(\d{2,4}|[12])\s*(gb|tb)\b")
# People often drop the unit: "16 pro 256".
_BARE_CAPACITY = re.compile(r"(?<![0-9])(64|128|256|512)(?![0-9])")


def normalize_text(value: str) -> str:
    """Lowercase and strip Vietnamese accents so accented and plain spellings match."""
    # U+0111 (d with stroke) has no Unicode decomposition, so replace it explicitly.
    value = value.lower().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", value)
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split())


def parse_model(text: str) -> str | None:
    """Return a model key such as 'iphone-16-pro-max', 'iphone-16e' or 'iphone-air'."""
    match = _MODEL.search(normalize_text(text))
    if not match:
        return None
    number, e_suffix, air, variant = match.groups()
    parts = ["iphone", air or f"{number}{e_suffix or ''}"]
    if variant:
        parts.append("pro-max" if variant.startswith("pro") and variant.endswith("max") else variant)
    return "-".join(parts)


def parse_capacity(text: str) -> str | None:
    """Return a capacity such as '256gb' or '1tb'."""
    normalized = normalize_text(text)
    if match := _CAPACITY.search(normalized):
        return f"{match[1]}{match[2]}"
    match = _BARE_CAPACITY.search(normalized)
    return f"{match[1]}gb" if match else None


def parse_store(text: str) -> str | None:
    normalized = f" {normalize_text(text)} "
    for store, aliases in STORE_ALIASES.items():
        if any(f" {alias} " in normalized for alias in aliases):
            return store
    return None


def build_match_key(model: str, capacity: str) -> str:
    """Join a model key and a capacity into the product_match_key used across tables."""
    return f"{model}-{capacity}"
