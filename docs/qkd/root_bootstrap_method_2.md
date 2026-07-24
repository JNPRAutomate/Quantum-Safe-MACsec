# root bootstrap method 2 (environment variables)

## objective

Run deploy with explicit privileged bootstrap credentials for host repair tasks (ownership and permission fixes under script-user home), while keeping runtime execution under the script user.

This is a low-level operator procedure with deterministic shell steps and post-checks.

## runtime model

1. deploy step 1 runs script-user bootstrap and may require privileged credentials.
2. deploy steps 4 and 5 execute using script-user credentials for onbox delivery and runtime config.
3. post-deploy validation confirms qkd runtime health.

## preconditions

1. You are in a trusted shell session on the orchestrator host.
2. Repository branch and runtime artifacts are updated.
3. You have both credentials available:
: bootstrap/root password
: script-user password

## environment variables used by deploy

1. QKD_BOOTSTRAP_USER
: privileged account for bootstrap actions (typically root)
2. QKD_BOOTSTRAP_PASSWORD
: password for QKD_BOOTSTRAP_USER
3. QKD_SCRIPT_USER
: runtime user on Junos (typically admin)
4. QKD_SCRIPT_PASSWORD
: password for QKD_SCRIPT_USER

## secure input sequence (recommended)

```bash
cd /path/to/Quantum-Safe-MACsec

export QKD_BOOTSTRAP_USER=root
export QKD_SCRIPT_USER=admin

read -r -s -p 'QKD_BOOTSTRAP_PASSWORD: ' QKD_BOOTSTRAP_PASSWORD; echo
export QKD_BOOTSTRAP_PASSWORD

read -r -s -p 'QKD_SCRIPT_PASSWORD: ' QKD_SCRIPT_PASSWORD; echo
export QKD_SCRIPT_PASSWORD
```

Notes on read flags:

1. -s disables terminal echo for secret input.
2. -r prevents backslash escaping side effects.
3. -p prints an inline prompt.

## deploy execution

Full workflow:

```bash
python3 qkd_orchestrator.py deploy
```

Faster iterative workflow (skip validations):

```bash
python3 qkd_orchestrator.py deploy --skip-pre-validation --skip-post-validation
```

## post-checks (mandatory)

1. bootstrap summary contains no fatal failures.
2. onbox deploy step reports success on target devices.
3. provisioning commit succeeds on target devices.
4. runtime state files are created under the active runtime path.

## sensitive variable cleanup

Always clear shell secrets after deploy:

```bash
unset QKD_BOOTSTRAP_PASSWORD QKD_SCRIPT_PASSWORD
unset QKD_BOOTSTRAP_USER QKD_SCRIPT_USER
```

## failure modes and low-level troubleshooting

1. missing credentials
: symptom: deploy aborts before bootstrap or onbox deploy
: action: re-export variables and retry

2. bootstrap permission mismatch
: symptom: ownership or chmod failures in script-user home
: action: rerun method 2 with root bootstrap credentials and verify target path ownership

3. transient configuration db lock
: symptom: lock warning/retry during provisioning
: action: wait and rerun deploy after competing session exits

4. runtime json file missing on device
: symptom: qkd_onbox startup fails with missing config/inventory json
: action: rerun deploy step 4 and verify json files under /var/db/scripts/op
