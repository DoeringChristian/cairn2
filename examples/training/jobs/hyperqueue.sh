#!/bin/bash
#HQ --cpus=2
#HQ --time-limit=30m
#HQ --stdout=build/hq/%{JOB_ID}/%{TASK_ID}.out
#HQ --stderr=build/hq/%{JOB_ID}/%{TASK_ID}.err
# Usage, from examples/training: hq submit --each-line jobs/sweep.txt --env CAIRN_REPO=$CAIRN_REPO jobs/hyperqueue.sh
set -euo pipefail
source jobs/env.sh
python train.py $HQ_ENTRY
