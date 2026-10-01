#!/bin/sh

# SRC (source address) and DESTS (whitespace-separated name:address targets)
# come from the environment, normally set by tests/scripts/run_onbox_test.py
# from the selected inventory. There are no built-in lab defaults.
if [ -z "${SRC:-}" ] || [ -z "${DESTS:-}" ]; then
    echo "[ERROR] SRC and DESTS must be set; run through tests/scripts/run_onbox_test.py or export them" >&2
    exit 2
fi

DURATION="${1:-${DURATION:-90}}"
COUNT_PER_ROUND="${2:-${COUNT_PER_ROUND:-5}}"

START=$(date +%s)
END=$((START + DURATION))
ROUND=1

echo "============================================================"
echo " Double-buffer MACsec/QKD ping test"
echo " Source loopback: $SRC"
echo " Duration: ${DURATION}s"
echo " Ping count per destination per round: ${COUNT_PER_ROUND}"
echo " Start: $(date)"
echo "============================================================"
echo

echo "=== Initial MACsec state ==="
cli -c 'show security macsec connections | match "Interface name|CA name|Status: inuse"'
echo

echo "=== Initial routes from source node ==="
for item in $DESTS; do
    NAME=$(echo "$item" | cut -d: -f1)
    DST=$(echo "$item" | cut -d: -f2)
    echo "--- route to $NAME $DST ---"
    cli -c "show route $DST"
done

echo
echo "============================================================"
echo " Starting ping loop"
echo "============================================================"

while [ "$(date +%s)" -lt "$END" ]; do

    NOW=$(date '+%Y-%m-%d %H:%M:%S')
    echo
    echo "------------------------------------------------------------"
    echo "ROUND $ROUND at $NOW"
    echo "------------------------------------------------------------"

    for item in $DESTS; do
        NAME=$(echo "$item" | cut -d: -f1)
        DST=$(echo "$item" | cut -d: -f2)

        echo
        echo ">>> ping $NAME $DST source $SRC"

        cli -c "ping $DST source $SRC rapid count $COUNT_PER_ROUND"

        RC=$?

        if [ "$RC" -ne 0 ]; then
            echo "!!! PING COMMAND RETURNED NON-ZERO rc=$RC dst=$DST name=$NAME"
        fi
    done

    echo
    echo "=== MACsec state after round $ROUND ==="
    cli -c 'show security macsec connections | match "Interface name|CA name|Status: inuse"'

    ROUND=$((ROUND + 1))
    sleep 2

done

echo
echo "============================================================"
echo " Test completed at $(date)"
echo "============================================================"
echo

echo "=== Final MACsec state ==="
cli -c 'show security macsec connections | match "Interface name|CA name|Status: inuse"'

echo
echo "=== Final MACsec statistics summary ==="
cli -c 'show security macsec statistics | match "Interface name|Encrypted packets|Encrypted bytes|Accepted packets|Decrypted bytes"'
