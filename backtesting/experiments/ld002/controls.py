"""Section 7 conditional ID replacement; RNG state travels in each daily seal."""
from copy import deepcopy

import numpy as np

from . import PATHS, Refused
from .ledger import targets


def initial_controls():
    return [{"mapping": {}, "ids": [], "rng": np.random.PCG64(seed).state}
            for seed in np.random.SeedSequence(0).spawn(PATHS)]


def replace(previous, output, universe, *, valid):
    if len(previous) != PATHS:
        raise Refused("exactly 200 replacement paths are required")
    if not valid:
        return deepcopy(previous), [None] * PATHS
    identities = {h["code"]: h["conviction"] for h in output["holdings"]}
    denominator = sum(identities.values())
    states, plans = [], []
    for state in previous:
        rng = np.random.Generator(np.random.PCG64(0))
        rng.bit_generator.state = state["rng"]
        mapping = {code: stock for code, stock in state["mapping"].items() if code in identities}
        for code in sorted(set(identities) - set(state["ids"])):
            pool = sorted(set(universe) - set(mapping.values()))
            if pool:
                mapping[code] = str(rng.choice(pool))
        substituted = {**output, "holdings": [{"code": mapping[code], "conviction": identities[code]}
                                            for code in sorted(mapping)]}
        plans.append({**targets(substituted, denominator=denominator), "output": substituted,
                      "shortage_ids": sorted(set(identities) - set(mapping))})
        states.append({"mapping": mapping, "ids": sorted(identities), "rng": rng.bit_generator.state})
    return states, plans
