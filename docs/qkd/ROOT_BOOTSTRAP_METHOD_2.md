# Root Bootstrap Method 2 (Environment Variables)

This guide documents Method 2 for running QKD deploy with root bootstrap credentials and script-user credentials provided through environment variables.

## When to use this

Use this method when script user home or SSH artifacts require privileged repair (for example root-owned leftovers under /var/home/admin/.ssh).

## Steps

1. Go to the project directory.

```bash
cd /home/aterren/new_deploy4.1/Quantum-Safe-MACsec
```

2. Set the bootstrap user to root.

```bash
export QKD_BOOTSTRAP_USER=root
```

3. Enter root password securely (hidden input), then export it.

```bash
read -s QKD_BOOTSTRAP_PASSWORD; export QKD_BOOTSTRAP_PASSWORD
```

4. Set the script user (current runtime user in this deployment).

```bash
export QKD_SCRIPT_USER=admin
```

5. Enter script-user password securely (hidden input), then export it.

```bash
read -s QKD_SCRIPT_PASSWORD; export QKD_SCRIPT_PASSWORD
```

6. Run deploy.

```bash
python3 qkd_orchestrator.py deploy
```

7. Clear sensitive variables from the current shell after deploy.

```bash
unset QKD_BOOTSTRAP_PASSWORD QKD_SCRIPT_PASSWORD
```

## Notes

- The password prompts above do not print the secret and avoid putting cleartext passwords in shell history.
- If deployment reports a transient Junos configuration lock, re-run deploy after the lock owner exits, or wait for lock retry handling.
- Keep QKD_BOOTSTRAP_USER set to root only when privileged bootstrap remediation is needed.
