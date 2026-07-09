#!/bin/sh

SRC="10.100.255.7"
DURATION="${1:-720}"
COUNT_PER_ROUND="${2:-5}"
SLEEP_BETWEEN_ROUNDS="${3:-2}"

TEST_TS=$(date '+%Y%m%d_%H%M%S')
OUT="/var/tmp/ring_mka_rotation_test_${TEST_TS}.log"

DESTS="
acx2:10.100.255.9
acx3:10.100.255.8
acx4:10.100.255.11
acx5:10.100.255.10
"

QKD_IFACES="
et-2/0/4
et-2/0/2
"

QKD_CAS="
CA1:QKD_CA1
CA9:QKD_CA9
"

QKD_LOG_GLOB="/var/tmp/qkd_debug*.log"

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

capture_qkd_state()
{
    LABEL="$1"

    section "$LABEL - QKD onbox state"

    for iface in $QKD_IFACES; do
        run_cli "qkd_onbox status iface $iface" \
            "op qkd_onbox.py action status iface $iface"
    done
}

capture_qkd_timeline()
{
    LABEL="$1"

    section "$LABEL - QKD rotation timeline"

    run_shell "qkd scheduled/pending/promoted timeline" \
        'for f in /var/tmp/qkd_debug*.log; do [ -f "$f" ] || continue; echo "### $f"; grep -E "KEYCHAIN ROTATION START|INSTALL-KEY SCHEDULE|STATE SAVED|pending_key_id|next_start_time|MKA KEY CONFIRMED|PENDING KEY PROMOTED|PENDING_KEY_NOT_CONFIRMED|KEYCHAIN ROTATION DONE|KEYCHAIN INSTALL FAIL|INSTALL-KEY ABORTED|MACSEC NOT INUSE|UNKNOWN_CAK|DOT1XD_MACSEC_SC_UNKNOWN_CAK_ERR" "$f" | tail -120; done'
}

capture_keychain_config()
{
    LABEL="$1"

    section "$LABEL - MACsec/keychain config"

    for item in $QKD_CAS; do
        CA=$(echo "$item" | cut -d: -f1)
        KC=$(echo "$item" | cut -d: -f2)

        run_cli "CA config $CA" \
            "show configuration security macsec connectivity-association $CA | display set"

        run_cli "authentication-key-chain $KC" \
            "show configuration security authentication-key-chains key-chain $KC | display set"
    done
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
    
    capture_qkd_state "$LABEL"
    
    capture_keychain_config "$LABEL"
    
    run_cli "LACP interfaces" \
        'show lacp interfaces | no-more'

    run_cli "Aggregated Ethernet terse" \
        'show interfaces terse | match "ae|et-"'

    run_cli "ISIS adjacency" \
        'show isis adjacency | no-more'

    run_cli "Routes to ring loopbacks" \
        'show route 10.100.255.0/24 exact'
}

capture_recent_events()
{
    LABEL="$1"

    section "$LABEL - recent event/log scan"

    run_cli "messages critical MACsec/MKA/LACP/ISIS events" \
    'show log messages | match "DOT1XD_MACSEC_SC_UNKNOWN_CAK_ERR|MACSEC_SC_CAK_ACTIVATED|MACSEC_SC_PRIMARY_CAK_IN_USE|LACP.*Detached|LACP.*Expired|LACP.*Defaulted|ADJDOWN|RPD_ISIS.*DOWN|link down|commit failed|authentication-key-chains not defined|May not be configured" | last 160'

    run_shell "qkd debug recent errors" \
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

        RC=$?

        if [ "$RC" -ne 0 ]; then
            log "!!! PING COMMAND RETURNED NON-ZERO rc=$RC dst=$DST name=$NAME"
        fi
    done

    run_cli "MACsec state after round $ROUND_ID" \
        'show security macsec connections | match "Interface name|CA name|Status: inuse"'

    run_cli "MKA summary after round $ROUND_ID" \
        'show security mka sessions | no-more'
    
    capture_qkd_state "ROUND $ROUND_ID"
    
    capture_qkd_timeline "ROUND $ROUND_ID"

    run_cli "LACP quick check after round $ROUND_ID" \
        'show lacp interfaces | match "Aggregated interface|LACP state|Collecting|Distributing|Detached|Expired|Defaulted|Synchronization"'

    run_cli "Recent critical LACP/MKA/MACsec/ISIS messages after round $ROUND_ID" \
    'show log messages | match "DOT1XD_MACSEC_SC_UNKNOWN_CAK_ERR|MACSEC_SC_CAK_ACTIVATED|MACSEC_SC_PRIMARY_CAK_IN_USE|LACP.*Detached|LACP.*Expired|LACP.*Defaulted|ADJDOWN|RPD_ISIS.*DOWN|link down|commit failed" | last 60'

}

summary()
{
    section "FINAL SUMMARY AND FAILURE MARKERS"

    log "Result file: $OUT"
    log

    run_shell "Count ping command failures" \
        "grep -c 'PING COMMAND RETURNED NON-ZERO' '$OUT'"

    run_shell "Count packet loss lines not 0%" \
        "grep -E 'packet loss' '$OUT' | grep -v '0% packet loss' | wc -l"

    run_shell "Count LACP bad markers" \
        "grep -Ei 'Detached|Expired|Defaulted|LACP.*down' '$OUT' | wc -l"

    run_shell "Count ISIS adjacency down markers" \
        "grep -Ei 'ADJDOWN|RPD_ISIS.*DOWN|adjacency.*down' '$OUT' | wc -l"

    run_shell "Count MACsec not-inuse / commit failures" \
        "grep -Ei 'MACSEC NOT INUSE|commit failed|authentication-key-chains not defined|May not be configured|KEYCHAIN INSTALL FAIL|INSTALL-KEY ABORTED' '$OUT' | wc -l"

    run_shell "Count transient UNKNOWN CAK events" \
        "grep -c 'DOT1XD_MACSEC_SC_UNKNOWN_CAK_ERR' '$OUT'"

    run_shell "Count MKA key confirmations" \
        "grep -c 'MKA KEY CONFIRMED' '$OUT'"

    run_shell "Count pending promotions" \
        "grep -c 'PENDING KEY PROMOTED' '$OUT'"

    run_shell "Count pending-not-confirmed skips" \
        "grep -c 'PENDING_KEY_NOT_CONFIRMED' '$OUT'"

    run_shell "Show QKD scheduled/pending/promoted timeline" \
        "grep -Ei 'KEYCHAIN ROTATION START|INSTALL-KEY SCHEDULE|STATE SAVED|pending_key_id|next_start_time|MKA KEY CONFIRMED|PENDING KEY PROMOTED|PENDING_KEY_NOT_CONFIRMED|KEYCHAIN ROTATION DONE' '$OUT' | tail -200"

    log
    log "Expected healthy scheduled behavior:"
    log "  1. KEYCHAIN ROTATION START start_time=<future local time>"
    log "  2. STATE SAVED pending_key_id=<key> next_start_time=<future>"
    log "  3. Before start_time: ROTATION SKIP reason=PENDING_KEY_NOT_CONFIRMED"
    log "  4. Around start_time: MKA KEY CONFIRMED"
    log "  5. Then: PENDING KEY PROMOTED"
    log "  6. Ping loss = 0%"
    log "  7. LACP stays Current / Collecting distributing"
}

section "Ring MACsec/QKD/MKA scheduled rotation test"
log "Source loopback: $SRC"
log "Duration: ${DURATION}s"
log "Ping count per destination per round: ${COUNT_PER_ROUND}"
log "Sleep between rounds: ${SLEEP_BETWEEN_ROUNDS}s"
log "Start: $(date)"
log "Start UTC: $(date -u)"
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