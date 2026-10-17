#!/usr/bin/env bash
set -euo pipefail
cd /home/are01/src/HQA_Project/HQA_av002
export GIT_INDEX_FILE="$PWD/research/experiments/AV002_avoidance_holdout_recovery/validation/merge/git_staging/index"
export GIT_OBJECT_DIRECTORY="$PWD/research/experiments/AV002_avoidance_holdout_recovery/validation/merge/git_staging/objects"
export GIT_ALTERNATE_OBJECT_DIRECTORIES=/home/are01/src/HQA_Project/HQA_Project/.git/objects
exec git "$@"
