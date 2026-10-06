import ast
import json
from pathlib import Path

from lib.qbt.runtime_builder import qbt_onbox_path


def _load_guard(tmp_path):
    source = qbt_onbox_path().read_text()
    wanted = {"_state_write_is_stale", "save_db_state"}
    nodes = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            nodes.append(node)
        if isinstance(node, ast.Assign) and any(
            getattr(target, "id", "") == "STATE_SAVE_POLICY" for target in node.targets
        ):
            nodes.append(node)
    written = []
    seed_only = {"value": True}
    namespace = {
        "seed_only": seed_only,
        "link_by_interface": lambda iface: {"interface": iface},
        "configured_bootstrap_seed_only": lambda link, iface: seed_only["value"],
        "Path": Path,
        "json": json,
        "db_state_file": lambda peer, iface: str(tmp_path / "state.json"),
        "log": lambda *args, **kwargs: written.append(args[0]),
        "_save_db_state_unlocked": lambda peer, iface, state: (
            (tmp_path / "state.json").write_text(json.dumps(state)) or True
        ),
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "guard", "exec"), namespace)
    return namespace, written


def _disk(tmp_path):
    return json.loads((tmp_path / "state.json").read_text())["generation"]


def test_stale_periodic_write_cannot_revert_a_newer_install(tmp_path):
    guard, logs = _load_guard(tmp_path)
    (tmp_path / "state.json").write_text(json.dumps({"generation": 11}))

    assert guard["save_db_state"]("EVO1", "et-0/0/1", {"generation": 9}) is True

    assert _disk(tmp_path) == 11
    assert any("STATE SAVE DROPPED" in line for line in logs)


def test_newer_or_equal_generation_is_written(tmp_path):
    guard, _ = _load_guard(tmp_path)
    (tmp_path / "state.json").write_text(json.dumps({"generation": 9}))

    guard["save_db_state"]("EVO1", "et-0/0/1", {"generation": 9})
    assert _disk(tmp_path) == 9
    guard["save_db_state"]("EVO1", "et-0/0/1", {"generation": 11})
    assert _disk(tmp_path) == 11


SEEDED = {"generation": 0, "ring_phase": "seeded"}


def test_seed_adoption_is_written_while_the_keychain_holds_only_the_seed(tmp_path):
    guard, _ = _load_guard(tmp_path)

    guard["save_db_state"]("EVO1", "et-0/0/1", {"generation": 5})
    guard["save_db_state"]("EVO1", "et-0/0/1", dict(SEEDED))

    assert _disk(tmp_path) == 0


def test_a_run_that_adopted_the_seed_before_an_install_cannot_erase_it(tmp_path):
    # Seen on a router rebuilt from empty: the slave saved generation 3 after
    # installing the batch, then a run that had adopted the seed earlier saved
    # generation 0 and the pair never aligned.
    guard, logs = _load_guard(tmp_path)
    (tmp_path / "state.json").write_text(json.dumps({"generation": 3}))
    guard["seed_only"]["value"] = False

    guard["save_db_state"]("EVO1", "et-0/0/1", dict(SEEDED))

    assert _disk(tmp_path) == 3
    assert any("STATE SAVE DROPPED" in line for line in logs)


def test_a_missing_state_file_is_always_written(tmp_path):
    guard, _ = _load_guard(tmp_path)
    guard["seed_only"]["value"] = False

    guard["save_db_state"]("EVO1", "et-0/0/1", dict(SEEDED))

    assert _disk(tmp_path) == 0


def test_generation_zero_without_the_seeded_phase_is_still_stale(tmp_path):
    guard, _ = _load_guard(tmp_path)
    (tmp_path / "state.json").write_text(json.dumps({"generation": 3}))

    guard["save_db_state"]("EVO1", "et-0/0/1", {"generation": 0, "ring_phase": "ready"})

    assert _disk(tmp_path) == 3


def test_bilateral_install_handlers_are_authoritative(tmp_path):
    guard, _ = _load_guard(tmp_path)
    (tmp_path / "state.json").write_text(json.dumps({"generation": 11}))
    guard["STATE_SAVE_POLICY"]["force"] = True

    guard["save_db_state"]("EVO1", "et-0/0/1", {"generation": 3})

    assert _disk(tmp_path) == 3


def test_install_dispatch_marks_the_process_as_authoritative(tmp_path):
    source = qbt_onbox_path().read_text()

    assert source.count('STATE_SAVE_POLICY["force"] = True') == 2
