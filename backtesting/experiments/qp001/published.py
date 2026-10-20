"""Bounded reader for the hash-sealed, indent=2 published AV001 result.

The full JSON expands well beyond the short replay's memory needs. Only the
first three selections/observations and the small price-hash map are decoded.
Formatting is part of the checked result hash; unexpected structure fails.
"""
import gzip
import json
import re


def _object(handle, indent, opening):
    lines = [opening]
    for line in handle:
        lines.append(line)
        if line.startswith(" " * indent + "}") and line.strip() in ("}", "},"):
            return json.loads("".join(lines).strip().removesuffix(","))
    raise ValueError("unterminated published AV001 object")


def _first_three(handle, indent):
    values = []
    for line in handle:
        if line.strip() == "{":
            values.append(_object(handle, indent + 2, line))
            if len(values) == 3:
                return values
        elif line.strip() == "]" or line.strip() == "],":
            break
    raise ValueError("published AV001 replay requires three observations")


def read_av_window(path):
    result = {"selections": {}, "observations": {}, "prices": {}}
    section = rule = variant = None
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            match = re.match(r'^( +)"([^"\\]+)": (.*)', line)
            if match is None:
                continue
            spaces, name, value = match.groups()
            depth = len(spaces)
            if depth == 2:
                section, rule, variant = name, None, None
            elif depth == 4 and section in ("selections", "rules"):
                rule, variant = name, None
                if section == "selections" and rule in ("S", "O") and value == "[":
                    result["selections"][rule] = _first_three(handle, depth)
            elif depth == 6 and section == "rules":
                variant = name
            elif depth == 8 and section == "rules" and rule in ("S", "O") and variant == "av_all" and name == "per_month" and value == "[":
                result["observations"][rule] = _first_three(handle, depth)
            elif depth == 4 and section == "source_hashes" and name == "prices" and value == "{":
                result["prices"] = _object(handle, depth, "{\n")
    if set(result["selections"]) != {"S", "O"} or set(result["observations"]) != {"S", "O"} or not result["prices"]:
        raise ValueError("published AV001 result structure differs from the sealed replay contract")
    return result
