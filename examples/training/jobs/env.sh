# Sourced by every cluster job. Jobs log to a Cairn server, and each process keeps
# its outbox of unsent results on node-local disk.
: "${CAIRN_REPO:?set CAIRN_REPO to your Cairn server, e.g. https://cairn.example.org}"
export CAIRN_STATE_DIR="${TMPDIR:-/tmp}/cairn-$(hostname)-$$"
