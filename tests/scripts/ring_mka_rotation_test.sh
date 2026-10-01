#!/bin/sh

set -eu

# All lab-specific values come from the environment. They are normally set by
# tests/scripts/run_onbox_test.py from the selected inventory; there are no
# built-in lab defaults.
#
#   SRC           source address for pings (local link address)
#   DESTS         whitespace-separated name:address ping targets
#   QKD_IFACES    whitespace-separated MACsec interfaces to inspect
#   QKD_CAS       whitespace-separated CA:keychain pairs to inspect
#   QKD_LOG_GLOB  on-box qkd_debug log glob
#   ROUTE_PREFIX  prefix(es) shown in the route snapshot

require_env()
{
    for name in "$@"; do
        eval "value=\${$name:-}"
        if [ -z "$value" ]; then
            echo "[ERROR] $name is not set; run through tests/scripts/run_onbox_test.py or export it" >&2
            exit 2
        fi
    done
}

require_env SRC DESTS QKD_IFACES QKD_CAS QKD_LOG_GLOB ROUTE_PREFIX

DURATION="${1:-${DURATION:-720}}"
COUNT_PER_ROUND="${2:-${COUNT_PER_ROUND:-5}}"
SLEEP_BETWEEN_ROUNDS="${3:-${SLEEP_BETWEEN_ROUNDS:-2}}"
OUT_DIR="${OUT_DIR:-/var/tmp}"
LOG_PREFIX="${LOG_PREFIX:-ring_mka_rotation_test}"

TEST_TS=$(date '+%Y%m%d_%H%M%S')
OUT="${OUT_DIR}/${LOG_PREFIX}_${TEST_TS}.log"

START=$(date +%s)
END=$((START + DURATION))
ROUND=1

# Test window start, in the formats of the QKD logs (YYYY-MM-DD HH:MM:SS) and
# of syslog (MMDDHHMMSS, built from "Mon DD HH:MM:SS"). The final summary only
# counts events at or after these marks, read once from their source, so
# repeated per-round dumps in the report cannot inflate it.
WINDOW_QKD_TS=$(date '+%Y-%m-%d %H:%M:%S')
WINDOW_SYSLOG_TS=$(date '+%m%d%H%M%S')
LAST_QKD_TS="$WINDOW_QKD_TS"
LAST_SYSLOG_TS="$WINDOW_SYSLOG_TS"

PINGS_SENT=0
PINGS_RECEIVED=0
PING_LOSS_ROUNDS=0
PING_CMD_FAILURES=0

SYSLOG_PATTERN='DOT1XD_MACSEC_SC_UNKNOWN_CAK_ERR|MACSEC_SC_CAK_ACTIVATED|MACSEC_SC_PRIMARY_CAK_IN_USE|LACP.*Detached|LACP.*Expired|LACP.*Defaulted|ADJDOWN|RPD_ISIS.*DOWN|RPD_OSPF_NBRDOWN|SNMP_TRAP_LINK_DOWN|link down|commit failed|authentication-key-chains not defined|May not be configured'

QKD_EVENT_PATTERN='STATE RECONCILED FROM ROUTER|ROLLING_REPLACEMENT (START|DONE)|RING_COMPLETION (START|DONE)|KEYCHAIN INSTALL (OK|FAIL)|KEYCHAIN BATCH INSTALL FAIL|MKA KEY CONFIRMED|PENDING KEY PROMOTED|ROTATION (SKIP|BLOCKED)|INSTALL-KEY ABORTED|\[ERROR\]'

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

# QKD log lines with a timestamp at or after $1 (YYYY-MM-DD HH:MM:SS), from
# every file of QKD_LOG_GLOB and its first rotation, without duplicates.
qkd_lines_since()
{
    for f in $QKD_LOG_GLOB; do
        for g in "$f.1" "$f"; do
            [ -f "$g" ] || continue
            awk -v since="$1" '/^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] / && substr($0, 1, 19) >= since' "$g"
        done
    done | sort -u
}

# Syslog lines matching SYSLOG_PATTERN with a timestamp at or after $1
# (MMDDHHMMSS), oldest first and without duplicates. Junos rotates
# /var/log/messages often, so the files in $2 (default: the two previous
# rotations and the current file) are read. The audit lines of this script's
# own CLI commands are excluded.
syslog_events_since()
{
    for logfile in ${2:-messages.1.gz messages.0.gz messages}; do
        cli -c "show log $logfile | match \"$SYSLOG_PATTERN\" | except UI_CMDLINE_READ_LINE | no-more" 2>&1
    done |
        awk -v since="$1" '
            BEGIN {
                split("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec", names, " ")
                for (i = 1; i <= 12; i++) month[names[i]] = sprintf("%02d", i)
            }
            ($1 in month) && $3 ~ /^[0-9][0-9]:[0-9][0-9]:[0-9][0-9]$/ {
                split($3, t, ":")
                if (month[$1] sprintf("%02d", $2) t[1] t[2] t[3] >= since && !seen[$0]++) print
            }'
}

count_lines()
{
    awk -v pattern="$1" '$0 ~ pattern { n++ } END { print n + 0 }'
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

    section "$LABEL - new QKD key events since $LAST_QKD_TS"

    NEXT_QKD_TS=$(date '+%Y-%m-%d %H:%M:%S')
    qkd_lines_since "$LAST_QKD_TS" | grep -E "$QKD_EVENT_PATTERN" | tee -a "$OUT" || true
    LAST_QKD_TS="$NEXT_QKD_TS"
}

capture_new_syslog_events()
{
    LABEL="$1"

    section "$LABEL - new syslog events since $LAST_SYSLOG_TS"

    NEXT_SYSLOG_TS=$(date '+%m%d%H%M%S')
    syslog_events_since "$LAST_SYSLOG_TS" "messages.0.gz messages" | tee -a "$OUT"
    LAST_SYSLOG_TS="$NEXT_SYSLOG_TS"
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

    run_cli "OSPF neighbors" \
        'show ospf neighbor | no-more'

    for prefix in $ROUTE_PREFIX; do
        run_cli "Route to $prefix" "show route $prefix exact"
    done
}

capture_recent_events()
{
    LABEL="$1"

    section "$LABEL - recent event/log scan"

    run_cli "messages critical MACsec/MKA/LACP/routing events" \
        "show log messages | match \"$SYSLOG_PATTERN\" | except UI_CMDLINE_READ_LINE | last 160"

    run_shell "qkd debug recent errors" \
        "for f in $QKD_LOG_GLOB; do [ -f \"\$f\" ] || continue; echo \"### \$f\"; grep -E \"ERROR|FAIL|FAILED|KEYCHAIN|MKA|PENDING|PROMOTED|ROTATION|BOOTSTRAP|INSTALL-KEY|MACSEC|SSH|DEC|ENC|KME\" \"\$f\" | tail -80; done"

    run_shell "qkd debug recent tail" \
        "for f in $QKD_LOG_GLOB; do [ -f \"\$f\" ] || continue; echo \"### \$f\"; tail -40 \"\$f\"; done"
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

        if PING_OUT=$(cli -c "ping $DST source $SRC rapid count $COUNT_PER_ROUND" 2>&1); then
            RC=0
        else
            RC=$?
        fi
        printf '%s\n' "$PING_OUT" | tee -a "$OUT"

        COUNTS=$(printf '%s\n' "$PING_OUT" |
            sed -nE 's/^([0-9]+) packets transmitted, ([0-9]+) (packets )?received.*/\1 \2/p' | tail -1)

        if [ "$RC" -ne 0 ] || [ -z "$COUNTS" ]; then
            PING_CMD_FAILURES=$((PING_CMD_FAILURES + 1))
            PINGS_SENT=$((PINGS_SENT + COUNT_PER_ROUND))
            PING_LOSS_ROUNDS=$((PING_LOSS_ROUNDS + 1))
            log "!!! PING FAILED rc=$RC dst=$DST name=$NAME"
            continue
        fi

        set -- $COUNTS
        PINGS_SENT=$((PINGS_SENT + $1))
        PINGS_RECEIVED=$((PINGS_RECEIVED + $2))
        if [ "$2" -lt "$1" ]; then
            PING_LOSS_ROUNDS=$((PING_LOSS_ROUNDS + 1))
            log "!!! PING LOSS sent=$1 received=$2 dst=$DST name=$NAME"
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

    capture_new_syslog_events "ROUND $ROUND_ID"
}

# Counts are taken once, from the device syslog and the QKD logs restricted to
# the test window, never from this report (which repeats the same lines every
# round and also contains the commands that were run).
summary()
{
    section "FINAL SUMMARY (test window from $WINDOW_QKD_TS)"

    WINDOW_SYSLOG=$(syslog_events_since "$WINDOW_SYSLOG_TS")
    WINDOW_QKD=$(qkd_lines_since "$WINDOW_QKD_TS" | grep -E "$QKD_EVENT_PATTERN" || true)
    FAILURES=0

    subsection "Traffic"
    log "Pings sent:              $PINGS_SENT"
    log "Pings received:          $PINGS_RECEIVED"
    log "Ping calls with loss:    $PING_LOSS_ROUNDS"
    log "Ping command failures:   $PING_CMD_FAILURES"
    if [ "$PING_LOSS_ROUNDS" -ne 0 ] || [ "$PING_CMD_FAILURES" -ne 0 ] || [ "$PINGS_SENT" -eq 0 ]; then
        FAILURES=$((FAILURES + 1))
    fi

    subsection "Syslog events in the test window"
    for iface in $QKD_IFACES; do
        IFACE_LINES=$(printf '%s\n' "$WINDOW_SYSLOG" | grep -E "$iface([^0-9]|\$)" || true)
        ACTIVATED=$(printf '%s\n' "$IFACE_LINES" | count_lines 'MACSEC_SC_CAK_ACTIVATED')
        PRIMARY=$(printf '%s\n' "$IFACE_LINES" | count_lines 'MACSEC_SC_PRIMARY_CAK_IN_USE')
        UNKNOWN=$(printf '%s\n' "$IFACE_LINES" | count_lines 'UNKNOWN_CAK_ERR')
        log "$iface: CAK activated=$ACTIVATED primary CAK in use=$PRIMARY unknown CAK errors=$UNKNOWN"
        [ "$UNKNOWN" -eq 0 ] || FAILURES=$((FAILURES + 1))
        [ "$ACTIVATED" -gt 0 ] || log "  note: no CAK switch on $iface in the window (duration shorter than the rekey interval?)"
    done
    LACP_BAD=$(printf '%s\n' "$WINDOW_SYSLOG" | count_lines 'LACP.*(Detached|Expired|Defaulted)')
    ROUTING_DOWN=$(printf '%s\n' "$WINDOW_SYSLOG" | count_lines 'ADJDOWN|RPD_ISIS.*DOWN|RPD_OSPF_NBRDOWN')
    LINK_DOWN=$(printf '%s\n' "$WINDOW_SYSLOG" | count_lines 'SNMP_TRAP_LINK_DOWN|link down')
    COMMIT_FAIL=$(printf '%s\n' "$WINDOW_SYSLOG" | count_lines 'commit failed|authentication-key-chains not defined|May not be configured')
    log "LACP bad states:         $LACP_BAD"
    log "Routing adjacency down:  $ROUTING_DOWN"
    log "Link down:               $LINK_DOWN"
    log "Commit failures:         $COMMIT_FAIL"
    if [ "$LACP_BAD" -ne 0 ] || [ "$ROUTING_DOWN" -ne 0 ] || [ "$LINK_DOWN" -ne 0 ] || [ "$COMMIT_FAIL" -ne 0 ]; then
        FAILURES=$((FAILURES + 1))
    fi

    subsection "QKD log events in the test window"
    for iface in $QKD_IFACES; do
        IFACE_LINES=$(printf '%s\n' "$WINDOW_QKD" | grep -F "[$iface]" || true)
        log "$iface:"
        log "  state reconciled from router: $(printf '%s\n' "$IFACE_LINES" | count_lines 'STATE RECONCILED FROM ROUTER')"
        log "  MKA key confirmed:            $(printf '%s\n' "$IFACE_LINES" | count_lines 'MKA KEY CONFIRMED')"
        log "  pending key promoted:         $(printf '%s\n' "$IFACE_LINES" | count_lines 'PENDING KEY PROMOTED')"
        log "  ring refills done:            $(printf '%s\n' "$IFACE_LINES" | count_lines '(ROLLING_REPLACEMENT|RING_COMPLETION) DONE')"
        log "  keychain installs OK:         $(printf '%s\n' "$IFACE_LINES" | count_lines 'KEYCHAIN INSTALL OK')"
        log "  rotation skips:               $(printf '%s\n' "$IFACE_LINES" | count_lines 'ROTATION SKIP')"
        INSTALL_FAIL=$(printf '%s\n' "$IFACE_LINES" | count_lines 'KEYCHAIN (BATCH )?INSTALL FAIL|INSTALL-KEY ABORTED')
        BLOCKED=$(printf '%s\n' "$IFACE_LINES" | count_lines 'ROTATION BLOCKED')
        ERRORS=$(printf '%s\n' "$IFACE_LINES" | count_lines '[[]ERROR[]]')
        log "  install failures:             $INSTALL_FAIL"
        log "  rotation blocked:             $BLOCKED"
        log "  ERROR lines:                  $ERRORS"
        if [ "$INSTALL_FAIL" -ne 0 ] || [ "$BLOCKED" -ne 0 ] || [ "$ERRORS" -ne 0 ]; then
            FAILURES=$((FAILURES + 1))
        fi
    done

    subsection "QKD key events in the test window"
    printf '%s\n' "$WINDOW_QKD" | grep -Ev 'ROTATION SKIP' | tee -a "$OUT" || true

    subsection "Syslog events in the test window"
    printf '%s\n' "$WINDOW_SYSLOG" | tee -a "$OUT"

    log
    log "Expected healthy ring behaviour on each QKD interface:"
    log "  1. CAK activated about once per rekey interval, no unknown CAK errors"
    log "  2. STATE RECONCILED FROM ROUTER after each CAK switch"
    log "  3. ROLLING_REPLACEMENT/RING_COMPLETION DONE when the ring is refilled"
    log "  4. ROTATION SKIP while the ring is still full (normal)"
    log "  5. No install failures, no ROTATION BLOCKED, no ERROR lines"
    log "  6. 0% ping loss; no LACP, routing adjacency or link down events"
    log
    if [ "$FAILURES" -eq 0 ]; then
        log "RESULT: PASS"
    else
        log "RESULT: FAIL ($FAILURES failed check groups)"
    fi
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

# At least one round always runs, even when the initial snapshot outlasts DURATION.
while :; do
    ping_round "$ROUND"
    ROUND=$((ROUND + 1))
    [ "$(date +%s)" -lt "$END" ] || break
    sleep "$SLEEP_BETWEEN_ROUNDS"
done

capture_operational_snapshot "FINAL"
capture_recent_events "FINAL"
summary

section "Test completed at $(date)"
log "Result file: $OUT"

[ "$FAILURES" -eq 0 ] || exit 1
