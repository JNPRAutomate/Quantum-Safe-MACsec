"""Administrative access to existing QBT instances without exposing master keys."""

import shlex


def admin_command(container, *arguments):
    script = (
        'set -eu; '
        'test -s /run/secrets/master-key-id; '
        'test -s /run/secrets/master-key-bytes; '
        'export KME_CRYPTO_MASTER_KEY_ID="$(cat /run/secrets/master-key-id)"; '
        'export KME_CRYPTO_MASTER_KEY_BYTES="$(cat /run/secrets/master-key-bytes)"; '
        'exec qbt-kme "$@"'
    )
    return shlex.join(
        ["docker", "exec", container, "/bin/sh", "-c", script, "qbt-admin", *arguments]
    )
