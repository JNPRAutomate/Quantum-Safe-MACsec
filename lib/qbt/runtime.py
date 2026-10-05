"""Administrative access to existing QBT instances without exposing master keys."""

import shlex


def admin_script_command(container, script, *arguments):
    script = (
        'set -eu; '
        'test -s /run/secrets/master-key-id; '
        'test -s /run/secrets/master-key-bytes; '
        'export KME_CRYPTO_MASTER_KEY_ID="$(cat /run/secrets/master-key-id)"; '
        'export KME_CRYPTO_MASTER_KEY_BYTES="$(cat /run/secrets/master-key-bytes)"; '
        + script
    )
    return shlex.join(
        ["docker", "exec", container, "/bin/sh", "-c", script, "qbt-admin", *arguments]
    )


def admin_command(container, *arguments):
    return admin_script_command(container, 'exec qbt-kme "$@"', *arguments)
