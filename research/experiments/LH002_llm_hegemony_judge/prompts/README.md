# LH002 prompts and provenance

All `v1*` prompt and schema files here are byte-for-byte copies of LH001 commit
`65dd9e5`, except `v1_probe.*`, which is deliberately excluded. Strategy calls
continue to use `v1_common.txt` and the unchanged numeric/text templates.
`v2_probe.txt` and `v2_probe.schema.json` are new and implement LH002 section 7:
eight memory-based answers, A/B/C options, confidence 1–5 and reasons up to 100
characters. The runner sends the v2 probe template alone, without `v1_common.txt`.

The shared implementation is `backtesting/experiments/lh001/`; `config.py` selects
the experiment identity and `probe_v2.py` implements the LH002 questions and
one-sided exact binomial rules. B_num controls use the decision-close 20-session
ADV top 300 of the full HC002 universe, regardless of selected industries, as
fixed in LH002 section 2. Other strategy, execution and verdict rules remain v1.

```bash
venv/bin/python -m backtesting.experiments.lh001 --experiment LH002 probe
venv/bin/python -m backtesting.experiments.lh001 --experiment LH002 screening
venv/bin/python -m backtesting.experiments.lh001 --experiment LH002 final
venv/bin/python -m backtesting.experiments.lh001 --experiment LH002 status
venv/bin/python -m backtesting.experiments.lh002 status
```

Stage commands are metadata-only dry runs by default. `--execute` requires all
`v1*` and `v2*` files to be committed and unchanged before real model calls.
This implementation task does not commit files or invoke a model. Tests use
synthetic facts and injected subprocesses; dry runs never read probe source facts.

Runtime cache, inputs, budget, manifest, snapshots and stage state live only under
`data/research/lh002/`, with an independent 1,100-call cap. Results are written to
this experiment's `results/` directory and registry rows use
`LH002_llm_hegemony_judge`. Final source acquisition uses its own single
`HoldoutSession("LH002_llm_hegemony_judge")`; resume uses the frozen snapshot.
`probe_insensitive`, incomplete probes and `insufficient_clean_window` all block
final acquisition. The dedicated contamination reader uses only month-end
market facts, including 2024 controls; it never calls `guard_period`, consumes
holdout access, or evaluates strategy returns.
