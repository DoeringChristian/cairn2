#!/bin/bash
#SBATCH --job-name=fit-sweep
#SBATCH --time=00:30:00
#SBATCH --cpus-per-task=2
#SBATCH --mem=4G
# Usage, from examples/training: sbatch --array=1-$(wc -l < jobs/sweep.txt)%2 jobs/slurm-array.sbatch
set -euo pipefail
source jobs/env.sh
srun python train.py $(sed -n "${SLURM_ARRAY_TASK_ID}p" jobs/sweep.txt)
