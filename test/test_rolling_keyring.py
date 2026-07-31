import ast
from pathlib import Path


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
            "trim_installed_keys_preserve_active",
        )
        cls.functions["KEYCHAIN_KEEP_LAST"] = 3
        cls.functions["max_installed_keys"] = lambda: 4

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

    def test_full_ring_reuses_only_bilaterally_retired_slot(self):
        assert self.plan({0, 1, 2, 3}, 1, 2, 0, 0) == (
            "ROLLING_REPLACEMENT",
            [0],
            None,
        )

    def test_active_slot_is_never_replaced(self):
        assert self.plan({0, 1, 2, 3}, 1, 2, 1, 1) == (
            None,
            [],
            "RETIRED_SLOT_STILL_PROTECTED",
        )

    def test_next_slot_is_never_replaced(self):
        assert self.plan({0, 1, 2, 3}, 1, 2, 2, 2) == (
            None,
            [],
            "RETIRED_SLOT_STILL_PROTECTED",
        )

    def test_peer_retired_slot_mismatch_blocks_rotation(self):
        assert self.plan({0, 1, 2, 3}, 1, 2, 0, 3) == (
            None,
            [],
            "RETIRED_SLOT_MISMATCH",
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
