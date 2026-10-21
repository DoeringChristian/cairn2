#!/bin/bash
#SBATCH --job-name=fit
#SBATCH --time=00:30:00
#SBATCH --cpus-per-task=2
#SBATCH --mem=4G
# Usage, from examples/training: sbatch jobs/slurm.sbatch model=cubic learning_rate=0.3
set -euo pipefail
source jobs/env.sh
srun python train.py "$@"
