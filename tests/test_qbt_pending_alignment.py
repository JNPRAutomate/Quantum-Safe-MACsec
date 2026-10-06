"""QBT runtime: master and peer must retain the same pending queue after a rolling batch."""

import ast
from pathlib import Path
from types import SimpleNamespace

from lib.qbt.runtime_builder import qbt_onbox_path

ROOT = Path(__file__).resolve().parents[1]
NAMES = {
    "_finalize_bilateral_install", "purge_pending_in_replaced_slots",
    "purge_pending_older_than_start_time",
}


def start_epoch(value):
    return {"t3": 300, "t0": 400, "t1": 500}.get(value)


def load(namespace):
    source = qbt_onbox_path().read_text()
    tree = ast.parse(source)
    selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in NAMES]
    exec(compile(ast.Module(body=selected, type_ignores=[]), "runtime", "exec"), namespace)
    return namespace


def pending_state():
    return {
        "pending_keys": [
            {"generation": 11, "key_id": "surviving", "start_time": "t3", "slot": 3},
        ],
        "installed_keys": [{"slot": slot} for slot in range(4)],
        "inflight_install": {"ack_id": "ack"},
    }


def records():
    return [
        {"generation": 12, "key_id": "new-0", "start_time": "t0", "slot": 0},
        {"generation": 13, "key_id": "new-1", "start_time": "t1", "slot": 1},
    ]


def namespace():
    return load({
        "epoch_from_junos_start_time": start_epoch,
        "normalize_pending_keys": lambda state: state,
        "sync_pending_legacy_fields": lambda state: state,
        "find_slot_for_key_id_in_installed": lambda *_args: None,
        "log": lambda *_args, **_kwargs: None,
        "append_pending_key": lambda state, generation, key_id, start_time, slot=None: {
            **state,
            "pending_keys": state["pending_keys"] + [{
                "generation": generation, "key_id": key_id,
                "start_time": start_time, "slot": slot,
            }],
        },
        "record_installed_key": lambda state, *_args, **_kwargs: state,
        "max_installed_keys": lambda: 4,
        "time": SimpleNamespace(time=lambda: 123),
    })


def test_master_keeps_future_pending_key_in_untouched_slot():
    runtime = namespace()
    result = runtime["_finalize_bilateral_install"](
        pending_state(), records(), "ROLLING_REPLACEMENT"
    )
    assert [item["key_id"] for item in result["pending_keys"]] == [
        "surviving", "new-0", "new-1",
    ]


def test_master_and_peer_purge_agree_for_replaced_slots():
    runtime = namespace()
    state = pending_state()
    state["pending_keys"].append(
        {"generation": 10, "key_id": "replaced", "start_time": "t0", "slot": 0}
    )
    master = runtime["_finalize_bilateral_install"](dict(state), records(), "ROLLING_REPLACEMENT")
    peer = runtime["purge_pending_in_replaced_slots"](dict(state), records())
    assert [item["key_id"] for item in master["pending_keys"][:1]] == [
        item["key_id"] for item in peer["pending_keys"]
    ] == ["surviving"]
    assert "replaced" not in [item["key_id"] for item in master["pending_keys"]]
