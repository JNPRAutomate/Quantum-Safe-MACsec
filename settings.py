CONFIG = {
    "runtime_dir": "config/runtime",
    "inventory_dir": "config/inventory",
    "templates_dir": "config/templates"
}


# -------------------------------------------------
# PKI CONFIGURATION (GLOBAL DEFAULTS)
# -------------------------------------------------

PKI = {
    "C": "IT",
    "O": "Juniper Networks",
    "KEY_USAGE": "digitalSignature,keyEncipherment",
    "EXT_KEY_USAGE": "clientAuth,serverAuth",

    # validity
    "DAYS": 365,
    "KEY_SIZE": 4096,

    # ✅ CA naming (IMPORTANT)
    "CA_CERT_NAME": "offbox_rootCA.crt",
    "CA_KEY_NAME": "offbox_rootCA.key",

    # ✅ remote dir on devices
    "REMOTE_CERT_DIR": "/var/db/scripts/certs",

    # ✅ SAE naming
    "SAE_PREFIX": "sae",
    "SAE_PAD": 3,
}


QKD = {
    # On-box script
    "SCRIPT_NAME": "qkd_onbox.py",

    # Single runtime identity.
    #
    # This user must be used consistently for:
    #   - Junos event-options python-script-user
    #   - qkd_onbox.py runtime execution
    #   - peer SSH-exec from one router to another
    #   - runtime state/log/lock ownership under /var/tmp
    #
    # Root must only be used by the orchestrator for deploy/clean/setup.
    "SCRIPT_USER": "admin",

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

    # Runtime files on Junos
    "REMOTE_TMP_DIR": "/var/tmp",
    "LOG_FILE": "/var/tmp/qkd_debug.log",
    "STATE_FILE_PREFIX": "/var/tmp/qkd_db",
    "LOCK_FILE_PREFIX": "/var/tmp/qkd_onbox",

    # Log rotation inside qkd_onbox.py
    "LOG_MAX_BYTES": 10485760,
    "LOG_BACKUP_COUNT": 5,

    # SSH runtime identity
    "SSH_HOME_BASE": "/var/home",
    "SSH_KEY_NAME": "qkd_id_rsa",
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
}
