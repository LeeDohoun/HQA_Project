"""Round percentages in AI-written prose to one decimal place.

The prompts ask every model to round percentages and percentage points to one decimal, but a
model sometimes copies a raw provider value such as "1.56%". This applies the rule after the
answer arrives, so the screen never depends on the model remembering it. Other numbers (prices,
multiples such as "PER 31.69배") are left as written.
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# A number with two or more decimals right before a percent sign: "1.56%", "-13.25 %p", "2.345％", "0.75퍼센트".
# The sign stays outside the match, so rounding the magnitude rounds halves away from zero.
_PERCENT = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})+|\d+)\.(\d{2,})(?=\s*(?:%|％|퍼센트))")
_TENTH = Decimal("0.1")


def _rounded(match: re.Match) -> str:
    whole, fraction = match.group(1), match.group(2)
    value = Decimal(whole.replace(",", "") + "." + fraction)
    rounded = value.quantize(_TENTH, rounding=ROUND_HALF_UP)
    if rounded == 0 and value != 0:
        return match.group(0)  # "0.04%" stays: "0.0%" would claim there was no change at all.
    return format(rounded, ",.1f" if "," in whole else ".1f")


def round_percentages(text: str) -> str:
    """Return text with every percentage rounded to one decimal place; anything unexpected returns it unchanged."""
    if not isinstance(text, str):
        return text
    try:
        return _PERCENT.sub(_rounded, text)
    except (InvalidOperation, ValueError, TypeError):
        return text
