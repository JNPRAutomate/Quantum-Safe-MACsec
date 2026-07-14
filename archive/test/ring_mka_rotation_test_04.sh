#!/bin/sh

SRC="10.100.255.7"

DURATION="${1:-600}"
COUNT_PER_ROUND="${2:-10}"
SLEEP_BETWEEN_ROUNDS="${3:-2}"

TEST_TS=$(date '+%Y%m%d_%H%M%S')
OUT="/var/tmp/ring_mka_rotation_test_${TEST_TS}.log"
EVENTLOG="/var/tmp/ring_mka_events_${TEST_TS}.log"

touch "$EVENTLOG"

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

capture_events()
{
    cli -c \
    'show log messages | match "ADJDOWN|ADJUP|Detached|Defaulted|Expired|MACSEC NOT INUSE|commit failed|authentication-key-chains|RPD_ISIS|STATE SAVE ERROR|Operation not permitted"' \
    >> "$EVENTLOG" 2>&1
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

    log "Result file : $OUT"
    log "Event file  : $EVENTLOG"

    log

    run_shell "Ping failures (100% loss)" \
        "grep -c '100% packet loss' '$OUT'"

    run_shell "ISIS ADJDOWN events" \
        "grep -c 'ADJDOWN' '$EVENTLOG'"

    run_shell "ISIS ADJUP events" \
        "grep -c 'ADJUP' '$EVENTLOG'"

    run_shell "LACP Detached events" \
        "grep -c 'Detached' '$EVENTLOG'"

    run_shell "LACP Defaulted events" \
        "grep -c 'Defaulted' '$EVENTLOG'"

    run_shell "LACP Expired events" \
        "grep -c 'Expired' '$EVENTLOG'"

    run_shell "MACSEC NOT INUSE events" \
        "grep -c 'MACSEC NOT INUSE' '$EVENTLOG'"

    run_shell "Commit failures" \
        "grep -c 'commit failed' '$EVENTLOG'"

    run_shell "Authentication-keychain failures" \
        "grep -c 'authentication-key-chains not defined' '$EVENTLOG'"

    run_shell "State save failures" \
        "grep -E 'STATE SAVE ERROR|Operation not permitted' '$EVENTLOG' | wc -l"

    run_shell "Successful key promotions" \
        "grep -c 'PENDING KEY PROMOTED' '$OUT'"

    run_shell "Successful rotations" \
        "grep -c 'KEYCHAIN ROTATION DONE' '$OUT'"

    run_shell "MACSEC operational OK" \
        "grep -c 'MACSEC OPERATIONAL STATE OK' '$OUT'"

    section "CURRENT NETWORK STATE"

    run_cli "Current ISIS state" \
        'show isis adjacency'

    run_cli "Current LACP state" \
        'show lacp interfaces'

    run_cli "Current MACsec state" \
        'show security macsec connections'

    run_cli "Current MKA state" \
        'show security mka sessions'

    section "EXPECTED"

    log "Ping failures           : 0"
    log "ISIS ADJDOWN            : 0"
    log "LACP Detached           : 0"
    log "LACP Defaulted          : 0"
    log "LACP Expired            : 0"
    log "MACSEC NOT INUSE        : 0"
    log "Commit failures         : 0"
    log "State save failures     : 0"

    log
    log "Successful promotions   : > 0"
    log "Successful rotations    : > 0"
    log "MACSEC operational OK   : > 0"
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

    capture_events

    ROUND=$((ROUND + 1))

    sleep "$SLEEP_BETWEEN_ROUNDS"
done

capture_operational_snapshot "FINAL"
capture_recent_events "FINAL"

summary

section "Test completed"

log "Finished at $(date)"
log "Result file: $OUT"