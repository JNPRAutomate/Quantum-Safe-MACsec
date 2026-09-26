import ast
import base64
import calendar
import json
import os
from pathlib import Path
from types import SimpleNamespace
import time

import pytest
import subprocess

from lib.qkd.inventory_builder import validate_qkd_policy


ROOT = Path(__file__).resolve().parents[1]
ONBOX = ROOT / "artifacts" / "qkd_onbox.py"


def load_functions(*names):
    tree = ast.parse(ONBOX.read_text(encoding="utf-8"))
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    namespace = {}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(ONBOX), "exec"), namespace)
    return namespace


class TestRollingKeyringPlan:
    @classmethod
    def setup_class(cls):
        cls.functions = load_functions(
            "bootstrap_seed_key_id",
            "select_ring_update_slots",
            "_slots_consumed_before_active",
            "trim_installed_keys_preserve_active",
            "normalize_slot_ring",
            "record_installed_key",
            "reconcile_installed_keys_from_pending",
            "normalize_successful_timing_history",
            "adaptive_grace_history_size",
            "adaptive_grace_floor_seconds",
            "adaptive_grace_safety_margin_seconds",
            "adaptive_grace_rounding_seconds",
            "adaptive_activation_grace_seconds",
            "record_successful_transaction_timing",
            "rotation_interval_seconds",
            "script_execution_interval_seconds",
            "active_slot_index",
            "configured_bootstrap_seed_only",
        )
        cls.functions["KEYCHAIN_KEEP_LAST"] = 3
        cls.functions["MIN_ROTATION_INTERVAL"] = 60
        cls.functions["max_installed_keys"] = lambda: 4
        cls.functions["time"] = SimpleNamespace(time=lambda: 123)
        cls.functions["qkd_policy"] = lambda: {
            "execution_interval_seconds": 60,
            "key_activation_interval_seconds": 120,
            "adaptive_grace_history_size": 32,
            "adaptive_grace_floor_seconds": 150,
            "adaptive_grace_safety_margin_seconds": 30,
            "adaptive_grace_rounding_seconds": 60,
        }

    def plan(self, slots, active, next_slot, local_previous=None, peer_previous=None):
        return self.functions["select_ring_update_slots"](
            slots,
            4,
            active,
            next_slot,
            local_previous,
            peer_previous,
        )

    def test_seed_completes_only_missing_future_slots(self):
        assert self.plan({0}, 0, None) == ("RING_COMPLETION", [1, 2, 3], None)

    def test_four_slot_ring_replaces_n_minus_two_slots(self):
        assert self.plan({0, 1, 2, 3}, 1, 2) == (
            "ROLLING_REPLACEMENT",
            [3, 0],
            None,
        )

    def test_active_and_pending_slots_are_never_replaced(self):
        operation, slots, reason = self.plan({0, 1, 2, 3}, 1, 2)
        assert operation == "ROLLING_REPLACEMENT"
        assert reason is None
        assert set(slots) == {0, 3}
        assert {1, 2}.isdisjoint(slots)

    def test_full_ring_without_pending_self_heals_by_rearming_n_minus_two_slots(self):
        # Every slot is configured but none carries a future start-time (the
        # ring was exhausted, e.g. by a transient rotation failure). Rather
        # than stalling forever, re-arm the N-2 window starting right after active.
        assert self.plan({0, 1, 2, 3}, 1, None) == (
            "RING_REARM",
            [2, 3],
            None,
        )

    def test_invalid_active_slot_is_not_modified(self):
        assert self.plan({0, 1, 2, 3}, None, None) == (
            None,
            [],
            "ACTIVE_SLOT_INVALID",
        )

    def test_non_adjacent_active_pending_pair_is_not_modified(self):
        assert self.plan({0, 1, 2, 3}, 1, 3) == (
            None,
            [],
            "ACTIVE_PENDING_PAIR_NOT_ADJACENT",
        )

    def test_two_slot_ring_has_no_replaceable_capacity(self):
        assert self.functions["select_ring_update_slots"]({0, 1}, 2, 0, 1) == (
            None,
            [],
            "NO_REPLACEABLE_SLOTS",
        )

    def test_partial_non_seed_ring_is_not_modified(self):
        assert self.plan({0, 1}, 0, 1) == (None, [], "NOT_CLEAN_SEED")

    def test_seed_identity_matches_orchestrator_contract(self):
        assert (
            self.functions["bootstrap_seed_key_id"]("QKD_CA1", 0)
            == "QKD_CA1:bootstrap:key-name:0"
        )

    def test_state_retains_metadata_for_all_four_slots(self):
        state = {
            "active_key_id": "key-0",
            "installed_keys": [
                {"key_id": f"key-{slot}", "slot": slot}
                for slot in range(4)
            ],
        }
        result = self.functions["trim_installed_keys_preserve_active"](state)
        assert {item["slot"] for item in result["installed_keys"]} == {0, 1, 2, 3}

    def test_slot_replacement_does_not_discard_new_entry_for_stale_active(self):
        state = {
            "active_key_id": "bootstrap",
            "installed_keys": [
                {"key_id": "bootstrap", "slot": 0},
                {"key_id": "old-1", "slot": 1},
                {"key_id": "key-2", "slot": 2},
                {"key_id": "key-3", "slot": 3},
            ],
        }
        state = self.functions["record_installed_key"](
            state, 4, "new-0", "2026-07-31.15:48:10", 0, "pending"
        )
        state = self.functions["record_installed_key"](
            state, 5, "new-1", "2026-07-31.15:50:10", 1, "pending"
        )
        assert [item["key_id"] for item in state["slots"]] == [
            "new-0",
            "new-1",
            "key-2",
            "key-3",
        ]

    def test_pending_metadata_repairs_existing_stale_slot_state(self):
        state = {
            "active_key_id": "key-3",
            "installed_keys": [
                {"key_id": "bootstrap", "slot": 0},
                {"key_id": "old-1", "slot": 1},
                {"key_id": "key-2", "slot": 2},
                {"key_id": "key-3", "slot": 3},
            ],
            "pending_keys": [
                {
                    "generation": 4,
                    "key_id": "new-0",
                    "start_time": "2026-07-31.15:48:10",
                    "slot": 0,
                },
                {
                    "generation": 5,
                    "key_id": "new-1",
                    "start_time": "2026-07-31.15:50:10",
                    "slot": 1,
                },
            ],
        }
        state = self.functions["reconcile_installed_keys_from_pending"](state)
        assert [item["key_id"] for item in state["slots"]] == [
            "new-0",
            "new-1",
            "key-2",
            "key-3",
        ]

    def test_sixty_slot_ring_replaces_fifty_eight_slots(self):
        operation, slots, reason = self.functions["select_ring_update_slots"](
            set(range(60)),
            60,
            57,
            58,
        )
        assert operation == "ROLLING_REPLACEMENT"
        assert reason is None
        assert len(slots) == 58
        assert slots[:3] == [59, 0, 1]
        assert slots[-1] == 56
        assert {57, 58}.isdisjoint(slots)

    def test_adaptive_grace_uses_maximum_successful_sample_and_rounds_up(self):
        state = {
            "successful_timing_history": [
                {
                    "completed_at_ms": 1,
                    "delta_commit_ms": 10_000,
                    "delta_ack_ms": 81_001,
                    "delta_total_ms": 91_001,
                }
            ]
        }
        assert self.functions["adaptive_activation_grace_seconds"](state) == 180

    def test_future_n_minus_two_targets_are_not_treated_as_consumed(self):
        self.functions["epoch_from_junos_start_time"] = lambda value: int(value)
        state = {
            "slots": [
                {"start_time": "0"},
                {"start_time": "120"},
                {"start_time": "240"},
                {"start_time": "360"},
            ]
        }
        assert not self.functions["_slots_consumed_before_active"](state, [2, 3], 0)
        assert self.functions["_slots_consumed_before_active"](state, [0, 1], 2)

    def test_successful_timing_history_is_capped_and_failures_are_ignored(self):
        state = {"successful_timing_history": []}
        record = self.functions["record_successful_transaction_timing"]
        for index in range(35):
            transaction = {
                "t0_commit_request_ms": index * 10_000,
                "t1_commit_finished_ms": index * 10_000 + 1_000,
                "t2_peer_send_ms": index * 10_000 + 2_000,
            }
            state = record(state, transaction, index * 10_000 + 3_000)
        state = record(state, {}, 999_999)
        assert len(state["successful_timing_history"]) == 32
        assert state["successful_timing_history"][0]["completed_at_ms"] == 33_000

    def test_script_execution_and_key_activation_intervals_are_independent(self):
        assert self.functions["script_execution_interval_seconds"]() == 60
        assert self.functions["rotation_interval_seconds"]() == 120

    def test_live_config_wins_over_stale_active_slot_metadata(self):
        self.functions["get_configured_keychain_entries"] = lambda *args, **kwargs: {
            0: {"key_name": "OLD_CKN"},
            1: {"key_name": "LIVE_CKN"},
            2: {"key_name": "FUTURE_2"},
            3: {"key_name": "FUTURE_3"},
        }
        self.functions["ckn_from_key_id"] = lambda key_id: "LIVE_CKN"
        self.functions["normalize_hex_string"] = lambda value: str(value or "").upper()
        state = {
            "active_key_id": "active-key-id",
            "installed_keys": [
                {"key_id": "active-key-id", "slot": 0},
            ],
        }
        assert (
            self.functions["active_slot_index"](
                state,
                iface="et-0/0/0",
                keychain_name="QKD_CA_MX1_MX2",
            )
            == 1
        )

    def test_live_seed_only_shape_detects_orchestrator_reset(self):
        self.functions["stable_keychain_name"] = lambda link: "QKD_CA_MX1_MX2"
        self.functions["get_configured_keychain_entries"] = lambda *args, **kwargs: {
            0: {"key_name": "BOOTSTRAP_CKN"},
        }
        self.functions["bootstrap_seed_key_id"] = (
            lambda keychain_name, slot=0: f"{keychain_name}:bootstrap:key-name:{slot}"
        )
        self.functions["ckn_from_key_id"] = lambda key_id: "BOOTSTRAP_CKN"
        self.functions["normalize_hex_string"] = lambda value: str(value or "").upper()
        assert self.functions["configured_bootstrap_seed_only"](
            {},
            "et-0/0/0",
        )

    def test_full_ring_is_not_mistaken_for_orchestrator_reset(self):
        self.functions["stable_keychain_name"] = lambda link: "QKD_CA_MX1_MX2"
        self.functions["get_configured_keychain_entries"] = lambda *args, **kwargs: {
            slot: {"key_name": f"CKN_{slot}"} for slot in range(4)
        }
        assert not self.functions["configured_bootstrap_seed_only"](
            {},
            "et-0/0/0",
        )

    def test_rpc_rotation_runs_after_macsec_keyring_cycle(self):
        tree = ast.parse(ONBOX.read_text(encoding="utf-8"))
        run_master = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "run_master"
        )
        source = ast.get_source_segment(ONBOX.read_text(encoding="utf-8"), run_master)
        assert source.index("run_master_rolling_link(link)") < source.index(
            "run_rpc_key_rotation_cycle()"
        )

    def test_inflight_recovery_precedes_macsec_inuse_guard(self):
        tree = ast.parse(ONBOX.read_text(encoding="utf-8"))
        rolling_link = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "run_master_rolling_link"
        )
        source = ast.get_source_segment(ONBOX.read_text(encoding="utf-8"), rolling_link)
        assert source.index('state.get("inflight_install")') < source.index(
            "macsec_has_inuse_sa(iface, expected_ca=ca_name)"
        )

    def test_slave_batch_reconciles_seed_reset_before_install(self):
        tree = ast.parse(ONBOX.read_text(encoding="utf-8"))
        slave_batch = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "run_slave_install_key_batch"
        )
        source = ast.get_source_segment(ONBOX.read_text(encoding="utf-8"), slave_batch)
        assert source.index("configured_bootstrap_seed_only(link, iface)") < source.index(
            "INSTALL-KEY-BATCH REQUEST"
        )
        assert "SEED_STATE_SAVE_FAILED" in source

    def test_post_ack_finalize_reconciles_before_state_save(self):
        tree = ast.parse(ONBOX.read_text(encoding="utf-8"))
        rolling_link = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "run_master_rolling_link"
        )
        source = ast.get_source_segment(ONBOX.read_text(encoding="utf-8"), rolling_link)
        finalize_index = source.index(
            "state = _finalize_bilateral_install(state, peer_payload, operation)"
        )
        reconcile_index = source.index(
            "state = reconcile_state_with_router(link, iface, state)",
            finalize_index,
        )
        save_index = source.index("if not save_db_state(peer, iface, state):", reconcile_index)
        assert finalize_index < reconcile_index < save_index

    def test_rpc_batch_transport_uses_script_user_identity(self):
        tree = ast.parse(ONBOX.read_text(encoding="utf-8"))
        send_command = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "send_command"
        )
        source = ast.get_source_segment(ONBOX.read_text(encoding="utf-8"), send_command)

        assert "peer_user = SCRIPT_USER" in source
        assert 'ssh_transport_options(RPC_SSH_KEY)' in source
        assert 'f"SSH RPC EXEC {peer_user}@{peer_ip}' in source
        assert 'timeout = peer_batch_ack_timeout_seconds()' in source
        assert '"OK INSTALL-KEY-BATCH" not in stdout' in source
        assert '"ConnectTimeout=10"' in source

    def test_rpc_recovery_does_not_read_scp_ack(self):
        tree = ast.parse(ONBOX.read_text(encoding="utf-8"))
        resume = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "resume_inflight_install"
        )
        source = ast.get_source_segment(ONBOX.read_text(encoding="utf-8"), resume)

        assert 'peer_state = get_peer_status(link, iface)' in source
        assert "read_remote_peer_batch_ack" not in source
        assert "_state_records_match(peer_state, records)" in source

    def test_rpc_success_is_recorded_before_bilateral_finalize(self):
        tree = ast.parse(ONBOX.read_text(encoding="utf-8"))
        rolling_link = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "run_master_rolling_link"
        )
        source = ast.get_source_segment(ONBOX.read_text(encoding="utf-8"), rolling_link)
        send_index = source.index('if not send_command(')
        rpc_success_index = source.index('reason_stage="MASTER_RPC_RESPONSE"', send_index)
        finalize_index = source.index(
            "state = _finalize_bilateral_install(state, peer_payload, operation)",
            rpc_success_index,
        )

        assert send_index < rpc_success_index < finalize_index


class TestTransactionalRpcKeyRotation:
    def test_corrupt_rotation_state_blocks_new_transaction(self, tmp_path):
        functions = load_functions(
            "rpc_key_rotation_state_file",
            "load_rpc_key_rotation_state",
        )
        state_file = tmp_path / "qkd_rpc_key_rotation.json"
        state_file.write_text("{not-json", encoding="utf-8")
        functions.update(
            {
                "STATE_DIR": str(tmp_path),
                "Path": Path,
                "json": json,
                "format_epoch_human": lambda value: str(value),
            }
        )

        with pytest.raises(RuntimeError, match="invalid RPC key rotation state"):
            functions["load_rpc_key_rotation_state"]()

    def test_incomplete_peer_metadata_blocks_rotation(self):
        functions = load_functions("_rpc_rotation_peers")
        functions["managed_links"] = lambda: [
            {
                "peer": "EVO2",
                "peer_ip": "192.0.2.2",
                "peer_interface": None,
            }
        ]

        with pytest.raises(RuntimeError, match="incomplete direct RPC peer metadata"):
            functions["_rpc_rotation_peers"]()

    def test_source_filter_preserves_orchestrator_and_other_peer_keys(self):
        functions = load_functions("_rpc_keys_for_source")
        functions["SCRIPT_USER"] = "etsi_user"
        functions["_get_all_junos_auth_keys_for_user"] = lambda _user: [
            "ssh-ed25519 AAAAORCHESTRATOR orchestrator@linux",
            "ssh-ed25519 AAAAOLD qkd-rpc@EVO1",
            "ssh-ed25519 AAAAOTHER qkd-rpc@EVO2",
            "ssh-ed25519 AAAANEW qkd-rpc@EVO1",
        ]

        assert functions["_rpc_keys_for_source"]("EVO1") == [
            "ssh-ed25519 AAAAOLD qkd-rpc@EVO1",
            "ssh-ed25519 AAAANEW qkd-rpc@EVO1",
        ]

    def test_finalize_deletes_only_old_key_for_source(self):
        functions = load_functions("_decode_rpc_pubkey", "_apply_rpc_pubkey")
        commands = []
        new_key = "ssh-ed25519 AAAANEW qkd-rpc@EVO1"
        functions.update(
            {
                "base64": base64,
                "re": __import__("re"),
                "SCRIPT_USER": "etsi_user",
                "CLI_PATH": "/usr/sbin/cli",
                "_rpc_keys_for_source": lambda _source: [
                    "ssh-ed25519 AAAAOLD qkd-rpc@EVO1",
                    new_key,
                ],
                "acquire_junos_commit_lock": lambda: True,
                "release_junos_commit_lock": lambda: None,
                "junos_output_has_error": lambda _out, _err: False,
                "log": lambda *_args, **_kwargs: None,
                "subprocess": SimpleNamespace(
                    PIPE=object(),
                    run=lambda argv, **_kwargs: (
                        commands.append(argv[-1])
                        or SimpleNamespace(
                            returncode=0,
                            stdout=b"commit complete",
                            stderr=b"",
                        )
                    ),
                    TimeoutExpired=subprocess.TimeoutExpired,
                ),
            }
        )
        encoded = base64.urlsafe_b64encode(new_key.encode()).decode()

        assert functions["_apply_rpc_pubkey"]("EVO1", encoded, finalize=True)
        assert len(commands) == 1
        assert "AAAAOLD" in commands[0]
        assert "AAAANEW" not in commands[0]
        assert "orchestrator@linux" not in commands[0]

    def test_verify_physically_uses_next_private_key(self):
        functions = load_functions("_verify_rpc_next_key")
        calls = []
        functions.update(
            {
                "RPC_SSH_KEY": "/var/home/etsi_user/.ssh/qkd_rpc_id_ed25519",
                "SCRIPT_USER": "etsi_user",
                "json": json,
                "log": lambda *_args, **_kwargs: None,
                "ssh_transport_options": lambda path: ["-i", path],
                "subprocess": SimpleNamespace(
                    PIPE=object(),
                    run=lambda argv, **_kwargs: (
                        calls.append(argv)
                        or SimpleNamespace(returncode=0, stdout=b'{"ok": true}', stderr=b"")
                    )
                ),
            }
        )

        assert functions["_verify_rpc_next_key"](
            {"name": "EVO2", "ip": "10.0.0.2", "interface": "et-0/0/1"}
        )
        assert "/var/home/etsi_user/.ssh/qkd_rpc_id_ed25519.next" in calls[0]

    def test_application_sentinel_is_required(self):
        functions = load_functions("_run_rpc_key_action")
        functions.update(
            {
                "base64": base64,
                "DEVICE_NAME": "EVO1",
                "SCRIPT_USER": "etsi_user",
                "log": lambda *_args, **_kwargs: None,
                "ssh_transport_options": lambda path: ["-i", path],
                "subprocess": SimpleNamespace(
                    run=lambda *_args, **_kwargs: SimpleNamespace(
                        returncode=0,
                        stdout=b"commit complete",
                        stderr=b"",
                    )
                ),
            }
        )

        assert not functions["_run_rpc_key_action"](
            {"name": "EVO2", "ip": "10.0.0.2"},
            "prepare-rpc-pubkey",
            "ssh-ed25519 AAAANEW qkd-rpc@EVO1",
            "/tmp/current",
        )

    def test_missing_peer_blocks_activation(self):
        functions = load_functions("run_rpc_key_rotation_cycle")
        activated = []
        state = {
            "last_rotation_timestamp": 0,
            "rotation_count": 0,
            "transaction": {
                "id": "txn",
                "phase": "generated",
                "pubkey": "ssh-ed25519 AAAANEW qkd-rpc@EVO1",
                "peers": ["EVO2", "MX1"],
                "prepared_peers": [],
                "verified_peers": [],
                "activated": False,
                "finalized_peers": [],
            },
        }
        functions.update(
            {
                "RPC_SSH_KEY": "/tmp/rpc",
                "load_rpc_key_rotation_state": lambda: state,
                "save_rpc_key_rotation_state": lambda _state: None,
                "_rpc_rotation_peers": lambda: {
                    "EVO2": {"name": "EVO2"},
                    "MX1": {"name": "MX1"},
                },
                "_public_key_from_private": lambda _path: "ssh-ed25519 AAAAOLD",
                "_public_keys_match": lambda left, right: left in right,
                "_write_active_rpc_public_key": lambda _pubkey: True,
                "_run_rpc_key_action": lambda peer, *_args: peer["name"] != "MX1",
                "_verify_rpc_next_key": lambda _peer: True,
                "_activate_rpc_next_keypair": lambda: activated.append(True) or True,
                "log": lambda *_args, **_kwargs: None,
            }
        )

        assert not functions["run_rpc_key_rotation_cycle"]()
        assert activated == []
        assert state["transaction"]["prepared_peers"] == ["EVO2"]

    def test_recovery_after_activation_resumes_finalize(self):
        functions = load_functions("run_rpc_key_rotation_cycle")
        saved = []
        activated = []
        pubkey = "ssh-ed25519 AAAANEW qkd-rpc@EVO1"
        state = {
            "last_rotation_timestamp": 0,
            "rotation_count": 4,
            "transaction": {
                "id": "txn",
                "phase": "activating",
                "pubkey": pubkey,
                "peers": ["EVO2"],
                "prepared_peers": ["EVO2"],
                "verified_peers": ["EVO2"],
                "activated": False,
                "finalized_peers": [],
            },
        }
        functions.update(
            {
                "RPC_SSH_KEY": "/tmp/rpc",
                "time": SimpleNamespace(time=lambda: 500),
                "load_rpc_key_rotation_state": lambda: state,
                "save_rpc_key_rotation_state": lambda value: saved.append(
                    json.loads(json.dumps(value))
                ),
                "_rpc_rotation_peers": lambda: {"EVO2": {"name": "EVO2"}},
                "_public_key_from_private": lambda _path: "ssh-ed25519 AAAANEW",
                "_public_keys_match": lambda left, right: left in right,
                "_write_active_rpc_public_key": lambda value: value == pubkey,
                "_run_rpc_key_action": lambda *_args: True,
                "_verify_rpc_next_key": lambda _peer: True,
                "_activate_rpc_next_keypair": lambda: activated.append(True) or True,
                "log": lambda *_args, **_kwargs: None,
            }
        )

        assert functions["run_rpc_key_rotation_cycle"]()
        assert activated == []
        assert saved[-1]["transaction"] is None
        assert saved[-1]["rotation_count"] == 5

    def test_activation_swaps_next_and_keeps_previous(self, tmp_path):
        functions = load_functions("_activate_rpc_next_keypair")
        active = tmp_path / "qkd_rpc_id_ed25519"
        active.write_text("old-private")
        Path(f"{active}.pub").write_text("old-public")
        Path(f"{active}.next").write_text("new-private")
        Path(f"{active}.next.pub").write_text("new-public")
        functions.update(
            {
                "RPC_SSH_KEY": str(active),
                "os": os,
                "shutil": __import__("shutil"),
                "log": lambda *_args, **_kwargs: None,
            }
        )

        assert functions["_activate_rpc_next_keypair"]()
        assert active.read_text() == "new-private"
        assert Path(f"{active}.pub").read_text() == "new-public"
        assert Path(f"{active}.prev").read_text() == "old-private"
        assert Path(f"{active}.pub.prev").read_text() == "old-public"


class TestTimezoneSafeStartTimes:
    @classmethod
    def setup_class(cls):
        cls.functions = load_functions(
            "epoch_from_junos_start_time",
            "junos_start_time_from_epoch",
        )
        cls.functions["calendar"] = calendar
        cls.functions["time"] = time

    def test_generated_start_time_is_explicit_utc(self):
        value = self.functions["junos_start_time_from_epoch"](1790167106)
        assert value == "2026-09-23.12:38:26 +0000"

    def test_offset_and_utc_forms_represent_same_instant(self):
        parse = self.functions["epoch_from_junos_start_time"]
        assert parse("2026-09-23.14:38:26 +0200") == parse(
            "2026-09-23.12:38:26 +0000"
        )


class TestBilateralSlotMetadata:
    @classmethod
    def setup_class(cls):
        cls.functions = load_functions("_slot_metadata_matches")
        cls.functions["epoch_from_junos_start_time"] = lambda value: {
            "one": 1,
            "two": 2,
        }.get(value, value)

    def test_accepts_bootstrap_seed_with_platform_timezone_difference(self):
        local_state = {
            "slots": [{
                "key_id": "QKD_CA:bootstrap:key-name:0",
                "start_time": "2026-1-1.00:01:00 +0100",
            }],
        }
        peer_state = {
            "slots": [{
                "key_id": "QKD_CA:bootstrap:key-name:0",
                "start_time": "2026-1-1.00:01:00 +0000",
            }],
        }

        assert self.functions["_slot_metadata_matches"](local_state, peer_state, {0})

    def test_rejects_different_timestamp_for_non_bootstrap_slot(self):
        local_state = {
            "slots": [
                None,
                {"key_id": "key-1", "start_time": "one"},
            ],
        }
        peer_state = {
            "slots": [
                None,
                {"key_id": "key-1", "start_time": "two"},
            ],
        }

        assert not self.functions["_slot_metadata_matches"](local_state, peer_state, {1})


class TestRpcBatchDelivery:
    def setup_method(self):
        self.functions = load_functions("send_command")
        self.calls = []
        payload = json.dumps(
            [{
                "slot": 1,
                "key_id": "key-1",
                "generation": 1,
                "start_time": "2026-09-24.15:00:00 +0000",
            }],
            separators=(",", ":"),
        )
        self.payload_b64 = base64.urlsafe_b64encode(payload.encode()).decode()
        self.functions.update(
            {
                "validate_link_runtime": lambda link, require_peer_transport: True,
                "format_next_start_time_with_millis": lambda value: value,
                "epoch_from_junos_start_time": lambda value: 2_000_000_000,
                "peer_enqueue_min_margin_seconds": lambda: 60,
                "SCRIPT_USER": "etsi_user",
                "RPC_SSH_KEY": "/var/home/etsi_user/.ssh/qkd_rpc_id_ed25519",
                "ssh_transport_options": lambda key: ["-i", key],
                "peer_batch_ack_timeout_seconds": lambda: 150,
                "log": lambda *args, **kwargs: None,
                "base64": base64,
                "json": json,
                "time": SimpleNamespace(time=lambda: 1_000_000_000),
            }
        )

    def run_rpc(self, stdout=b"OK INSTALL-KEY-BATCH count=1\n"):
        def run(cmd, stdout=None, stderr=None, timeout=None):
            self.calls.append((cmd, timeout))
            return SimpleNamespace(returncode=0, stdout=self.stdout, stderr=b"")

        self.stdout = stdout
        self.functions["subprocess"] = SimpleNamespace(
            PIPE=object(),
            TimeoutExpired=subprocess.TimeoutExpired,
            run=run,
        )
        return self.functions["send_command"](
            {
                "peer_ip": "100.123.113.1",
                "peer_interface": "et-0/0/7",
                "peer_sae": "sae-002",
            },
            "install-key-batch",
            "et-0/0/2",
            batch_b64=self.payload_b64,
            ack_id="ack-1",
        )

    def test_rpc_uses_script_user_identity_and_positive_ack(self):
        assert self.run_rpc()
        cmd, timeout = self.calls[0]

        assert cmd[0] == "ssh"
        assert "/var/home/etsi_user/.ssh/qkd_rpc_id_ed25519" in cmd
        assert "etsi_user@100.123.113.1" in cmd
        assert "action install-key-batch iface et-0/0/7" in cmd[-1]
        assert timeout == 150

    def test_rpc_rejects_zero_exit_without_positive_ack(self):
        assert not self.run_rpc(stdout=b"")


class TestRpcPeerStatus:
    def setup_method(self):
        self.functions = load_functions("get_peer_status")
        self.calls = []
        self.logs = []
        self.functions.update(
            {
                "validate_link_runtime": lambda link, require_peer_transport: True,
                "SCRIPT_USER": "etsi_user",
                "RPC_SSH_KEY": "/var/home/etsi_user/.ssh/qkd_rpc_id_ed25519",
                "ssh_transport_options": lambda key: ["-i", key],
                "json": json,
                "log": lambda *args, **kwargs: self.logs.append(args),
            }
        )
        self.link = {
            "peer_ip": "100.123.113.1",
            "peer_interface": "et-0/0/7",
            "peer_sae": "sae-002",
        }

    def run_status(self, returncode=0, stdout=b'{"active_key_id":"key-1"}\n', stderr=b""):
        def run(cmd, stdout=None, stderr=None, timeout=None):
            self.calls.append((cmd, timeout))
            return SimpleNamespace(
                returncode=returncode,
                stdout=self.stdout,
                stderr=self.stderr,
            )

        self.stdout = stdout
        self.stderr = stderr
        self.functions["subprocess"] = SimpleNamespace(
            PIPE=object(),
            TimeoutExpired=subprocess.TimeoutExpired,
            run=run,
        )
        return self.functions["get_peer_status"](self.link, "et-0/0/2")

    def test_status_uses_script_user_rpc_without_scp(self):
        assert self.run_status() == {"active_key_id": "key-1"}
        cmd, timeout = self.calls[0]

        assert cmd == [
            "ssh",
            "-i",
            "/var/home/etsi_user/.ssh/qkd_rpc_id_ed25519",
            "etsi_user@100.123.113.1",
            "op qkd_onbox.py action status iface et-0/0/7",
        ]
        assert timeout == 10
        assert all("SCP" not in str(entry) for entry in self.logs)

    def test_status_accepts_json_wrapped_by_cli_output(self):
        stdout = b'warning text\n{"active_key_id":"key-1"}\ncli trailer\n'

        assert self.run_status(stdout=stdout) == {"active_key_id": "key-1"}

    def test_status_rejects_nonzero_exit(self):
        assert self.run_status(returncode=1, stderr=b"permission denied") is None

    def test_status_rejects_malformed_json(self):
        assert self.run_status(stdout=b"not-json") is None


def test_qkd_policy_accepts_safe_independent_timers():
    validate_qkd_policy(
        {
            "rekey_enabled": True,
            "batch_enabled": True,
            "execution_interval_seconds": 60,
            "key_activation_interval_seconds": 120,
            "key_batch_size": 4,
            "max_installed_keys": 4,
            "peer_batch_ack_timeout_seconds": 150,
            "adaptive_grace_floor_seconds": 150,
            "adaptive_grace_safety_margin_seconds": 30,
            "adaptive_grace_rounding_seconds": 60,
        }
    )


def test_qkd_policy_rejects_grace_beyond_protected_horizon():
    with pytest.raises(ValueError, match="protected horizon"):
        validate_qkd_policy(
            {
                "rekey_enabled": True,
                "execution_interval_seconds": 300,
                "key_activation_interval_seconds": 120,
                "key_batch_size": 4,
                "max_installed_keys": 4,
                "peer_batch_ack_timeout_seconds": 390,
                "adaptive_grace_floor_seconds": 390,
                "adaptive_grace_safety_margin_seconds": 30,
                "adaptive_grace_rounding_seconds": 60,
            }
        )


def test_qkd_policy_rejects_ring_without_n_minus_two_capacity():
    with pytest.raises(ValueError, match="between 4 and 64"):
        validate_qkd_policy(
            {
                "rekey_enabled": True,
                "execution_interval_seconds": 60,
                "key_activation_interval_seconds": 120,
                "key_batch_size": 2,
                "max_installed_keys": 2,
            }
        )


def test_qkd_policy_rejects_negative_adaptive_safety_margin():
    with pytest.raises(ValueError, match="safety_margin_seconds"):
        validate_qkd_policy(
            {
                "rekey_enabled": True,
                "execution_interval_seconds": 60,
                "key_activation_interval_seconds": 120,
                "key_batch_size": 4,
                "max_installed_keys": 4,
                "peer_batch_ack_timeout_seconds": 150,
                "adaptive_grace_floor_seconds": 150,
                "adaptive_grace_safety_margin_seconds": -1,
            }
        )


def _valid_policy_with_rpc_rotation(interval):
    return {
        "rekey_enabled": True,
        "execution_interval_seconds": 60,
        "key_activation_interval_seconds": 120,
        "key_batch_size": 4,
        "max_installed_keys": 4,
        "peer_batch_ack_timeout_seconds": 150,
        "adaptive_grace_floor_seconds": 150,
        "adaptive_grace_safety_margin_seconds": 30,
        "adaptive_grace_rounding_seconds": 60,
        "rpc_key_rotation_interval_seconds": interval,
    }


@pytest.mark.parametrize("interval", [0, 60, 600])
def test_qkd_policy_accepts_safe_rpc_rotation_intervals(interval):
    validate_qkd_policy(_valid_policy_with_rpc_rotation(interval))


@pytest.mark.parametrize("interval", [-1, 1, 59])
def test_qkd_policy_rejects_unsafe_rpc_rotation_intervals(interval):
    with pytest.raises(ValueError, match="rpc_key_rotation_interval_seconds"):
        validate_qkd_policy(_valid_policy_with_rpc_rotation(interval))
