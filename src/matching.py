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
    r"(?:\b(?:iphone|ip)\s*|\b)(?:(1[1-9])(e)?(?![0-9])|(air|duo)\b)\s*(?:(pro\s*max|pm|pro|plus|mini)(?![a-z]))?"
)
_CAPACITY = re.compile(r"(?<![0-9])(\d{2,4}|[12])\s*(gb|tb)\b")
# People often drop the unit: "16 pro 256".
_BARE_CAPACITY = re.compile(r"(?<![0-9])(64|128|256|512)(?![0-9])")
# Used, refurbished, display and grey-import phones are not comparable with new VN/A stock.
_NOT_NEW_VNA = re.compile(r"\b(cpo|cu|like new|99|trung bay|da kich hoat|nhat ban|quoc te|my|lock)\b")
FIRST_GENERATION = 15


def normalize_text(value: str) -> str:
    """Lowercase and strip Vietnamese accents so accented and plain spellings match."""
    # U+0111 (d with stroke) has no Unicode decomposition, so replace it explicitly.
    value = value.lower().replace("\u0111", "d")
    decomposed = unicodedata.normalize("NFD", value)
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split())


def parse_model(text: str) -> str | None:
    """Return a model key such as 'iphone-16-pro-max', 'iphone-16e', 'iphone-air' or 'iphone-duo'."""
    match = _MODEL.search(normalize_text(text))
    if not match:
        return None
    number, e_suffix, name, variant = match.groups()
    parts = ["iphone", name or f"{number}{e_suffix or ''}"]
    if variant:
        is_pro_max = variant == "pm" or (variant.startswith("pro") and variant.endswith("max"))
        parts.append("pro-max" if is_pro_max else variant)
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


def in_scope(model: str) -> bool:
    """iPhone 15 and newer, including the e, Air and Duo models."""
    generation = model.split("-")[1]
    number = re.match(r"\d+", generation)
    return int(number.group()) >= FIRST_GENERATION if number else True


def is_new_vna(title: str) -> bool:
    return not _NOT_NEW_VNA.search(normalize_text(title))
