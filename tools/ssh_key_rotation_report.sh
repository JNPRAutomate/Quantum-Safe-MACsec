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

# SSH Helper function - tries key first, then password
ssh_cmd() {
    local key_path="$1"
    local user="$2"
    local host="$3"
    local cmd="$4"
    local password="$5"
    
    if [ -f "$key_path" ]; then
        # Try with SSH key
        ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -i "$key_path" "$user@$host" "$cmd" 2>/dev/null
    elif [ -n "$password" ]; then
        # Try with password (requires sshpass)
        if command -v sshpass &> /dev/null; then
            sshpass -p "$password" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 "$user@$host" "$cmd" 2>/dev/null
        else
            echo "ERROR: sshpass not installed, cannot use password auth" >&2
            return 1
        fi
    else
        return 1
    fi
}

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
    
    # Get rotation count and last timestamp
    rotation_info=$(ssh_cmd "$key_path" "$user" "$device_ip" \
        "grep 'PEER SSH KEY ROTATION COMPLETE' /var/home/macsec_user/qkd-state/logs/qkd_ssh_rotation_sae-${sae_id}.log 2>/dev/null | tail -1" \
        "$PASSWORD" 2>/dev/null || echo "")
    
    if [ -z "$rotation_info" ]; then
        rotation_count=0
        last_timestamp="N/A"
    else
        rotation_count=$(ssh_cmd "$key_path" "$user" "$device_ip" \
            "grep -c 'PEER SSH KEY ROTATION COMPLETE' /var/home/macsec_user/qkd-state/logs/qkd_ssh_rotation_sae-${sae_id}.log 2>/dev/null" \
            "$PASSWORD" 2>/dev/null || echo "0")
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
