#!/bin/bash
#PBS -N fit-sweep
#PBS -l select=1:ncpus=2:mem=4gb
#PBS -l walltime=00:30:00
# Usage, from examples/training: qsub -V -J 1-$(wc -l < jobs/sweep.txt) jobs/pbs.sh
set -euo pipefail
cd "$PBS_O_WORKDIR"
source jobs/env.sh
python train.py $(sed -n "${PBS_ARRAY_INDEX}p" jobs/sweep.txt)
