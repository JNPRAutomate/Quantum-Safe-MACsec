# Remote Linux Full-Suite Workflow with tmux

## Purpose

This guide explains how to operate the Linux helper VM from a macOS laptop,
keep a long-running build or deployment alive inside `tmux`, and monitor its
output from another terminal.

The examples use the live lab values:

- helper VM: `193.110.51.101`
- SSH port: `35022`
- SSH user: `aterren`
- macOS SSH key: `~/.ssh/aterren_rsa_qkd_lab`
- Linux repository: `/root/Quantum-Safe-MACsec`
- required Git branch: `main`
- tmux session: `redeploy-main`

Adjust these values only when operating a different environment.

## Safety model

The workflow has two levels:

1. **Non-destructive validation** runs tests and status commands without
   changing the KME or router configuration.
2. **Complete redeploy** deletes and recreates managed QKD/MACsec state, KME
   containers, database volumes, certificates, and runtime artifacts.

Do not run the complete redeploy during production traffic without an approved
maintenance window. QKD/MACsec and KME services will be temporarily
unavailable.

`etsi_user` is a key-only runtime identity. Do not set or request an
`etsi_user` password. Privileged router changes use the router bootstrap
account, normally `root`, while runtime SSH uses the generated
`qkd_id_ed25519` and `qkd_rpc_id_ed25519` keys.

## 1. Connect from macOS

Open Terminal on the macOS laptop:

```bash
ssh -i ~/.ssh/aterren_rsa_qkd_lab \
  -p 35022 \
  aterren@193.110.51.101
```

Become root without opening a password prompt:

```bash
sudo -n -i
```

Confirm the expected host and repository:

```bash
hostname
cd /root/Quantum-Safe-MACsec
pwd
```

Expected host:

```text
helpervm-kme-live
```

## 2. Verify and update `main`

Never redeploy from a detached HEAD or a release branch when the intended
integration baseline is `origin/main`.

```bash
cd /root/Quantum-Safe-MACsec
git status
git branch --show-current
git fetch origin
git switch main
git pull --ff-only origin main
git rev-parse HEAD
git rev-parse origin/main
```

The two hashes must match. Untracked observation data under `logs/` does not
prevent a fast-forward pull, but do not delete it unless it has been archived.

Stop if:

- tracked files are modified;
- `git pull --ff-only` fails;
- `HEAD` and `origin/main` differ after the pull.

Do not use `git reset --hard` or force operations to bypass those conditions.

## 3. Create the persistent tmux session

Create a named session rooted in the repository:

```bash
tmux new-session -s redeploy-main -c /root/Quantum-Safe-MACsec
```

Inside the new tmux window, activate the project virtual environment:

```bash
source venv/bin/activate
```

The prompt should begin with `(venv)`.

If the session already exists, attach to it instead:

```bash
tmux attach -t redeploy-main
```

If another terminal is attached and must be disconnected:

```bash
tmux attach -d -t redeploy-main
```

## 4. Detach without stopping the command

To leave a build or deployment running:

1. Press `Ctrl-b`.
2. Release both keys.
3. Press `d`.

The shell prints:

```text
[detached (from session redeploy-main)]
```

Do not press `Ctrl-c`; that sends an interrupt to the running command.
Typing `d` directly at the shell prompt is also incorrect: detach requires the
`Ctrl-b`, `d` tmux key sequence.

## 5. Monitor the same window

From a second macOS Terminal, connect again:

```bash
ssh -i ~/.ssh/aterren_rsa_qkd_lab \
  -p 35022 \
  aterren@193.110.51.101
sudo -n -i
tmux attach -t redeploy-main
```

This displays the same live terminal and command output.

Useful tmux commands:

```bash
tmux list-sessions
tmux capture-pane -p -t redeploy-main -S -200
```

`capture-pane` prints the latest 200 lines without attaching to the session.

To enable tmux scrollback while attached:

1. Press `Ctrl-b`, then `[`.
2. Scroll with arrow keys, Page Up, or Page Down.
3. Press `q` to return to live output.

## 6. Load the router bootstrap credential securely

Run these commands **inside tmux**:

```bash
export QKD_BOOTSTRAP_USER=root
export QKD_SCRIPT_USER=etsi_user

read -r -s -p 'Router root password: ' QKD_BOOTSTRAP_PASSWORD
echo
export QKD_BOOTSTRAP_PASSWORD
```

The password is not echoed and remains only in the tmux shell environment.
Do not put it on a command line, in Git, in a script, or in chat.

No `QKD_SCRIPT_PASSWORD` is needed because `etsi_user` is key-only.

## 7. Run the non-destructive full test suite

Run the complete Python test suite:

```bash
cd /root/Quantum-Safe-MACsec
python3 -m pytest -q
```

Run CLI smoke tests:

```bash
python3 kme_orchestrator.py --help >/tmp/kme_help.txt
python3 qkd_orchestrator.py --help >/tmp/qkd_help.txt
python3 tools/collect_device_logs.py --help >/tmp/collect_help.txt
python3 tools/observe_qkd_rotation.py --help >/tmp/observe_help.txt
python3 tools/qkd_link_rotation_report.py --help >/tmp/report_help.txt
echo "ALL CLI SMOKE TESTS PASSED"
```

Check the existing KME and QKD deployment:

```bash
python3 kme_orchestrator.py status --config config/kme/live1.yaml
python3 kme_orchestrator.py validate --config config/kme/live1.yaml
python3 qkd_orchestrator.py validate --phase full -v
```

If only validation is required, stop here and clear the credential as described
in step 11.

## 8. Complete redeploy sequence

The order matters because QKD generates certificates consumed by the KME
deployment, and a full QKD clean removes `etsi_user`.

### 8.1 Remove managed QKD/MACsec state and old PKI

```bash
python3 qkd_orchestrator.py clean --pki --full-macsec
```

This is destructive. It removes:

- managed QKD and MACsec configuration from the routers;
- the managed `etsi_user` login;
- on-box scripts and runtime state;
- local generated runtime artifacts;
- local generated certificates.

Wait for all three devices to report:

```text
[OK] Device clean complete
```

### 8.2 Regenerate runtime artifacts and certificates

```bash
python3 qkd_orchestrator.py create \
  --inventory config/inventory/input/lab3.yaml \
  --pki-profile hierarchical_ca
```

This regenerates:

- runtime inventory and topology;
- per-device on-box artifacts;
- three KME certificates;
- three SAE certificates;
- KME and Juniper CA chains;
- trust-exchange bundles.

### 8.3 Destroy the existing KME lab

```bash
python3 kme_orchestrator.py destroy \
  --config config/kme/live1.yaml \
  --force
```

This stops and removes the managed KME/PostgreSQL containers, database volumes,
and the managed Docker network.

### 8.4 Rebuild all KME services

```bash
python3 kme_orchestrator.py create \
  --config config/kme/live1.yaml \
  --count 3 \
  --no-cache \
  --recreate-db \
  --validate
```

This performs host checks, environment generation, an uncached image build,
certificate installation, database recreation, deployment of three KME
instances, and KME validation.

Expected final messages:

```text
[OK] KME validation passed
[OK] create workflow completed
```

### 8.5 Recreate the key-only router runtime identity

A full QKD clean deletes `etsi_user`, so bootstrap is mandatory before deploy:

```bash
python3 qkd_orchestrator.py bootstrap \
  --bootstrap-user root \
  -v
```

Expected summary:

```text
OK     : MX301-P1, MX304-P1, ACX7348-P1
FAILED : none
```

### 8.6 Deploy QKD/MACsec

```bash
python3 qkd_orchestrator.py deploy
```

The command performs:

1. pre-deploy validation;
2. artifact collection;
3. on-box file deployment;
4. QKD/MACsec provisioning and peer RPC key distribution;
5. post-deploy validation.

Expected final messages:

```text
Result: OK
deploy completed : ACX7348-P1, MX301-P1, MX304-P1
```

## 9. Recover from an interrupted or failed deployment

Read the final output before retrying:

```bash
tmux capture-pane -p -t redeploy-main -S -300
```

If deploy fails with:

```text
chown: etsi_user: illegal user name
```

the full clean removed the managed user and bootstrap was skipped. Run:

```bash
python3 qkd_orchestrator.py bootstrap --bootstrap-user root -v
python3 qkd_orchestrator.py deploy
```

Do not repeat the destructive KME or QKD cleanup unless the failed phase
requires it. Both orchestrators support rerunning individual phases.

## 10. Final validation

Run all post-deploy checks:

```bash
python3 qkd_orchestrator.py validate --phase full -v
python3 kme_orchestrator.py status --config config/kme/live1.yaml
python3 kme_orchestrator.py validate --config config/kme/live1.yaml
python3 -m pytest -q
```

Confirm the Git baseline did not change:

```bash
git status --short --branch
git branch --show-current
git rev-parse HEAD
git rev-parse origin/main
```

The branch must still be `main`, and both hashes must match. Generated
artifacts may be present according to repository ignore rules; tracked source
changes are not expected from a deployment.

## 11. Clear credentials and close tmux

Inside tmux:

```bash
unset QKD_BOOTSTRAP_PASSWORD
unset QKD_UPLOAD_PASSWORD
unset QKD_SCRIPT_PASSWORD
unset QKD_BOOTSTRAP_USER
unset QKD_UPLOAD_USER
unset QKD_SCRIPT_USER
exit
```

After `exit`, the tmux session should terminate. Verify from the helper VM:

```bash
tmux list-sessions
```

If the session is still present after all commands have finished:

```bash
tmux kill-session -t redeploy-main
```

## 12. Start a tmux command directly from macOS

For non-secret commands, macOS can create a detached session without first
opening an interactive shell:

```bash
ssh -i ~/.ssh/aterren_rsa_qkd_lab \
  -p 35022 \
  aterren@193.110.51.101 \
  "sudo -n tmux new-session -d -s test-main \
  -c /root/Quantum-Safe-MACsec \
  'venv/bin/python -m pytest -q; exec bash'"
```

Monitor it from macOS:

```bash
ssh -t -i ~/.ssh/aterren_rsa_qkd_lab \
  -p 35022 \
  aterren@193.110.51.101 \
  "sudo -n tmux attach -t test-main"
```

Use an interactive tmux shell for any command that needs credentials. Never
embed passwords in the remote command string.

## Related documentation

- [KME First-Run Guide](../kme/first_run_guide.md)
- [QKD Deploy Phases](../qkd/qkd_deploy_phases.md)
- [QKD CLI Reference](../qkd/cli_reference.md)
- [QKD root bootstrap method 2](../qkd/root_bootstrap_method_2.md)
- [QKD Post-Check Observation Tools](qkd_post_check_observation_tools.md)
