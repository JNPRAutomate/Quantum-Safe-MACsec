# Manual backup of the licensed QBT EVO containers

## Purpose and safety

This is a manual safeguard, separate from `qbt_orchestrator.py`. The
orchestrator does not create, copy, or verify these backups. A
successful preflight does not mean a backup exists.

Archive the complete persistent root from its own router:

| Router | Container | Persistent root |
| --- | --- | --- |
| EVO1 | `qbt-evo1` | `/var/db/qbt/evo1` |
| EVO2 | `qbt-evo2` | `/var/db/qbt/evo2` |

The archive includes the database and licence state, machine ID, master-key
identity and bytes, licence key, and staged offline activation file. These are
secrets. Keep the off-router copy in a private directory outside both routers
and the Git repository. Never exchange or restore the EVO1 identity or
database onto EVO2.

Taking a consistent archive briefly stops both KME containers. MACsec can go
down during this lab maintenance window; that is acceptable. This does not
delete either container or any persistent directory. Do not use `docker rm`,
`docker compose down -v`, or delete anything below `/var/db/qbt`.

## 1. Prepare protected storage

On each EVO, create a root-only directory (optional, for extra router-local copies):

```sh
mkdir -p /var/db/qbt-backups
chown root:root /var/db/qbt-backups
chmod 700 /var/db/qbt-backups
```

On the Mac, choose a private directory outside the clone and a new backup ID
shared by both archives. The examples use `qbt-before-compose-01`; choose a
different ID before repeating this procedure. Encryption is optional (see
section 6); the archives hold secrets, so keep them private and never place
them in the repository.

## 2. Stop both containers before archiving

Run the EVO1 command in an EVO1 root shell and the EVO2 command in an EVO2 root
shell:

```sh
# EVO1
docker stop qbt-evo1
docker inspect --format '{{.State.Status}}' qbt-evo1

# EVO2
docker stop qbt-evo2
docker inspect --format '{{.State.Status}}' qbt-evo2
```

Both checks must report `exited` before creating either archive. If an archive
step fails, start both original containers before troubleshooting:
`docker start qbt-evo1` on EVO1 and `docker start qbt-evo2` on EVO2. Do not
remove either container.

## 3. Archive and inspect each complete persistent root

Junos root shells may default to `csh`/`tcsh`, where POSIX variable assignment
is not valid syntax. These commands run the archive procedure in a `/bin/sh`
subshell, so they work from either shell. The fixed example archive ID is
`qbt-before-compose-01`; choose a different ID before repeating. An existing
archive with that name is never overwritten.

On EVO1:

```sh
/bin/sh -c 'set -eu; umask 077; archive=/var/tmp/qbt-evo1-qbt-before-compose-01.tar; if test -e "$archive"; then echo "Archive already exists; choose a new ID" >&2; exit 1; fi; tar -cf "$archive" -C /var/db/qbt evo1; sha256sum "$archive"; tar -tf "$archive" >/dev/null; tar -tf "$archive" | grep -Eq "^evo1/data/(license[.]storage|license[.]lic)[$]"; tar -tf "$archive" | grep -Fx "evo1/secrets/machine-id"; tar -tf "$archive" | grep -Fx "evo1/secrets/master-key-id"; tar -tf "$archive" | grep -Fx "evo1/secrets/master-key-bytes"; chmod 600 "$archive"'
```

On EVO2, run the same checks for `evo2`:

```sh
/bin/sh -c 'set -eu; umask 077; archive=/var/tmp/qbt-evo2-qbt-before-compose-01.tar; if test -e "$archive"; then echo "Archive already exists; choose a new ID" >&2; exit 1; fi; tar -cf "$archive" -C /var/db/qbt evo2; sha256sum "$archive"; tar -tf "$archive" >/dev/null; tar -tf "$archive" | grep -Eq "^evo2/data/(license[.]storage|license[.]lic)[$]"; tar -tf "$archive" | grep -Fx "evo2/secrets/machine-id"; tar -tf "$archive" | grep -Fx "evo2/secrets/master-key-id"; tar -tf "$archive" | grep -Fx "evo2/secrets/master-key-bytes"; chmod 600 "$archive"'
```

Every command must succeed. The archive listing must include the licence state
and all identity files. Do not print or paste any secret's contents.

## 4. Copy to the Mac and compare the plaintext archive digests

From the Mac, download both archives using legacy SCP (`-O`) and normal SSH
host-key verification:

```sh
BACKUP_ID=qbt-before-compose-01
BACKUP_DIR="$HOME/qbt-backups"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
scp -O "root@10.38.97.218:/var/tmp/qbt-evo1-${BACKUP_ID}.tar" "$BACKUP_DIR/"
scp -O "root@10.38.97.228:/var/tmp/qbt-evo2-${BACKUP_ID}.tar" "$BACKUP_DIR/"
chmod 600 "$BACKUP_DIR/qbt-evo1-${BACKUP_ID}.tar"
chmod 600 "$BACKUP_DIR/qbt-evo2-${BACKUP_ID}.tar"
shasum -a 256 "$BACKUP_DIR/qbt-evo1-${BACKUP_ID}.tar"
shasum -a 256 "$BACKUP_DIR/qbt-evo2-${BACKUP_ID}.tar"
```

Compare these digests with the `sha256sum` values recorded on the respective
routers. Stop if either differs. The plaintext archives contain licence and
master-key material; keep them root/user-private and do not place them in the
repository.

## 5. Restart the existing containers

Once both archives are copied and their digests match, restart each original
container promptly:

```sh
# EVO1
docker start qbt-evo1
docker inspect --format '{{.State.Status}}' qbt-evo1

# EVO2
docker start qbt-evo2
docker inspect --format '{{.State.Status}}' qbt-evo2
```

Both checks must report `running`. This limits the service interruption; an
extended MACsec outage is acceptable for this lab, but is not needed to copy
or copy the already-created archives.

## 6. Keep the verified backup (encryption optional)

The minimum accepted backup is the pair of verified plain `.tar` archives on the
Mac, in `$HOME/qbt-backups` (directory mode `0700`, files `0600`), outside the
repository, with SHA-256 digests matching the routers. Encryption or a password
is not required for this lab. These archives contain the licence key and
master-key bytes, so do not share them, commit them, or place them in shared
storage. Optionally copy them to a second private location, and, if wanted,
compress with `gzip -k` (no password) or encrypt with `age`.

Router-local copies alone do not protect against loss of a router. After the
backup is verified you may delete the temporary `/var/tmp/qbt-evo*-*.tar` files
on the routers by their exact paths; do not delete anything below `/var/db/qbt`.

## 7. Recreate only after the manual backup is complete

The orchestrator asks for `--confirm-manual-backup` and
`--confirm-recreate`, followed by typed confirmations. Recreate preflights
active licence state and identity before mutation, reuses the existing bind
mounts and locally loaded image with Compose, and keeps the original stopped
containers under rollback names. It never removes a container or persistent
directory.

For this lab, a temporary MACsec interruption is acceptable and is not a
recreation failure. The mandatory success gate is that both new containers run
with the same active licence, machine ID, master-key inputs and persistent
database. Paired ETSI and MACsec checks remain separate follow-up actions.

```sh
python qbt_orchestrator.py preflight
python qbt_orchestrator.py recreate \
  --confirm-manual-backup --confirm-recreate
```

Do not run `recreate` until the archive contents and SHA-256 comparisons have
been checked.
