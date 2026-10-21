#!/usr/bin/env bash
# Submit run: collector pull, then submit-due --execute (see run.sh).
exec bash "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/run.sh" submit "$@"
