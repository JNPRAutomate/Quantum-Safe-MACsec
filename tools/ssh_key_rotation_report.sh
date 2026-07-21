#!/bin/bash
#
# SSH Key Rotation Report
# Collects PEER SSH KEY ROTATION COMPLETE counts from all 11 devices
# Shows number of successful rotations per device + last rotation timestamp
#
# Usage: sh tools/ssh_key_rotation_report.sh [password]
#

# Auto-detect workspace root (parent of tools directory)
WORKSPACE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
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

# Check if password is provided as argument
PASSWORD="${1:-}"

# If no password provided, check if keys exist - if not, prompt for password
if [ -z "$PASSWORD" ]; then
    key_sample="certs/hierarchical_ca/juniper_pki/certs/sae-001/sae-001_id_ed25519"
    if [ ! -f "$key_sample" ]; then
        read -sp "Enter SSH password (same for all devices): " PASSWORD
        echo ""
    fi
fi

# Collect results
TOTAL_ROTATIONS=0
DEVICES_WITH_ROTATIONS=0

for sae_id in 001 002 003 004 005 006 007 008 009 010 011; do
    device_info="${DEVICES[$sae_id]}"
    device_name="${device_info%%:*}"
    device_ip="${device_info##*:}"
    
    key_path="certs/hierarchical_ca/juniper_pki/certs/sae-${sae_id}/sae-${sae_id}_id_ed25519"
    user="labuser"
    log_file="/var/home/macsec_user/qkd-state/logs/qkd_ssh_rotation_sae-${sae_id}.log"
    
    # Try to get rotation count via SSH
    rotation_count=0
    last_timestamp="N/A"
    
    if [ -f "$key_path" ]; then
        # Use SSH key if available
        result=$(ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o UserKnownHostsFile=/dev/null \
            -i "$key_path" "$user@$device_ip" \
            "wc -l < '$log_file' 2>/dev/null || echo 0" 2>/dev/null)
        if [ $? -eq 0 ] && [ -n "$result" ] && [ "$result" != "0" ]; then
            rotation_count=$(ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o UserKnownHostsFile=/dev/null \
                -i "$key_path" "$user@$device_ip" \
                "grep -c 'PEER SSH KEY ROTATION COMPLETE' '$log_file' 2>/dev/null" 2>/dev/null || echo "0")
            last_line=$(ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o UserKnownHostsFile=/dev/null \
                -i "$key_path" "$user@$device_ip" \
                "tail -1 '$log_file' 2>/dev/null" 2>/dev/null)
            last_timestamp=$(echo "$last_line" | awk '{print $1, $2}')
        fi
    elif [ -n "$PASSWORD" ]; then
        # Use password if sshpass available
        if command -v sshpass &> /dev/null; then
            result=$(sshpass -p "$PASSWORD" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o UserKnownHostsFile=/dev/null \
                "$user@$device_ip" \
                "wc -l < '$log_file' 2>/dev/null || echo 0" 2>/dev/null)
            if [ $? -eq 0 ] && [ -n "$result" ] && [ "$result" != "0" ]; then
                rotation_count=$(sshpass -p "$PASSWORD" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o UserKnownHostsFile=/dev/null \
                    "$user@$device_ip" \
                    "grep -c 'PEER SSH KEY ROTATION COMPLETE' '$log_file' 2>/dev/null" 2>/dev/null || echo "0")
                last_line=$(sshpass -p "$PASSWORD" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o UserKnownHostsFile=/dev/null \
                    "$user@$device_ip" \
                    "tail -1 '$log_file' 2>/dev/null" 2>/dev/null)
                last_timestamp=$(echo "$last_line" | awk '{print $1, $2}')
            fi
        fi
    fi
    
    # Ensure rotation_count is numeric
    rotation_count=$(echo "$rotation_count" | grep -oE '^[0-9]+' || echo "0")
    
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
