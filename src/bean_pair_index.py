"""BEAN compact retention keys: eight typed pairs = exactly 16 ASCII bytes.

Slots reference separate, versioned tables. These indexes are lossless LOCATORS
for the source decision evidence, not a replacement for that evidence. Codebook
entries describe observed field values, never invent causal explanations.

A space matches any character *within that pair's type*; a fully blank pair
matches any type. Prefix patterns may be shorter than eight slots.
"""
from __future__ import annotations

import json
from string import ascii_lowercase, ascii_uppercase, digits, punctuation

SCHEMA = "aiscend.bean.pair-index.v1"
PAIR_TYPES = {
    "upper": ascii_uppercase,
    "lower": ascii_lowercase,
    "numeric": digits,
    "symbol": punctuation,
}
SLOTS = (
    ("product", "upper"),
    ("momentum_7d", "lower"),
    ("momentum_28d", "lower"),
    ("momentum_84d", "lower"),
    ("volatility_14d", "lower"),
    ("action", "numeric"),
    ("root_cause_code", "numeric"),
    ("net_return_band", "numeric"),
)
# Version ONE table assignments are immutable. Future extensions must append
# unused values, or create a new schema: never reinterpret old memory.
REFERENCES = {
    "product": {"AA": "BTC-USD", "AB": "ETH-USD"},
    "momentum_7d": {"aa": "down", "ab": "flat", "ac": "up"},
    "momentum_28d": {"aa": "down", "ab": "flat", "ac": "up"},
    "momentum_84d": {"aa": "down", "ab": "flat", "ac": "up"},
    "volatility_14d": {"aa": "high", "ab": "normal"},
    "action": {"01": "cash", "02": "trade"},
    "root_cause_code": {f"{n:02d}": f"{n:02d}" for n in range(1, 7)},
    "net_return_band": {
        "01": "less_than_minus_5pct",
        "02": "minus_5pct_to_minus_1pct",
        "03": "minus_1pct_to_0pct",
        "04": "zero",
        "05": "0pct_to_1pct",
        "06": "1pct_to_5pct",
        "07": "greater_than_5pct",
    },
}


def pair_type(pair):
    """Validate a literal typed pair; spaces may wildcard either position."""
    if not isinstance(pair, str) or len(pair) != 2 or not pair.isascii():
        raise ValueError("A pair must be exactly two ASCII characters")
    observed = [c for c in pair if c != " "]
    kinds = {kind for c in observed for kind, alphabet in PAIR_TYPES.items()
             if c in alphabet}
    if len(kinds) > 1 or len(kinds) == 0 and observed:
        raise ValueError("Mixed or unsupported character classes in pair")
    return next(iter(kinds)) if kinds else None


def parse(value, *, pattern=False):
    if not isinstance(value, str) or not value.isascii():
        raise ValueError("Index must be printable ASCII")
    if len(value) % 2 or (pattern and not 2 <= len(value) <= 16) or (
            not pattern and len(value) != 16):
        raise ValueError("Indexes use exactly eight 2-character slots; "
                         "patterns use 1-8 complete pairs")
    pairs = tuple(value[i:i+2] for i in range(0,len(value),2))
    for i, pair in enumerate(pairs):
        kind = pair_type(pair)
        if any(ord(c) < 32 or ord(c) > 126 for c in pair):
            raise ValueError("Only printable ASCII pairs permitted")
        if not pattern and (" " in pair or kind != SLOTS[i][1]):
            raise ValueError("Retained index needs eight complete typed pairs")
        if pattern and kind is not None and kind != SLOTS[i][1]:
            raise ValueError("Pattern pair type is inconsistent with slot")
    return pairs


def matches(pattern, value):
    query = parse(pattern, pattern=True)
    stored = parse(value)
    for p, token in zip(query,stored):
        if not all(q == " " or q == c for q,c in zip(p,token)):
            return False
    return True


def return_band(net):
    if net < -.05: return "01"
    if net < -.01: return "02"
    if net < 0: return "03"
    if net == 0: return "04"
    if net <= .01: return "05"
    if net <= .05: return "06"
    return "07"


def encode(*, product, state, action, root_cause_code, net_return):
    """Encode only measured, retained classifications from explicit tables."""
    values = {
        "product": product,
        "action": action,
        "root_cause_code": root_cause_code,
        "net_return_band": REFERENCES["net_return_band"][return_band(net_return)],
        **{name: state[name] for name in (
            "momentum_7d","momentum_28d","momentum_84d","volatility_14d")},
    }
    parts=[]
    for name, kind in SLOTS:
        reverse={meaning:code for code,meaning in REFERENCES[name].items()}
        if values[name] not in reverse:
            raise ValueError("Unknown definition for "+name+": "+str(values[name]))
        token=reverse[values[name]]
        if pair_type(token) != kind:
            raise AssertionError("Incorrect typed reference")
        parts.append(token)
    code="".join(parts)
    parse(code)
    if len(code.encode("ascii"))!=16:
        raise AssertionError("Index must be exactly 16 bytes")
    return code


def query_from_state(product, state):
    """Five known, predecision market slots; 3 fully wildcarded outcome slots."""
    return encode_prefix(product, state) + "      "


def encode_prefix(product, state):
    values={"product":product,**state}
    parts=[]
    for field,kind in SLOTS[:5]:
        reverse={v:k for k,v in REFERENCES[field].items()}
        if values[field] not in reverse:
            raise ValueError("Unknown market variable: "+field)
        parts.append(reverse[values[field]])
    return "".join(parts)


def reference_document():
    """Slot order is the implicit table address; pair is the reference key."""
    slots=[]
    for index,(name,kind) in enumerate(SLOTS,1):
        slots.append({
            "position":index,
            "byte_offset":2*(index-1),
            "field":name, "type":kind,
            "references":[{"key":key,"meaning":meaning} for key,meaning
                          in sorted(REFERENCES[name].items())],
        })
    return {
        "schema":SCHEMA,"slots":slots,
        "index_bytes":16,
        "separator_bytes":0,
        "wildcard":"ASCII space (only in searches, never saved indexes)",
        "causal_boundary":"slots 1-5 before decision; slots 6-8 after exit",
        "definition_policy":"versioned append-only; do not reassign codes",
        "no_claim_of_causal_root_proof":True,
    }
