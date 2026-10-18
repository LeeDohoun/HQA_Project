#!/usr/bin/env bash
# Morning run: collector pull, then the daily morning run (see run.sh).
exec bash "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/run.sh" morning "$@"
