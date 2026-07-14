#!/bin/sh

SRC="10.100.255.7"

DURATION="${1:-600}"
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

    run_cli "MACsec statistics" \
        'show security macsec statistics | match "Interface name|Encrypted packets|Accepted packets|Invalid|Not valid|Not using SA"'

    run_cli "MKA summary" \
        'show security mka sessions | no-more'

    run_cli "LACP" \
        'show lacp interfaces | no-more'

    run_cli "Interfaces terse" \
        'show interfaces terse | match "ae|et-"'

    run_cli "ISIS adjacency" \
        'show isis adjacency | no-more'
}

capture_recent_events()
{
    LABEL="$1"

    section "$LABEL - logs"

    run_cli "Network events" \
        'show log messages | match "ADJDOWN|ADJUP|Defaulted|Detached|Expired|link down|link up|commit failed|MACSEC|MKA|authentication-key-chains" | last 200'

    run_shell "QKD debug highlights" \
        'for f in /var/tmp/qkd_debug*.log; do [ -f "$f" ] || continue; echo "### $f"; grep -E "FAIL|FAILED|PROMOTED|ROTATION|INSTALL-KEY|MACSEC|STATE SAVE|SSH RC|DEC|ENC" "$f" | tail -120; done'
}

ping_round()
{
    ROUND_ID="$1"

    NOW=$(date '+%Y-%m-%d %H:%M:%S')

    section "ROUND $ROUND_ID at $NOW"

    for item in $DESTS
    do
        NAME=$(echo "$item" | cut -d: -f1)
        DST=$(echo "$item" | cut -d: -f2)

        subsection "ping $NAME $DST"

        cli -c "ping $DST source $SRC rapid count $COUNT_PER_ROUND" \
            2>&1 | tee -a "$OUT"
    done

    run_cli "MACsec state" \
        'show security macsec connections | match "Interface name|CA name|Status: inuse"'

    run_cli "MKA state" \
        'show security mka sessions | no-more'

    run_cli "LACP state" \
        'show lacp interfaces | match "Collecting|distributing|Detached|Expired|Defaulted"'
}

summary()
{
    section "FINAL SUMMARY"

    log "Result file: $OUT"
    log

    run_shell "Ping failures (100% loss)" \
        "grep -c '100% packet loss' '$OUT'"

    run_shell "ISIS adjacency DOWN" \
        "grep -E 'ADJDOWN|RPD_ISIS_ADJDOWN|adjacency.*down' '$OUT' | wc -l"

    run_shell "LACP failures" \
        "grep -Ei 'Detached|Defaulted|Expired|LACP.*down' '$OUT' | wc -l"

    run_shell "MACsec not inuse" \
        "grep -Ei 'MACSEC NOT INUSE|Status: not inuse' '$OUT' | wc -l"

    run_shell "Commit failures" \
        "grep -Ei 'commit failed|configuration check-out failed|error: commit' '$OUT' | wc -l"

    run_shell "Authentication-keychain failures" \
        "grep -Ei 'authentication-key-chains not defined|May not be configured' '$OUT' | wc -l"

    run_shell "QKD install failures" \
        "grep -Ei 'KEYCHAIN INSTALL FAIL|INSTALL-KEY ABORTED|BOOTSTRAP FAILED|ROTATION FAILED|PEER_INSTALL_KEY_FAILED|LOCAL_INSTALL_KEY_FAILED' '$OUT' | wc -l"

    run_shell "Crypto failures" \
        "grep -Ei 'ENC_FAILED|DEC_FAILED' '$OUT' | wc -l"

    run_shell "State save failures" \
        "grep -Ei 'STATE SAVE ERROR|Operation not permitted' '$OUT' | wc -l"

    run_shell "SSH failures" \
        "grep -Ei 'SSH RC=[1-9]|SSH FAILED|peer SSH failed' '$OUT' | wc -l"

    run_shell "Successful key promotions" \
        "grep -c 'PENDING KEY PROMOTED' '$OUT'"

    run_shell "Successful rotations" \
        "grep -c 'KEYCHAIN ROTATION DONE' '$OUT'"

    run_shell "MACsec operational OK" \
        "grep -c 'MACSEC OPERATIONAL STATE OK' '$OUT'"

    section "PASS FAIL VIEW"

    run_shell "Failures" \
        "grep -Ei '100% packet loss|ADJDOWN|Detached|Defaulted|Expired|MACSEC NOT INUSE|commit failed|KEYCHAIN INSTALL FAIL|INSTALL-KEY ABORTED|ENC_FAILED|DEC_FAILED|STATE SAVE ERROR|Operation not permitted' '$OUT' | tail -50"

    run_shell "Successes" \
        "grep -E 'PENDING KEY PROMOTED|KEYCHAIN ROTATION DONE|MACSEC OPERATIONAL STATE OK' '$OUT' | tail -50"

    section "EXPECTED"

    log "Ping failures              : 0"
    log "ISIS adjacency DOWN        : 0"
    log "LACP failures              : 0"
    log "MACsec NOT INUSE           : 0"
    log "Commit failures            : 0"
    log "QKD install failures       : 0"
    log "Crypto failures            : 0"
    log "State save failures        : 0"
    log "SSH failures               : 0"
    log
    log "Successful key promotions  : > 0"
    log "Successful rotations       : > 0"
    log "MACsec operational OK      : > 0"
}

section "Ring MACsec/QKD/MKA rotation test"

log "Source loopback: $SRC"
log "Duration: ${DURATION}s"
log "Ping count: $COUNT_PER_ROUND"
log "Sleep: $SLEEP_BETWEEN_ROUNDS"
log "Start: $(date)"
log "Result file: $OUT"

capture_operational_snapshot "INITIAL"
capture_recent_events "INITIAL"

while [ "$(date +%s)" -lt "$END" ]
do
    ping_round "$ROUND"

    ROUND=$((ROUND + 1))

    sleep "$SLEEP_BETWEEN_ROUNDS"
done

capture_operational_snapshot "FINAL"
capture_recent_events "FINAL"

summary

section "Test completed"

log "Finished at $(date)"
log "Result file: $OUT"