import ast
from pathlib import Path

import pytest

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
            "normalize_successful_timing_history",
            "adaptive_grace_history_size",
            "adaptive_grace_floor_seconds",
            "adaptive_grace_safety_margin_seconds",
            "adaptive_grace_rounding_seconds",
            "adaptive_activation_grace_seconds",
            "record_successful_transaction_timing",
            "rotation_interval_seconds",
            "script_execution_interval_seconds",
        )
        cls.functions["KEYCHAIN_KEEP_LAST"] = 3
        cls.functions["MIN_ROTATION_INTERVAL"] = 60
        cls.functions["max_installed_keys"] = lambda: 4
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

    def test_full_ring_without_pending_is_not_modified(self):
        assert self.plan({0, 1, 2, 3}, 1, None) == (
            None,
            [],
            "ACTIVE_PENDING_PAIR_INCOMPLETE",
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

    def test_ssh_rotation_runs_after_macsec_keyring_cycle(self):
        tree = ast.parse(ONBOX.read_text(encoding="utf-8"))
        run_master = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "run_master"
        )
        source = ast.get_source_segment(ONBOX.read_text(encoding="utf-8"), run_master)
        assert source.index("run_master_rolling_link(link)") < source.index(
            "run_peer_key_rotation_cycle(DEVICE, peer_devices)"
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
