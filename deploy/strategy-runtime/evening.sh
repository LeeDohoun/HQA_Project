#!/usr/bin/env bash
# Evening run: collector pull, then the daily evening run (see run.sh).
exec bash "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/run.sh" evening "$@"
