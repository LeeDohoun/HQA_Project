# LH003 v3 artifacts

`v3_common.txt`, `v3_c_num.txt`, `v3_reask.txt` and `v3_c_num.schema.json`
are fixed artifacts. Commit these and the implementation before the first
real-data model call. This task makes no commits or model calls.

Each decision copies the schema and adds its anonymised IDs as both arrays'
`items.enum`. JSON Schema enforces string types, exact lengths 30/10, uniqueness
within arrays and no additional properties. Python validation enforces the
cross-array intersection constraint, which standard JSON Schema cannot express
using these per-decision enums. The optional `$schema` URI is omitted because
its date-like draft name fails the required leak check; the validator is
explicitly `Draft202012Validator`.

The LH002 token-based leak checker checks the complete prompt, dynamic schema,
fixed re-ask and assembled re-ask prompt. Invalid answers/errors are preserved
locally and never interpolated into re-asks. Artifact byte hashes and final
prompt/schema/re-ask content hashes are sealed per decision. This does not prove
that a company cannot be inferred from its numbers.

Development checks use an injected Python callable. It never starts Codex,
authenticates or calls a model, even when given a fake `codex exec` argument
list. These checks spend zero of the eight synthetic-format model calls.
