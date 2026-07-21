#!/bin/bash
#
# SSH Key Rotation Report
# Collects PEER SSH KEY ROTATION COMPLETE counts from all 11 devices
# Shows number of successful rotations per device + last rotation timestamp
#

set -e

WORKSPACE_ROOT="/Users/aterren/Lavoro 2026/quantum 2026/newMACSEC39_ready_for_git"
cd "$WORKSPACE_ROOT"

# Device mapping: sae_id -> (device_name, device_ip)
declare -A DEVICES=(
    [001]="MX1:100.123.113.151"
    [002]="MX2:100.123.113.152"
    [003]="MX3:100.123.113.2"
    [004]="MX4:100.123.113.4"
    [005]="MX5:100.123.113.3"
    [006]="MX6:100.123.113.1"
    [007]="ACX1:100.123.170.207"
    [008]="ACX2:100.123.170.200"
    [009]="ACX3:100.123.170.203"
    [010]="ACX4:100.123.170.204"
    [011]="ACX5:100.123.170.205"
)

echo "╔════════════════════════════════════════════════════════════════════════╗"
echo "║              SSH KEY ROTATION REPORT - ALL DEVICES                    ║"
echo "║                    $(date '+%Y-%m-%d %H:%M:%S')                          ║"
echo "╚════════════════════════════════════════════════════════════════════════╝"
echo ""

# Collect results
TOTAL_ROTATIONS=0
DEVICES_WITH_ROTATIONS=0

for sae_id in 001 002 003 004 005 006 007 008 009 010 011; do
    device_info="${DEVICES[$sae_id]}"
    device_name="${device_info%%:*}"
    device_ip="${device_info##*:}"
    
    # Determine if MX or ACX for proper SSH key path
    if [[ "$device_name" =~ ^MX ]]; then
        key_path="certs/hierarchical_ca/juniper_pki/certs/sae-${sae_id}/sae-${sae_id}_id_ed25519"
        user="labuser"
    else
        key_path="certs/hierarchical_ca/juniper_pki/certs/sae-${sae_id}/sae-${sae_id}_id_ed25519"
        user="labuser"
    fi
    
    # Get rotation count and last timestamp
    rotation_info=$(ssh -i "$key_path" "$user@$device_ip" \
        "cd /var/home/macsec_user/qkd-state/logs/ && \
         grep 'PEER SSH KEY ROTATION COMPLETE' qkd_ssh_rotation_sae-${sae_id}.log 2>/dev/null | tail -1" 2>/dev/null || echo "")
    
    if [ -z "$rotation_info" ]; then
        rotation_count=0
        last_timestamp="N/A"
    else
        rotation_count=$(ssh -i "$key_path" "$user@$device_ip" \
            "grep -c 'PEER SSH KEY ROTATION COMPLETE' /var/home/macsec_user/qkd-state/logs/qkd_ssh_rotation_sae-${sae_id}.log 2>/dev/null" 2>/dev/null || echo "0")
        last_timestamp=$(echo "$rotation_info" | awk '{print $1, $2}')
    fi
    
    # Format output
    printf "%-8s %-15s │ Rotations: %2d │ Last: %s\n" \
        "sae-$sae_id" "$device_name" "$rotation_count" "$last_timestamp"
    
    if [ "$rotation_count" -gt 0 ]; then
        TOTAL_ROTATIONS=$((TOTAL_ROTATIONS + rotation_count))
        DEVICES_WITH_ROTATIONS=$((DEVICES_WITH_ROTATIONS + 1))
    fi
done

echo ""
echo "╔════════════════════════════════════════════════════════════════════════╗"
echo "║ SUMMARY:                                                               ║"
printf "║ • Total Rotation Events: %-5d                                           ║\n" "$TOTAL_ROTATIONS"
printf "║ • Devices with Rotations: %-4d/11                                       ║\n" "$DEVICES_WITH_ROTATIONS"
echo "║                                                                        ║"
if [ "$DEVICES_WITH_ROTATIONS" -eq 11 ]; then
    echo "║ Status: ✅ ALL DEVICES SYNCHRONIZED                                    ║"
elif [ "$DEVICES_WITH_ROTATIONS" -ge 8 ]; then
    echo "║ Status: ⚠️  PARTIAL SYNCHRONIZATION - Check ACX devices                ║"
else
    echo "║ Status: ❌ CRITICAL - Multiple devices not synchronized               ║"
fi
echo "╚════════════════════════════════════════════════════════════════════════╝"
