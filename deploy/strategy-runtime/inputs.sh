#!/usr/bin/env bash
# Input seal before 08:59: collector pull, then submit-due --inputs-only --execute (see run.sh).
exec bash "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/run.sh" inputs "$@"
