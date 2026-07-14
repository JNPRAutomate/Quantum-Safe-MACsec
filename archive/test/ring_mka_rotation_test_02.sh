#!/bin/sh

SRC="10.100.255.7"

DURATION="${1:-180}"
COUNT_PER_ROUND="${2:-10}"
SLEEP_BETWEEN_ROUNDS="${3:-2}"

TEST_TS=$(date '+%Y%m%d_%H%M%S')
OUT="/var/tmp/ring_mka_rotation_test_${TEST_TS}.log"

DESTS="
acx2:10.100.255.9
acx3:10.100.255.8
acx4:10.100.255.11
acx5:10.100.255.10
"

START=$(date +%s)
END=$((START + DURATION))
ROUND=1

log()
{
    echo "$@" | tee -a "$OUT"
}

section()
{
    log
    log "============================================================"
    log "$@"
    log "============================================================"
}

subsection()
{
    log
    log "------------------------------------------------------------"
    log "$@"
    log "------------------------------------------------------------"
}

run_cli()
{
    TITLE="$1"
    CMD="$2"

    log
    log "--- $TITLE ---"
    log "CMD: $CMD"

    cli -c "$CMD" 2>&1 | tee -a "$OUT"
}

run_shell()
{
    TITLE="$1"
    CMD="$2"

    log
    log "--- $TITLE ---"
    log "CMD: $CMD"

    sh -c "$CMD" 2>&1 | tee -a "$OUT"
}

capture_operational_snapshot()
{
    LABEL="$1"

    section "$LABEL - operational snapshot"

    run_cli "MACsec connections" \
        'show security macsec connections | match "Interface name|CA name|Status: inuse|Cipher suite|Encryption"'

    run_cli "MACsec statistics summary" \
        'show security macsec statistics | match "Interface name|Encrypted packets|Encrypted bytes|Accepted packets|Decrypted bytes|Not valid|Not using SA|Invalid"'

    run_cli "MKA sessions summary" \
        'show security mka sessions | no-more'

    run_cli "MKA sessions detail" \
        'show security mka sessions detail | no-more'

    run_cli "LACP interfaces" \
        'show lacp interfaces | no-more'

    run_cli "Aggregated Ethernet terse" \
        'show interfaces terse | match "ae|et-"'

    run_cli "ISIS adjacency" \
        'show isis adjacency | no-more'
}

capture_recent_events()
{
    LABEL="$1"

    section "$LABEL - recent event/log scan"

    run_cli "messages critical network/MACsec/MKA/LACP/ISIS events" \
        'show log messages | match "LACP|lacp|MKA|mka|MACSEC|macsec|KMD|kmd|RPD_ISIS|ISIS|isis|ADJDOWN|ADJUP|ae|link down|link up|AUTH|authentication|keychain|connectivity-association|commit failed" | last 120'

    run_shell "qkd debug recent interesting events" \
        'for f in /var/tmp/qkd_debug*.log; do [ -f "$f" ] || continue; echo "### $f"; grep -E "ERROR|FAIL|FAILED|KEYCHAIN|MKA|PENDING|PROMOTED|ROTATION|BOOTSTRAP|INSTALL-KEY|MACSEC|SSH|DEC|ENC|KME" "$f" | tail -80; done'

    run_shell "qkd debug recent tail" \
        'for f in /var/tmp/qkd_debug*.log; do [ -f "$f" ] || continue; echo "### $f"; tail -40 "$f"; done'
}

ping_round()
{
    ROUND_ID="$1"

    NOW=$(date '+%Y-%m-%d %H:%M:%S')

    section "ROUND $ROUND_ID at $NOW"

    for item in $DESTS; do

        NAME=$(echo "$item" | cut -d: -f1)
        DST=$(echo "$item" | cut -d: -f2)

        subsection "ping $NAME $DST source $SRC"

        cli -c "ping $DST source $SRC rapid count $COUNT_PER_ROUND" 2>&1 | tee -a "$OUT"

    done

    run_cli "MACsec state after round $ROUND_ID" \
        'show security macsec connections | match "Interface name|CA name|Status: inuse"'

    run_cli "MKA summary after round $ROUND_ID" \
        'show security mka sessions | no-more'

    run_cli "LACP quick check after round $ROUND_ID" \
        'show lacp interfaces | match "Aggregated interface|LACP state|Collecting|Distributing|Detached|Expired|Defaulted|Synchronization"'

    run_cli "Recent LACP/MKA/MACsec/ISIS messages after round $ROUND_ID" \
        'show log messages | match "LACP|lacp|MKA|mka|MACSEC|macsec|RPD_ISIS|ADJDOWN|ADJUP|link down|link up|ae|commit failed" | last 40'
}

summary()
{
    section "FINAL SUMMARY AND FAILURE MARKERS"

    log "Result file: $OUT"
    log

    run_shell "Count ping failures" \
        "grep -c '100% packet loss' '$OUT'"

    run_shell "Count LACP down/defaulted/expired/detached markers" \
        "grep -Ei 'LACP.*(down|expired|defaulted|detached)|detached|expired|defaulted' '$OUT' | wc -l"

    run_shell "Count ISIS adjacency down markers" \
        "grep -Ei 'RPD_ISIS|ADJDOWN|adjacency.*down|isis.*down' '$OUT' | wc -l"

    run_shell "Count MACsec/MKA error markers" \
        "grep -Ei 'MACSEC.*FAIL|MKA.*FAIL|MACSEC NOT INUSE|commit failed|authentication-key-chains not defined|May not be configured' '$OUT' | wc -l"

    run_shell "Count QKD/keychain install failures" \
        "grep -Ei 'KEYCHAIN INSTALL FAIL|INSTALL-KEY ABORTED|BOOTSTRAP FAILED|ROTATION.*FAILED|PEER_INSTALL_KEY_FAILED|LOCAL_INSTALL_KEY_FAILED|DEC_FAILED|ENC_FAILED' '$OUT' | wc -l"

    run_shell "Count successful key promotions" \
        "grep -c 'PENDING KEY PROMOTED' '$OUT'"

    run_shell "Count keychain rotations" \
        "grep -c 'KEYCHAIN ROTATION DONE' '$OUT'"

    log
    log "Important markers to inspect manually:"
    log "  - packet loss"
    log "  - LACP detached/defaulted/expired"
    log "  - RPD_ISIS / ADJDOWN"
    log "  - MACSEC NOT INUSE"
    log "  - KEYCHAIN INSTALL FAIL"
    log "  - INSTALL-KEY ABORTED"
    log "  - PENDING KEY PROMOTED"
    log "  - KEYCHAIN ROTATION DONE"
    log "  - commit failed"
}

section "Ring MACsec/QKD/MKA scheduled rotation test"

log "Source loopback: $SRC"
log "Duration: ${DURATION}s"
log "Ping count per destination per round: ${COUNT_PER_ROUND}"
log "Sleep between rounds: ${SLEEP_BETWEEN_ROUNDS}s"
log "Start: $(date)"
log "Result file: $OUT"

section "Initial routes from source node"

for item in $DESTS; do

    NAME=$(echo "$item" | cut -d: -f1)
    DST=$(echo "$item" | cut -d: -f2)

    run_cli "route to $NAME $DST" "show route $DST"

done

capture_operational_snapshot "INITIAL"
capture_recent_events "INITIAL"

section "Starting ping/MKA rotation observation loop"

while [ "$(date +%s)" -lt "$END" ]; do

    ping_round "$ROUND"

    ROUND=$((ROUND + 1))

    sleep "$SLEEP_BETWEEN_ROUNDS"

done

capture_operational_snapshot "FINAL"
capture_recent_events "FINAL"

summary

section "Test completed at $(date)"

log "Result file: $OUT"