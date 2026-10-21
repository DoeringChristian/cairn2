#!/bin/bash
# Usage: jobs/local.sh [processes]   Runs every line of jobs/sweep.txt on this machine.
set -euo pipefail
cd "$(dirname "$0")/.."
xargs -P "${1:-4}" -L 1 python train.py < jobs/sweep.txt
