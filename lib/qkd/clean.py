# qkd_clean.py

import yaml
import shutil
from lxml import etree
from pathlib import Path

from jnpr.junos import Device

from lib.common.settings import CONFIG, PKI, QKD
from lib.common.config import load_inventory_base

BASE_DIR = Path(__file__).resolve().parents[2]


# ----------------------------------------
# CLEAN LOCAL RUNTIME
# ----------------------------------------
def clean_runtime():
    """
    Remove local generated runtime artifacts under config/runtime.

    Safety:
      - refuses to delete if path does not look like runtime
      - skips hidden files
    """

    runtime_dir = BASE_DIR / CONFIG["runtime_dir"]

    if not runtime_dir.exists():
        return

    if "runtime" not in str(runtime_dir):
        raise RuntimeError("Refusing to clean unsafe directory!")

    print(f"Cleaning local runtime only: {runtime_dir}")

    for f in runtime_dir.iterdir():

        if f.name.startswith("."):
            continue

        if f.is_file():
            f.unlink()

        elif f.is_dir():
            shutil.rmtree(f)

# ----------------------------------------
# CLEAN LOCAL CERTS
# ----------------------------------------
def clean_certs():
    """
    Remove local certs directory.

    This is controlled by --pki.
    """

    certs_dir = BASE_DIR / CONFIG["certs_dir"]

    if not certs_dir.exists():
        return

    print(f"Cleaning certs dir: {certs_dir}")

    for f in certs_dir.iterdir():

        if f.name.startswith("."):
            continue

        if f.is_file():
            f.unlink()

        elif f.is_dir():
            shutil.rmtree(f)

    print("CERTS CLEANED")

# ----------------------------------------
# COLLECT QKD CLEAN CANDIDATES
# ----------------------------------------
def collect_qkd_clean_candidates(device):
    """
    Collect interfaces, connectivity-associations, and keychains that belong
    to QKD links for one device.

    Supports both old and new inventory styles:
      - link["ca_names"]
      - link["ca_name"]
      - link["keychain_name"]

    Also derives fallback keychain name:
      QKD_<ca_name>
    """

    iface_candidates = []
    ca_candidates = []
    keychain_candidates = []

    for link in device.get("links", []):

        local_iface = link.get("interface")

        #
        # Clean only local interfaces belonging to this device.
        # Never try to delete peer interfaces from another device,
        # otherwise ACX4/ACX5 receive invalid et-2/x/x deletes.
        #

        if local_iface and local_iface not in iface_candidates:
            iface_candidates.append(local_iface)

        ca_name = link.get("ca_name")

        if ca_name and ca_name not in ca_candidates:
            ca_candidates.append(ca_name)

        for ca in link.get("ca_names", []):
            if ca and ca not in ca_candidates:
                ca_candidates.append(ca)

        keychain_name = link.get("keychain_name")

        if keychain_name and keychain_name not in keychain_candidates:
            keychain_candidates.append(keychain_name)

    for ca in ca_candidates:
        fallback_keychain = f"QKD_{ca}"

        if fallback_keychain not in keychain_candidates:
            keychain_candidates.append(fallback_keychain)

    return iface_candidates, ca_candidates, keychain_candidates

# ----------------------------------------
# CLEAN ONE REMOTE DEVICE
# ----------------------------------------
def clean_device(
    name,
    device,
    full_macsec=False,
    remove_peer_user=False,
    remove_script_user=False,
    clean_user=None,
    clean_password=None,
    fallback_user=None,
    fallback_password=None,
    fallback_user_secondary=None,
    fallback_password_secondary=None,
    peer_cmd_class_override=None,
):
    try:
        ip = device["ip"]

        device_auth = device.get("auth", {}) or {}
        default_user = device_auth.get("username")
        default_pass = device_auth.get("password")

        user = clean_user or default_user
        passwd = clean_password or default_pass

        if not user or not passwd:
            raise RuntimeError(
                f"Missing credentials for device {name}. "
                "Provide bootstrap credentials in inventory_base or runtime auth."
            )

        script_name = QKD["SCRIPT_NAME"]
        legacy_script_name = "onbox.py"
        script_dir = QKD.get("SCRIPT_DIR", "/var/db/scripts")
        op_script_dir = QKD.get("OP_SCRIPT_DIR", "/var/db/scripts/op")
        event_script_dir = QKD.get("EVENT_SCRIPT_DIR", "/var/db/scripts/event")
        remote_cert_dir = PKI.get("REMOTE_CERT_DIR", "/var/db/scripts/certs")
        auth_user = str(user)
        script_user = str(QKD.get("SCRIPT_USER", "admin"))
        peer_cmd_user = str(QKD.get("PEER_CMD_USER", "etsi_peer_view"))
        peer_cmd_class = str(
            peer_cmd_class_override
            or QKD.get("PEER_CMD_CLASS", "read-only")
        )

        print(f"Cleaning device {name} {ip}", flush=True)

        iface_candidates, ca_candidates, keychain_candidates = (
            collect_qkd_clean_candidates(device)
        )

        def safe_iface_name(iface):
            return iface.replace("/", "_")

        def device_sae_id():
            qkd = device.get("qkd", {}) or {}

            return (
                qkd.get("sae_id")
                or device.get("local_sae")
                or device.get("sae")
                or device.get("sae_id")
                or name
            )

        def runtime_tmp_paths():
            sae = device_sae_id()

            paths = [
                f"/var/tmp/{script_name}",
                f"/var/tmp/qkd_onbox_{sae}.lock",
            ]

            for link in device.get("links", []):
                iface = link.get("interface")
                peer = link.get("peer")

                if not iface or not peer:
                    continue

                safe_iface = safe_iface_name(iface)

                paths.extend(
                    [
                        f"/var/tmp/qkd_db_{peer}_{safe_iface}.json",
                        f"/var/tmp/qkd_debug_{sae}_{safe_iface}.log",
                        f"/var/tmp/qkd_onbox_{sae}_{safe_iface}_install-key.lock",
                        f"/var/tmp/qkd_onbox_{sae}_{safe_iface}_status.lock",
                    ]
                )

            deduped = []

            for path in paths:
                if path not in deduped:
                    deduped.append(path)

            return deduped

        runtime_paths = runtime_tmp_paths()
        state_file_prefix = str(QKD.get("STATE_FILE_PREFIX", "/var/tmp/qkd_db"))
        lock_file_prefix = str(QKD.get("LOCK_FILE_PREFIX", "/var/tmp/qkd_onbox"))
        log_file_path = str(QKD.get("LOG_FILE", "/var/tmp/qkd_debug.log"))

        tmp_runtime_name_prefixes = {
            Path(state_file_prefix).name,
            Path(lock_file_prefix).name,
            Path(log_file_path).stem,
        }
        tmp_runtime_name_prefixes = {
            prefix for prefix in tmp_runtime_name_prefixes if prefix
        }

        tmp_runtime_globs = [f"/var/tmp/{prefix}*" for prefix in sorted(tmp_runtime_name_prefixes)]
        tmp_runtime_globs_expr = " ".join(tmp_runtime_globs) if tmp_runtime_globs else ""

        def list_tmp_runtime_leftovers():
            output = run_shell(
                "verify /var/tmp runtime leftovers",
                f"ls -1 {tmp_runtime_globs_expr} 2>/dev/null || true",
                strict=False,
                show_output=False,
                show_label=False,
            )
            leftovers = []
            for raw_line in (output or "").splitlines():
                line = raw_line.strip()
                if not line or not line.startswith("/var/tmp/"):
                    continue
                if line not in leftovers:
                    leftovers.append(line)
            return leftovers

        soft_runtime_paths = [
            "/var/tmp/qkd_debug.log",
        ]
        config_cmds = [
            "delete event-options generate-event QKD_TIMER",
            "delete event-options policy QKD",
            "delete event-options policy QKD_POLICY",
            f"delete event-options event-script file {script_name}",
            f"delete event-options event-script file {legacy_script_name}",
            f"delete system scripts op file {script_name}",
            f"delete system scripts op file {legacy_script_name}",
        ]

        user_cleanup_cmds = []

        # Remove explicit script-user bindings before deleting users to avoid
        # Junos constraint failures when stale script stanzas remain.
        user_cleanup_cmds.extend(
            [
                f"delete event-options event-script file {script_name} python-script-user",
                f"delete event-options event-script file {legacy_script_name} python-script-user",
            ]
        )

        if remove_script_user:
            user_cleanup_cmds.append(f"delete system login user {script_user}")

        if remove_peer_user:
            user_cleanup_cmds.append(f"delete system login user {peer_cmd_user}")
            builtin_classes = {"super-user", "operator", "read-only", "unauthorized"}
            if peer_cmd_class and peer_cmd_class not in builtin_classes:
                user_cleanup_cmds.append(f"delete system login class {peer_cmd_class}")

        if full_macsec:
            config_cmds.append("delete security macsec")
            config_cmds.append("delete security authentication-key-chains")
        else:
            for iface in iface_candidates:
                config_cmds.append(
                    f"delete security macsec interfaces {iface}"
                )

            for ca in ca_candidates:
                config_cmds.append(
                    f"delete security macsec connectivity-association {ca}"
                )

            for keychain in keychain_candidates:
                config_cmds.append(
                    f"delete security authentication-key-chains key-chain {keychain}"
                )

        for iface in iface_candidates:
            config_cmds.append(
                f"delete interfaces {iface} description"
            )

        config_body = "; ".join(config_cmds)

        config_cleanup_body = f"configure; {config_body}; {{commit_cmd}}; exit"

        file_cleanup_parts = [
            f"rm -f {event_script_dir}/{script_name}",
            f"rm -f {op_script_dir}/{script_name}",
            f"rm -f /var/tmp/{script_name}",
            "rm -f /var/db/scripts/event/qkd.conf",
            f"rm -rf {remote_cert_dir}",
            f"rm -rf {script_dir}/certs",
            f"rm -rf {op_script_dir}/certs",
            f"rm -rf {event_script_dir}/certs",
        ]

        for path in runtime_paths:
            if path.endswith(".lock"):
                file_cleanup_parts.append(f"rm -rf {path}")
            else:
                file_cleanup_parts.append(f"rm -f {path}")
        for path in soft_runtime_paths:
            file_cleanup_parts.append(f"rm -f {path}")
        if tmp_runtime_globs_expr:
            file_cleanup_parts.append(f"rm -rf {tmp_runtime_globs_expr} 2>/dev/null || true")
        
        file_cleanup_cmd = "; ".join(file_cleanup_parts)

        dev = Device(
            host=ip,
            user=user,
            passwd=passwd,
            port=22,
        )

        def rpc_text(rsp):
            try:
                return etree.tostring(
                    rsp,
                    encoding="unicode",
                    method="text",
                ).strip()
            except Exception:
                return str(rsp).strip()

        ##
        def run_shell(label, command, strict=True, show_output=True, show_label=True):
            if show_label:
                print(f"[{name}] {label}", flush=True)

            rsp = dev.rpc.request_shell_execute(
                command=command
            )

            output = rpc_text(rsp)

            if show_output and output:
                for line in output.splitlines():
                    line = line.strip()

                    if not line:
                        continue
                    
                    if "warning: statement not found" in line:
                        continue

                    if (
                        "some configurations require commits to be synchronized" in line.lower()
                        and "commit no-synchronize" in line.lower()
                    ):
                        continue

                    if (
                        "graceful-switchover is enabled" in line.lower()
                        and "commit synchronize should be used" in line.lower()
                    ):
                        continue

                    if (
                        "qkd_debug.log" in line.lower()
                        and "operation not permitted" in line.lower()
                    ):
                        continue

                    if (
                        "/var/db/scripts/certs" in line.lower()
                        and "permission denied" in line.lower()
                    ):
                        continue

                    if (
                        "/var/db/scripts/certs" in line.lower()
                        and "directory not empty" in line.lower()
                    ):
                        continue

                    if (
                        "/root/.ssh/authorized_keys" in line.lower()
                        and (
                            "cannot create" in line.lower()
                            or "no such file or directory" in line.lower()
                            or "operation not permitted" in line.lower()
                            or "permission denied" in line.lower()
                        )
                    ):
                        continue
                    
                    if "Entering configuration mode" in line:
                        continue
                    
                    if "Exiting configuration mode" in line:
                        continue
                    
                    if "No match" in line:
                        continue
                    
                    if line == "True":
                        continue
                    
                    print(f"[{name}] {line}", flush=True)

            bad_markers = [
                "Ambiguous output redirect",
                "syntax error",
                "commit failed",
                "unknown command",
                "error:",
            ]

            low = output.lower()

            if strict and any(marker.lower() in low for marker in bad_markers):
                raise RuntimeError(
                    f"{label} failed on {name}\n"
                    f"command={command}\n"
                    f"output={output}"
                )

            return output

        def has_dual_re():
            out = run_cli_show("show chassis routing-engine")
            low = (out or "").lower()

            re1_lines = []
            for line in low.splitlines():
                if (
                    "re1" in line
                    or "routing engine 1" in line
                    or "slot 1:" in line
                ):
                    re1_lines.append(line)

            if not re1_lines:
                return False

            absent_tokens = (
                " empty",
                "absent",
                "not present",
                "not-installed",
                "not installed",
                "not online",
            )

            for line in re1_lines:
                if not any(token in line for token in absent_tokens):
                    return True

            return False

        def run_re1_cli(label, remote_command):
            escaped = remote_command.replace('"', '\\"')

            command_candidates = [
                f'request routing-engine execute command "{escaped}" routing-engine other',
                f'request routing-engine execute command "{escaped}" routing-engine backup',
                f'request routing-engine execute command "{escaped}" routing-engine re1',
                f'request routing-engine execute other command "{escaped}"',
                f'request routing-engine execute re1 command "{escaped}"',
            ]

            def _benign_line(line):
                low = line.lower()
                if not low:
                    return True
                if low.startswith("----"):
                    return True
                if low in {"backup:", "re0:", "re1:"}:
                    return True
                if "entering configuration mode" in low:
                    return True
                if "exiting configuration mode" in low:
                    return True
                if "warning: statement not found" in low:
                    return True
                if (
                    "some configurations require commits to be synchronized" in low
                    and "commit no-synchronize" in low
                ):
                    return True
                if (
                    "graceful-switchover is enabled" in low
                    and "commit synchronize should be used" in low
                ):
                    return True
                if (
                    "qkd_debug.log" in low
                    and "operation not permitted" in low
                ):
                    return True
                if (
                    "/root/.ssh/authorized_keys" in low
                    and (
                        "cannot create" in low
                        or "no such file or directory" in low
                        or "operation not permitted" in low
                        or "permission denied" in low
                    )
                ):
                    return True
                return False

            def _is_hard_failure(text):
                low = (text or "").lower()
                # Connectivity/syntax/command failures are hard failures.
                if (
                    "could not connect to re1" in low
                    or "cannot connect to other re" in low
                    or "syntax error" in low
                    or "unknown command" in low
                    or "unmatched '" in low
                    or "command not found" in low
                ):
                    return True

                # Soft-runtime cleanup may fail due to permissions; keep it non-fatal.
                if "operation not permitted" in low and "qkd_debug.log" in low:
                    return False

                # Generic error lines are considered hard failures unless they are
                # clearly related to benign soft-runtime cleanup above.
                if "error:" in low:
                    return True

                return False

            last_output = ""

            for command in command_candidates:
                try:
                    rsp = dev.rpc.cli(command, format="text")
                    output = rpc_text(rsp)
                except Exception as exc:
                    output = str(exc)

                last_output = output or ""

                if _is_hard_failure(output):
                    continue

                meaningful_lines = []
                for line in (output or "").splitlines():
                    line = line.strip()
                    if _benign_line(line):
                        continue
                    meaningful_lines.append(line)

                if meaningful_lines:
                    for line in meaningful_lines:
                        print(f"[{name}] {line}", flush=True)
                else:
                    print(f"[{name}] {label} OK (already clean)", flush=True)

                return True

            print(f"[{name}] WARN {label} not completed on RE1")
            if last_output:
                for line in last_output.splitlines()[:4]:
                    line = line.strip()
                    if line:
                        print(f"[{name}] {line}", flush=True)
            return False
        ##
        def run_cli_show(command):
            rsp = dev.rpc.cli(
                command,
                format="text",
            )

            return rpc_text(rsp)

        ##
        def remote_path_exists(path):
            output = run_shell(
                f"verify path {path}",
                (
                    f"test -e {path} "
                    f"&& echo EXISTS:{path} "
                    f"|| true"
                ),
                strict=False,
                show_output=False,
                show_label=False,
            )
        
            return f"EXISTS:{path}" in output
        ##
        
        dev.open()

        try:
            dual_re = has_dual_re()
            commit_cmd = "commit no-synchronize"
            config_cleanup_cmd = (
                "cli -c '"
                + config_cleanup_body.format(commit_cmd=commit_cmd)
                + "'"
            )
            peer_cleanup_failures = []

            run_shell(
                "config cleanup",
                config_cleanup_cmd,
                strict=True,
            )

            if dual_re:
                re1_cfg_shell = f"cli -c 'configure; {config_body}; commit no-synchronize; exit'"
                ok_re1_cfg = run_re1_cli(
                    "re1 config cleanup",
                    re1_cfg_shell,
                )
                if not ok_re1_cfg:
                    peer_cleanup_failures.append("re1 config cleanup")

            file_cleanup_output = run_shell(
                "file/cert/runtime cleanup",
                file_cleanup_cmd,
                strict=False,
            )

            cert_cleanup_permission_denied = False
            for raw_line in (file_cleanup_output or "").splitlines():
                low = raw_line.strip().lower()
                if "permission denied" in low and "/var/db/scripts/certs" in low:
                    cert_cleanup_permission_denied = True
                    break

            cert_paths = [
                remote_cert_dir,
                f"{script_dir}/certs",
                f"{op_script_dir}/certs",
                f"{event_script_dir}/certs",
            ]

            cert_leftovers_before_fallback = [
                path for path in cert_paths if remote_path_exists(path)
            ]

            fallback_candidates = []
            if fallback_user and fallback_password:
                fallback_candidates.append((str(fallback_user), str(fallback_password)))
            if fallback_user_secondary and fallback_password_secondary:
                fallback_candidates.append((str(fallback_user_secondary), str(fallback_password_secondary)))

            # Preserve order and de-duplicate user entries.
            deduped_candidates = []
            seen_users = set()
            for cand_user, cand_password in fallback_candidates:
                if cand_user in seen_users:
                    continue
                seen_users.add(cand_user)
                deduped_candidates.append((cand_user, cand_password))

            can_try_fallback_cleanup = bool(cert_leftovers_before_fallback and deduped_candidates)

            if can_try_fallback_cleanup:
                cert_only_cleanup_cmd = "; ".join(
                    [
                        f"rm -rf {remote_cert_dir}",
                        f"rm -rf {script_dir}/certs",
                        f"rm -rf {op_script_dir}/certs",
                        f"rm -rf {event_script_dir}/certs",
                    ]
                )

                fallback_success = False
                last_fallback_exc = None

                for candidate_user, candidate_password in deduped_candidates:
                    if candidate_user == auth_user:
                        continue

                    print(
                        f"[{name}] cert cleanup retry with fallback user {candidate_user}",
                        flush=True,
                    )

                    fallback_dev = Device(
                        host=ip,
                        user=candidate_user,
                        passwd=candidate_password,
                        port=22,
                    )

                    try:
                        fallback_dev.open()
                        rsp = fallback_dev.rpc.request_shell_execute(command=cert_only_cleanup_cmd)
                        fallback_output = rpc_text(rsp)

                        for line in (fallback_output or "").splitlines():
                            line = line.strip()
                            if not line:
                                continue
                            if "warning: statement not found" in line:
                                continue
                            if "permission denied" in line.lower():
                                continue
                            print(f"[{name}] {line}", flush=True)

                        fallback_success = True
                        break
                    except Exception as fallback_exc:
                        last_fallback_exc = fallback_exc
                        low = str(fallback_exc).lower()
                        if "connectautherror" in low:
                            print(
                                f"[{name}] fallback auth not available for user {candidate_user}; trying next candidate",
                                flush=True,
                            )
                        else:
                            print(
                                f"[{name}] WARN fallback cert cleanup failed as {candidate_user}: {fallback_exc}",
                                flush=True,
                            )
                    finally:
                        try:
                            fallback_dev.close()
                        except Exception:
                            pass

                if not fallback_success and last_fallback_exc:
                    print(
                        f"[{name}] WARN fallback cert cleanup exhausted: {last_fallback_exc}",
                        flush=True,
                    )

            if dual_re:
                ok_re1_files = run_re1_cli(
                    "re1 file/cert/runtime cleanup",
                    file_cleanup_cmd,
                )
                if not ok_re1_files:
                    peer_cleanup_failures.append("re1 file/cert/runtime cleanup")

            failures = []

            set_output = run_cli_show(
                "show configuration | display set"
            )

            forbidden_patterns = [
                "set event-options generate-event QKD_TIMER",
                "set event-options policy QKD",
                "set event-options policy QKD_POLICY",
                f"set event-options event-script file {script_name}",
                f"set event-options event-script file {legacy_script_name}",
                f"set system scripts op file {script_name}",
                f"set system scripts op file {legacy_script_name}",
            ]

            # NOTE: Login-user/class leftovers are verified after the dedicated
            # login users cleanup step. Checking them here would incorrectly
            # fail before the cleanup has been executed.

            if full_macsec:
                forbidden_patterns.extend(
                    [
                        "set security macsec ",
                        "set security authentication-key-chains ",
                    ]
                )
            else:
                for iface in iface_candidates:
                    forbidden_patterns.append(
                        f"set security macsec interfaces {iface}"
                    )

                for ca in ca_candidates:
                    forbidden_patterns.append(
                        f"set security macsec connectivity-association {ca}"
                    )

                for keychain in keychain_candidates:
                    forbidden_patterns.append(
                        f"set security authentication-key-chains key-chain {keychain}"
                    )

            for iface in iface_candidates:
                forbidden_patterns.append(
                    f"set interfaces {iface} description"
                )

            config_leftovers = []

            for line in set_output.splitlines():
                line = line.strip()

                for pattern in forbidden_patterns:
                    if pattern in line:
                        config_leftovers.append(line)
                        break

            if config_leftovers:
                failures.append(
                    "configuration leftovers:\n"
                    + "\n".join(config_leftovers)
                )

            paths_should_be_absent = [
                f"{op_script_dir}/{script_name}",
                f"{event_script_dir}/{script_name}",
                f"/var/tmp/{script_name}",
                "/var/db/scripts/event/qkd.conf",
                remote_cert_dir,
                f"{script_dir}/certs",
                f"{op_script_dir}/certs",
                f"{event_script_dir}/certs",
            ]

            for path in runtime_paths:
                if path not in paths_should_be_absent:
                    paths_should_be_absent.append(path)

            deduped_absent_paths = []
            for path in paths_should_be_absent:
                if path not in deduped_absent_paths:
                    deduped_absent_paths.append(path)

            file_leftovers = []

            for path in deduped_absent_paths:
                if remote_path_exists(path):
                    file_leftovers.append(path)

            if file_leftovers and cert_cleanup_permission_denied:
                cert_dirs = {
                    remote_cert_dir,
                    f"{script_dir}/certs",
                    f"{op_script_dir}/certs",
                    f"{event_script_dir}/certs",
                }

                cert_leftovers = [p for p in file_leftovers if p in cert_dirs]
                non_cert_leftovers = [p for p in file_leftovers if p not in cert_dirs]

                if cert_leftovers and not non_cert_leftovers:
                    print(f"[{name}] cleanup warning: cert dir leftovers ignored due to permission denied:")
                    for path in cert_leftovers:
                        print(f"[{name}]   {path}")
                    file_leftovers = []

            # Fallback cleanup for /var/tmp files with permission denied (sticky bit issue).
            # Files owned by macsec_user have sticky bit protection. Try as root.
            if file_leftovers:
                tmp_leftovers = [p for p in file_leftovers if p.startswith("/var/tmp/")]
                other_leftovers = [p for p in file_leftovers if not p.startswith("/var/tmp/")]

                if tmp_leftovers and not other_leftovers and fallback_user == "root" and fallback_password:
                    print(f"[{name}] /var/tmp cleanup fallback as root", flush=True)
                    
                    # Build rm commands one per file (safer than glob expansion)
                    rm_cmds = [f"rm -f {path}" for path in tmp_leftovers]
                    tmp_cleanup_cmd = "; ".join(rm_cmds)
                    
                    fallback_dev_tmp = Device(
                        host=ip,
                        user="root",
                        passwd=fallback_password,
                        port=22,
                    )
                    try:
                        fallback_dev_tmp.open()
                        rsp = fallback_dev_tmp.rpc.request_shell_execute(command=tmp_cleanup_cmd)
                        # Shell execute output is for logging only; don't rely on exit code
                        fallback_output = rpc_text(rsp)
                        if fallback_output and fallback_output.strip():
                            for line in fallback_output.splitlines():
                                if line.strip() and "permission denied" not in line.lower():
                                    print(f"[{name}] {line}", flush=True)
                        print(f"[{name}] /var/tmp cleanup via root completed", flush=True)
                        
                        # Re-verify after fallback cleanup
                        file_leftovers_after_fallback = [
                            path for path in tmp_leftovers if remote_path_exists(path)
                        ]
                        if not file_leftovers_after_fallback:
                            print(f"[{name}] cleanup note: /var/tmp leftovers removed via root")
                            file_leftovers = []
                        else:
                            file_leftovers = file_leftovers_after_fallback
                    except Exception as tmp_exc:
                        print(f"[{name}] WARN /var/tmp fallback cleanup failed: {tmp_exc}", flush=True)
                    finally:
                        try:
                            fallback_dev_tmp.close()
                        except Exception:
                            pass
            
            soft_leftovers = []

            for path in soft_runtime_paths:
                if remote_path_exists(path):
                    soft_leftovers.append(path)

            for path in list_tmp_runtime_leftovers():
                if path not in soft_leftovers:
                    soft_leftovers.append(path)

            if soft_leftovers:
                print(f"[{name}] cleanup warning: soft runtime leftovers:")
                for path in soft_leftovers:
                    print(f"[{name}]   {path}")
            
            # Separate /var/tmp log files from hard failures (permission denied on sticky bit is benign).
            if file_leftovers:
                # Check which ones are /var/tmp/*.log files (likely to have permission issues with sticky bit)
                log_leftovers = [p for p in file_leftovers if "/var/tmp/" in p and "qkd_debug" in p and p.endswith((".log", ".log.1", ".log.2", ".log.3", ".log.4", ".log.5"))]
                hard_leftovers = [p for p in file_leftovers if p not in log_leftovers]
                
                # Log files are soft leftovers; move them to warnings
                if log_leftovers and not hard_leftovers:
                    for log_path in log_leftovers:
                        if log_path not in soft_leftovers:
                            soft_leftovers.append(log_path)
                    file_leftovers = []
                elif hard_leftovers:
                    file_leftovers = hard_leftovers

            if file_leftovers:
                failures.append(
                    "file/runtime/cert leftovers:\n"
                    + "\n".join(file_leftovers)
                )

            if peer_cleanup_failures:
                failures.append(
                    "peer RE cleanup incomplete:\n"
                    + "\n".join(peer_cleanup_failures)
                )

            if user_cleanup_cmds:
                user_cleanup_body = "; ".join(user_cleanup_cmds)
                user_cleanup_cmd = (
                    f"cli -c 'configure; {user_cleanup_body}; {commit_cmd}; exit'"
                )

                run_shell(
                    "login users cleanup",
                    user_cleanup_cmd,
                    strict=True,
                )

                if dual_re:
                    re1_user_shell = f"cli -c 'configure; {user_cleanup_body}; commit no-synchronize; exit'"
                    ok_re1_users = run_re1_cli(
                        "re1 login users cleanup",
                        re1_user_shell,
                    )
                    if not ok_re1_users:
                        print(f"[FAIL] Device clean verification failed: {name}")
                        print("peer RE cleanup incomplete:\nre1 login users cleanup")
                        return False

                removed_users = []
                if remove_script_user:
                    removed_users.append(script_user)
                if remove_peer_user:
                    removed_users.append(peer_cmd_user)

                if auth_user in removed_users:
                    print(
                        f"[{name}] info: current clean session user {auth_user} was removed; "
                        "post-user verification skipped",
                        flush=True,
                    )

                # Post user-cleanup verification for login users/classes.
                set_output_after_users = run_cli_show(
                    "show configuration | display set"
                )

                user_leftovers = []

                if remove_script_user:
                    pat = f"set system login user {script_user} "
                    for line in set_output_after_users.splitlines():
                        line = line.strip()
                        if pat in line:
                            user_leftovers.append(line)

                if remove_peer_user:
                    pat = f"set system login user {peer_cmd_user} "
                    for line in set_output_after_users.splitlines():
                        line = line.strip()
                        if pat in line:
                            user_leftovers.append(line)

                    builtin_classes = {"super-user", "operator", "read-only", "unauthorized"}
                    if peer_cmd_class and peer_cmd_class not in builtin_classes:
                        pat = f"set system login class {peer_cmd_class} "
                        for line in set_output_after_users.splitlines():
                            line = line.strip()
                            if pat in line:
                                user_leftovers.append(line)

                if user_leftovers:
                    failures.append(
                        "login user/class leftovers:\n"
                        + "\n".join(user_leftovers)
                    )

            if dual_re:
                run_shell(
                    "final commit synchronize",
                    "cli -c 'configure; commit synchronize; exit'",
                    strict=True,
                )

            if failures:
                print(f"[FAIL] Device clean verification failed: {name}")

                for item in failures:
                    print(item)

                return False

            print(f"[OK] Device clean complete: {name}")
            return True

        finally:
            try:
                dev.close()
            except Exception:
                pass

    except Exception as e:
        print(f"[FAIL] Device clean failed: {name}: {e}")
        return False        

# ----------------------------------------
# CLEAN HANDLER
# ----------------------------------------
def handle_clean(args):
    """
    Clean handler used by qkd_orchestrator.py.

    Behavior:
      - --local-only:
          clean local runtime only
      - --local-only --pki:
          clean local runtime and local certs
      - no --local-only:
          clean remote devices first, then local runtime
      - no --local-only --pki:
          clean remote devices, local runtime, and local certs

    Important:
      Local certs are removed ONLY when --pki is explicitly provided.
    """

    remove_peer_user = (
        getattr(args, "remove_peer_user", False)
        or not getattr(args, "keep_users", False)
    )
    remove_script_user = (
        getattr(args, "remove_script_user", False)
        or not getattr(args, "keep_users", False)
    )

    print("=== QKD clean ===")
    print(f"local_only = {args.local_only}")
    print(f"pki        = {args.pki}")
    print(f"full_macsec = {args.full_macsec}")
    print(f"keep_users = {getattr(args, 'keep_users', False)}")
    print(f"remove_peer_user = {remove_peer_user}")
    print(f"remove_script_user = {remove_script_user}")
    print("")

    devices_file = BASE_DIR / CONFIG["runtime_dir"] / "devices.yaml"

    # ----------------------------------------
    # LOCAL ONLY MODE
    # ----------------------------------------
    if args.local_only:

        if remove_peer_user or remove_script_user:
            print("Ignoring user-removal flags in --local-only mode (no remote cleanup).")

        clean_runtime()

        if args.pki:
            clean_certs()
        else:
            print("Skipping local cert cleanup. Use --pki to remove certs.")

        print("Local clean complete")
        return

    # ----------------------------------------
    # REMOTE + LOCAL MODE
    # ----------------------------------------
    devices = {}

    if devices_file.exists():

        with open(devices_file) as f:
            data = yaml.safe_load(f) or {}

        devices = data.get("devices", {})

        print("Using runtime devices.yaml")

    else:

        print("No runtime devices.yaml found -> fallback to inventory_base")

        base = load_inventory_base()
        devices = base.get("devices", {})

        if devices:
            print("Using inventory_base devices")
        else:
            print("No devices found anywhere -> skipping remote device cleanup")

    failed = []

    bootstrap_user = None
    bootstrap_password = None
    fallback_user = None
    fallback_password = None
    fallback_user_secondary = None
    fallback_password_secondary = None
    peer_cmd_class_override = None

    try:
        base = load_inventory_base()
        secrets = base.get("secrets", {}) if isinstance(base, dict) else {}
        if not isinstance(secrets, dict):
            secrets = {}

        bootstrap_user = (
            secrets.get("bootstrap_user")
            or secrets.get("deploy_user")
            or None
        )
        bootstrap_password = (
            secrets.get("bootstrap_password")
            or secrets.get("deploy_password")
            or secrets.get("root_password")
            or None
        )

        fallback_user = (
            secrets.get("script_user")
            or secrets.get("default_user")
            or None
        )
        fallback_password = (
            secrets.get("script_password")
            or secrets.get("admin_password")
            or secrets.get("default_password")
            or None
        )
        fallback_user_secondary = secrets.get("default_user") or None
        fallback_password_secondary = (
            secrets.get("default_password")
            or secrets.get("admin_password")
            or None
        )
        peer_cmd_class_override = (
            secrets.get("peer_cmd_class")
            or QKD.get("PEER_CMD_CLASS", "read-only")
        )
    except Exception as exc:
        print(f"WARN unable to resolve inventory_base bootstrap credentials: {exc}")

    use_bootstrap_auth = bool(bootstrap_user and bootstrap_password)

    if use_bootstrap_auth:
        print(f"Remote clean auth source: inventory_base bootstrap_user={bootstrap_user}")
    else:
        print("Remote clean auth source: runtime devices auth (bootstrap credentials unavailable)")

    if fallback_user and fallback_password:
        print(f"Remote clean cert fallback auth source: inventory_base user={fallback_user}")
    else:
        print("Remote clean cert fallback auth source: unavailable")

    if fallback_user_secondary and fallback_password_secondary:
        print(f"Remote clean cert secondary fallback user: {fallback_user_secondary}")

    print(f"Remote clean peer class target: {peer_cmd_class_override}")

    for name, device in devices.items():
        ok = clean_device(
            name,
            device,
            full_macsec=args.full_macsec,
            remove_peer_user=remove_peer_user,
            remove_script_user=remove_script_user,
            clean_user=bootstrap_user if use_bootstrap_auth else None,
            clean_password=bootstrap_password if use_bootstrap_auth else None,
            fallback_user=fallback_user,
            fallback_password=fallback_password,
            fallback_user_secondary=fallback_user_secondary,
            fallback_password_secondary=fallback_password_secondary,
            peer_cmd_class_override=peer_cmd_class_override,
        )

        if not ok:
            failed.append(name)

    if failed:
        raise RuntimeError(
            f"Remote clean failed for devices: {', '.join(failed)}. "
            f"Local runtime was not removed."
        )

    clean_runtime()

    if args.pki:
        clean_certs()
    else:
        print("Skipping local cert cleanup. Use --pki to remove certs.")

    print("Full clean complete")



