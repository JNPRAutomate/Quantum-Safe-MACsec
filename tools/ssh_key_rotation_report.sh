#!/bin/bash
#
# SSH Key Rotation Report - Juniper Device Compatible
# Collects PEER SSH KEY ROTATION COMPLETE counts from all 11 devices
# Counts only ROTATION entries (excludes BOOTSTRAP)
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
    
    rotation_count=0
    last_timestamp="N/A"
    
    # Determine SSH command prefix
    if [ -f "$key_path" ]; then
        ssh_prefix="ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -i $key_path"
    elif [ -n "$PASSWORD" ]; then
        if command -v sshpass &> /dev/null; then
            ssh_prefix="sshpass -p '$PASSWORD' ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5"
        else
            ssh_prefix=""
        fi
    else
        ssh_prefix=""
    fi
    
    # If we have a valid SSH command, execute it
    if [ -n "$ssh_prefix" ]; then
        # Execute the command to get rotation count
        # Note: Juniper CLI requires 'start shell' to access Unix commands
        cmd="$ssh_prefix $user@$device_ip 'start shell ; grep -c \"PEER SSH KEY ROTATION\" $log_file 2>/dev/null ; exit'"
        result=$(eval "$cmd" 2>/dev/null || echo "0")
        
        # Extract numeric value safely - get last line (grep output after shell)
        rotation_count=$(echo "$result" | tail -1 | grep -oE '^[0-9]+' || echo "0")
        
        # Get last timestamp if rotations found
        if [ "$rotation_count" -gt 0 ] 2>/dev/null; then
            cmd_last="$ssh_prefix $user@$device_ip 'start shell ; grep \"PEER SSH KEY ROTATION\" $log_file 2>/dev/null | tail -1 ; exit'"
            last_line=$(eval "$cmd_last" 2>/dev/null | grep "PEER SSH KEY ROTATION")
            last_timestamp=$(echo "$last_line" | awk '{print $1, $2}')
        fi
    fi
    
    # Format output
    printf "%-8s %-15s │ Rotations: %2d │ Last: %s\n" \
        "sae-$sae_id" "$device_name" "$rotation_count" "$last_timestamp"
    
    if [ "$rotation_count" -gt 0 ] 2>/dev/null; then
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
    echo "║ Status: ⚠️  PARTIAL SYNCHRONIZATION - Check missing devices            ║"
else
    echo "║ Status: ❌ CRITICAL - Multiple devices not synchronized               ║"
fi
echo "╚════════════════════════════════════════════════════════════════════════╝"
