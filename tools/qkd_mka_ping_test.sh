#!/bin/sh
# qkd_mka_ping_test.sh
#
# Ring-wide MACsec/QKD/MKA health test.
# Run from ACX1 (sae-007, loopback 10.100.255.7) to cover all ring nodes.
#
# Usage:
#   sh qkd_mka_ping_test.sh [duration_seconds] [ping_count] [sleep_between_rounds]
#
# Defaults:
#   duration  = 600s  (10 minutes)
#   count     = 10    pings per destination per round
#   sleep     = 2s    between rounds
#
# Examples:
#   sh qkd_mka_ping_test.sh            # 10 min test
#   sh qkd_mka_ping_test.sh 300        # 5 min test
#   sh qkd_mka_ping_test.sh 3600 20 5  # 1 hour, 20 pings, 5s sleep

SRC="10.100.255.7"

DURATION="${1:-600}"
COUNT_PER_ROUND="${2:-10}"
SLEEP_BETWEEN_ROUNDS="${3:-2}"

TEST_TS=$(date '+%Y%m%d_%H%M%S')
OUT="/var/tmp/qkd_mka_ping_test_${TEST_TS}.log"
EVENTLOG="/var/tmp/qkd_mka_events_${TEST_TS}.log"

touch "$EVENTLOG"

# All ring nodes: MX1-MX6 + ACX1(skip, that's us) + ACX2-ACX5
# Loopbacks:
#   mx1  = 10.100.255.5    mx2  = 10.100.255.6
#   mx3  = 10.100.255.2    mx4  = 10.100.255.4
#   mx5  = 10.100.255.3    mx6  = 10.100.255.1
#   acx2 = 10.100.255.9    acx3 = 10.100.255.8
#   acx4 = 10.100.255.11   acx5 = 10.100.255.10

MX_DESTS="
mx1:10.100.255.5
mx2:10.100.255.6
mx3:10.100.255.2
mx4:10.100.255.4
mx5:10.100.255.3
mx6:10.100.255.1
"

ACX_DESTS="
acx2:10.100.255.9
acx3:10.100.255.8
acx4:10.100.255.11
acx5:10.100.255.10
"

ALL_DESTS="$MX_DESTS $ACX_DESTS"

START=$(date +%s)
END=$((START + DURATION))
ROUND=1

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log()        { echo "$@"        | tee -a "$OUT"; }
section()    { log; log "============================================================"; log "$@"; log "============================================================"; }
subsection() { log; log "------------------------------------------------------------"; log "$@"; log "------------------------------------------------------------"; }

run_cli() {
    TITLE="$1"; CMD="$2"
    log; log "--- $TITLE ---"; log "CMD: $CMD"
    cli -c "$CMD" 2>&1 | tee -a "$OUT"
}

run_shell() {
    TITLE="$1"; CMD="$2"
    log; log "--- $TITLE ---"; log "CMD: $CMD"
    sh -c "$CMD" 2>&1 | tee -a "$OUT"
}

# ---------------------------------------------------------------------------
# Event capture
# ---------------------------------------------------------------------------

capture_events() {
    cli -c \
        'show log messages | match "ADJDOWN|ADJUP|Detached|Defaulted|Expired|MACSEC NOT INUSE|commit failed|authentication-key-chains|RPD_ISIS|STATE SAVE ERROR|Operation not permitted"' \
        >> "$EVENTLOG" 2>&1
}

# ---------------------------------------------------------------------------
# Operational snapshot
# ---------------------------------------------------------------------------

capture_operational_snapshot() {
    LABEL="$1"
    section "$LABEL - operational snapshot"

    run_cli "MACsec connections" \
        'show security macsec connections | match "Interface name|CA name|Status: inuse|Cipher suite|Encryption"'

    run_cli "MACsec statistics" \
        'show security macsec statistics | match "Interface name|Encrypted packets|Accepted packets|Invalid|Not valid|Not using SA"'

    run_cli "MKA sessions" \
        'show security mka sessions | no-more'

    run_cli "MKA statistics" \
        'show security mka statistics | no-more'

    run_cli "LACP state" \
        'show lacp interfaces | no-more'

    run_cli "Interfaces terse" \
        'show interfaces terse | match "ae|et-"'

    run_cli "ISIS adjacency" \
        'show isis adjacency | no-more'
}

# ---------------------------------------------------------------------------
# Recent events
# ---------------------------------------------------------------------------

capture_recent_events() {
    LABEL="$1"
    section "$LABEL - logs"

    run_cli "Network events" \
        'show log messages | match "ADJDOWN|ADJUP|Defaulted|Detached|Expired|link down|link up|commit failed|MACSEC|MKA|authentication-key-chains" | last 200'

    run_shell "QKD key promotions and failures" \
        'for f in /var/home/macsec_user/qkd-state/logs/qkd_debug*.log; do [ -f "$f" ] || continue; echo "### $f"; grep -E "PROMOTED|ROTATION|FAIL|INSTALL OK|MKA_KEY|STATE SAVE ERROR|SSH RC" "$f" | tail -60; done'

    run_shell "QKD PEER STATE MISMATCH" \
        'for f in /var/home/macsec_user/qkd-state/logs/qkd_debug*.log; do [ -f "$f" ] || continue; echo "### $f"; grep "MISMATCH\|STALE\|Permission denied" "$f" | tail -20; done'
}

# ---------------------------------------------------------------------------
# Per-round ping test
# ---------------------------------------------------------------------------

ping_round() {
    ROUND_ID="$1"
    NOW=$(date '+%Y-%m-%d %H:%M:%S')
    section "ROUND $ROUND_ID at $NOW"

    subsection "MX ring nodes"
    for item in $MX_DESTS; do
        NAME=$(echo "$item" | cut -d: -f1)
        DST=$(echo "$item" | cut -d: -f2)
        subsection "ping $NAME ($DST)"
        cli -c "ping $DST source $SRC rapid count $COUNT_PER_ROUND" 2>&1 | tee -a "$OUT"
    done

    subsection "ACX ring nodes"
    for item in $ACX_DESTS; do
        NAME=$(echo "$item" | cut -d: -f1)
        DST=$(echo "$item" | cut -d: -f2)
        subsection "ping $NAME ($DST)"
        cli -c "ping $DST source $SRC rapid count $COUNT_PER_ROUND" 2>&1 | tee -a "$OUT"
    done

    run_cli "MACsec state" \
        'show security macsec connections | match "Interface name|CA name"'

    run_cli "MKA state" \
        'show security mka sessions | no-more'

    run_cli "MKA statistics (CAK mismatches)" \
        'show security mka statistics | match "Interface name|CAK mismatch"'

    run_cli "LACP state" \
        'show lacp interfaces | match "Collecting|distributing|Detached|Expired|Defaulted"'
}

# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------

summary() {
    section "FINAL SUMMARY"

    log "Result file : $OUT"
    log "Event file  : $EVENTLOG"
    log ""

    run_shell "Ping failures - MX nodes (100% loss)" \
        "grep -c '100% packet loss' '$OUT' || echo 0"

    run_shell "ISIS ADJDOWN events" \
        "grep -c 'ADJDOWN' '$EVENTLOG' 2>/dev/null || echo 0"

    run_shell "ISIS ADJUP events" \
        "grep -c 'ADJUP' '$EVENTLOG' 2>/dev/null || echo 0"

    run_shell "LACP Detached events" \
        "grep -c 'Detached' '$EVENTLOG' 2>/dev/null || echo 0"

    run_shell "LACP Defaulted events" \
        "grep -c 'Defaulted' '$EVENTLOG' 2>/dev/null || echo 0"

    run_shell "LACP Expired events" \
        "grep -c 'Expired' '$EVENTLOG' 2>/dev/null || echo 0"

    run_shell "MACSEC NOT INUSE events" \
        "grep -c 'MACSEC NOT INUSE' '$EVENTLOG' 2>/dev/null || echo 0"

    run_shell "Commit failures" \
        "grep -c 'commit failed' '$EVENTLOG' 2>/dev/null || echo 0"

    run_shell "Authentication-keychain failures" \
        "grep -c 'authentication-key-chains not defined' '$EVENTLOG' 2>/dev/null || echo 0"

    run_shell "State save failures" \
        "grep -E 'STATE SAVE ERROR|Operation not permitted' '$EVENTLOG' 2>/dev/null | wc -l"

    run_shell "Successful key promotions (QKD)" \
        "grep -c 'PENDING KEY PROMOTED' '$OUT' 2>/dev/null || echo 0"

    run_shell "Successful rotations (QKD)" \
        "grep -c 'KEYCHAIN ROTATION BATCH DONE\|ROTATION_DONE' '$OUT' 2>/dev/null || echo 0"

    run_shell "MACSEC OPERATIONAL OK events" \
        "grep -c 'MACSEC OPERATIONAL STATE OK' '$OUT' 2>/dev/null || echo 0"

    run_shell "Peer state mismatches (QKD)" \
        "grep -c 'PEER STATE MISMATCH' '$OUT' 2>/dev/null || echo 0"

    run_shell "CAK mismatch packets (MKA)" \
        "grep 'CAK mismatch' '$OUT' | grep -v ' 0$' | wc -l"

    section "CURRENT NETWORK STATE"

    run_cli "Current ISIS adjacency" \
        'show isis adjacency'

    run_cli "Current LACP state" \
        'show lacp interfaces'

    run_cli "Current MACsec connections" \
        'show security macsec connections'

    run_cli "Current MKA sessions" \
        'show security mka sessions'

    run_cli "Current MKA statistics" \
        'show security mka statistics'

    section "EXPECTED (all zeroes = PASS)"

    log "Ping failures           : 0"
    log "ISIS ADJDOWN            : 0"
    log "LACP Detached           : 0"
    log "LACP Defaulted          : 0"
    log "LACP Expired            : 0"
    log "MACSEC NOT INUSE        : 0"
    log "Commit failures         : 0"
    log "State save failures     : 0"
    log "CAK mismatch packets    : 0"
    log ""
    log "Successful promotions   : > 0"
    log "Successful rotations    : > 0"
    log "MACSEC operational OK   : > 0"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

section "Ring MACsec/QKD/MKA rotation test"

log "Source loopback : $SRC (ACX1/sae-007)"
log "Destinations    : MX1-MX6 + ACX2-ACX5 ($(echo $ALL_DESTS | wc -w) nodes)"
log "Duration        : ${DURATION}s"
log "Ping count      : $COUNT_PER_ROUND per destination per round"
log "Sleep           : ${SLEEP_BETWEEN_ROUNDS}s between rounds"
log "Start           : $(date)"
log "Result file     : $OUT"
log "Event file      : $EVENTLOG"

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

log "Finished at : $(date)"
log "Result file : $OUT"
log "Event file  : $EVENTLOG"
