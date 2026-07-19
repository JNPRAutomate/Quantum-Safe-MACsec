CONFIG = {

    # repo layout
    "runtime_dir": "config/runtime",
    "inventory_dir": "config/inventory",
    "inventory_input_dir": "config/inventory/input",
    "templates_dir": "config/templates",

    # qkd runtime policy
    "qkd_policy_file": "config/inventory/qkd_policy.yaml",
    "runtime_qkd_policy_file": "config/runtime/qkd_policy.yaml",

    # artifacts
    "artifacts_dir": "artifacts",

    # certs
    "certs_dir": "certs",

    # pki
    "self_signed_dir": "certs/self_signed",
    "hierarchical_dir": "certs/hierarchical_ca",
}


# -------------------------------------------------
# PKI CONFIGURATION (GLOBAL DEFAULTS)
# -------------------------------------------------

PKI = {

    "COUNTRY": "IT",
    "ORG": "HPE",

    # validity
    "KEY_SIZE": 4096,
    "VALIDITY_DAYS": 365,

    # remote dir on devices
    "REMOTE_CERT_DIR": "/var/db/scripts/certs",

    # SAE naming
    "SAE_PREFIX": "sae",
    "SAE_PAD": 3,
    "SAE_SEPARATOR": "-",

    # KME naming
    "KME_PREFIX": "kme",
    "KME_PAD": 3,
    "KME_SEPARATOR": "-",

    # self-signed PKI naming
    "SELF_SIGNED_CA_CERT_NAME": "offbox_rootCA.crt",
    "SELF_SIGNED_CA_KEY_NAME": "offbox_rootCA.key",
}


QKD = {
    # On-box script
    "SCRIPT_NAME": "qkd_onbox.py",

    # Runtime identity used for local on-box execution and Junos event script user.
    "SCRIPT_USER": "macsec_user",

    # Dedicated low-privilege identity used only for peer SSH transport
    # (master -> slave send-command/status for key-id workflow).
    "PEER_CMD_USER": "etsi_peer_view",

    # Privileged deploy/cleanup user.
    #
    # Used only by the offbox orchestrator for:
    #   - cleaning stale root-owned runtime files
    #   - installing scripts
    #   - installing event-options config
    #   - fixing ownership/permissions
    "DEPLOY_USER": "root",

    # Junos script directories
    "SCRIPT_DIR": "/var/db/scripts",
    "OP_SCRIPT_DIR": "/var/db/scripts/op",
    "EVENT_SCRIPT_DIR": "/var/db/scripts/event",

    # Full remote path where the op script must exist
    "REMOTE_OP_SCRIPT_PATH": "/var/db/scripts/op/qkd_onbox.py",

    # External runtime JSON files consumed by qkd_onbox.py
    "ONBOX_CONFIG_DIR": "/var/db/scripts/op",
    "ONBOX_CONFIG_JSON_NAME": "qkd_onbox_config.json",
    "ONBOX_INVENTORY_JSON_NAME": "qkd_onbox_inventory.json",

    # File permissions applied at deploy time
    # 0555: executable/readable but not writable (including owner)
    "ONBOX_SCRIPT_MODE": "0555",
    # 0664: JSON editable by owner/group (customer-operable)
    "ONBOX_JSON_MODE": "0664",

    # Runtime files on Junos
    "REMOTE_TMP_DIR": "/var/tmp",
    "LOG_FILE": "/var/home/macsec_user/qkd-state/logs/qkd_debug.log",
    "STATE_FILE_PREFIX": "/var/home/macsec_user/qkd-state/qkd_db",
    "LOCK_FILE_PREFIX": "/var/home/macsec_user/qkd-state/qkd_onbox",

    # Log rotation inside qkd_onbox.py
    "LOG_MAX_BYTES": 10485760,
    "LOG_BACKUP_COUNT": 5,

    # SSH runtime identity
    "SSH_HOME_BASE": "/var/home",
    "SSH_KEY_NAME": "qkd_id_ed25519",
    # Private key used by SCRIPT_USER to connect as PEER_CMD_USER on peers.
    "PEER_CMD_SSH_KEY_NAME": "qkd_peer_cmd_ed25519",
    "SSH_KEY_TYPE": "ed25519",
    # Used only when SSH_KEY_TYPE is rsa.
    "SSH_KEY_BITS": 4096,
    "SSH_KEY_COMMENT": "qkd-orchestrator-andrea.terren@hpe.com",

    # Derived convention:
    #
    # SSH home:
    #   /var/home/{SCRIPT_USER}
    #
    # SSH dir:
    #   /var/home/{SCRIPT_USER}/.ssh
    #
    # SSH private key:
    #   /var/home/{SCRIPT_USER}/.ssh/{SSH_KEY_NAME}
    #
    # SSH public key:
    #   /var/home/{SCRIPT_USER}/.ssh/{SSH_KEY_NAME}.pub
    #
    # authorized_keys:
    #   /var/home/{SCRIPT_USER}/.ssh/authorized_keys

    # Peer command authorized_keys target:
    #   /var/home/{PEER_CMD_USER}/.ssh/authorized_keys

    # Runtime policy
    "ENFORCE_RUNTIME_USER": True,
    "CLEAN_RUNTIME_ON_DEPLOY": True,
    "CLEAN_RUNTIME_ON_CLEAN": True,

    # If True, qkd_onbox.py must refuse runtime actions when executed
    # as root or as a user different from SCRIPT_USER.
    #
    # Important: action=status is NOT read-only anymore because it can
    # promote pending keys and save state.
    "REFUSE_WRONG_RUNTIME_USER": True,
    "VALIDATE_VERBOSE": False
}
