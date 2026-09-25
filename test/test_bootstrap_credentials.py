import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

import qkd_orchestrator


ORCHESTRATOR = Path(qkd_orchestrator.__file__)


ENVIRONMENT_VARIABLES = (
    "QKD_DEFAULT_USER",
    "QKD_DEFAULT_PASSWORD",
    "QKD_BOOTSTRAP_USER",
    "QKD_BOOTSTRAP_PASSWORD",
)


def clear_credential_environment(monkeypatch):
    for name in ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def test_bootstrap_credentials_prefer_environment(monkeypatch):
    monkeypatch.setenv("QKD_DEFAULT_USER", "env-default")
    monkeypatch.setenv("QKD_DEFAULT_PASSWORD", "env-default-password")
    monkeypatch.setenv("QKD_BOOTSTRAP_USER", "env-bootstrap")
    monkeypatch.setenv("QKD_BOOTSTRAP_PASSWORD", "env-bootstrap-password")

    credentials = qkd_orchestrator.resolve_interactive_bootstrap_credentials(
        {
            "secrets": {
                "bootstrap_user": "inventory-bootstrap",
                "bootstrap_password": "inventory-bootstrap-password",
            }
        },
        input_fn=lambda _: pytest.fail("username prompt was not expected"),
        password_fn=lambda _: pytest.fail("password prompt was not expected"),
        interactive=False,
    )

    assert credentials == ("env-bootstrap", "env-bootstrap-password")


def test_bootstrap_credentials_prompt_only_for_missing_password(monkeypatch):
    clear_credential_environment(monkeypatch)
    prompts = []

    credentials = qkd_orchestrator.resolve_interactive_bootstrap_credentials(
        {"secrets": {"bootstrap_user": "root"}},
        input_fn=lambda _: pytest.fail("username prompt was not expected"),
        password_fn=lambda prompt: prompts.append(prompt) or "bootstrap-secret",
        interactive=True,
    )

    assert credentials == ("root", "bootstrap-secret")
    assert prompts == ["Bootstrap password for root: "]


def test_bootstrap_credentials_fall_back_to_default_identity(monkeypatch):
    clear_credential_environment(monkeypatch)

    credentials = qkd_orchestrator.resolve_interactive_bootstrap_credentials(
        {
            "secrets": {
                "default_user": "labuser",
                "default_password": "default-secret",
            }
        },
        input_fn=lambda _: pytest.fail("username prompt was not expected"),
        password_fn=lambda _: pytest.fail("password prompt was not expected"),
        interactive=False,
    )

    assert credentials == ("labuser", "default-secret")


def test_bootstrap_credentials_prompt_for_both_missing_values(monkeypatch):
    clear_credential_environment(monkeypatch)

    credentials = qkd_orchestrator.resolve_interactive_bootstrap_credentials(
        {},
        input_fn=lambda _: "root",
        password_fn=lambda _: "bootstrap-secret",
        interactive=True,
    )

    assert credentials == ("root", "bootstrap-secret")


def test_bootstrap_credentials_fail_without_tty(monkeypatch):
    clear_credential_environment(monkeypatch)

    with pytest.raises(RuntimeError, match="no interactive terminal"):
        qkd_orchestrator.resolve_interactive_bootstrap_credentials(
            {},
            interactive=False,
        )


def test_bootstrap_dry_run_does_not_prompt(monkeypatch):
    clear_credential_environment(monkeypatch)
    calls = []
    monkeypatch.setattr(
        qkd_orchestrator,
        "load_runtime_devices",
        lambda: {},
    )
    monkeypatch.setattr(
        qkd_orchestrator,
        "load_inventory_base",
        lambda: {},
    )
    monkeypatch.setattr(
        qkd_orchestrator,
        "setup_logger",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        qkd_orchestrator,
        "bootstrap_script_users",
        lambda **kwargs: calls.append(kwargs) or ([], []),
    )
    monkeypatch.setattr(
        qkd_orchestrator,
        "resolve_interactive_bootstrap_credentials",
        lambda *_args, **_kwargs: pytest.fail("credential prompt was not expected"),
    )

    qkd_orchestrator.handle_bootstrap(
        SimpleNamespace(dry_run=True, verbose=0)
    )

    assert calls[0]["dry_run"] is True


def test_create_resolves_credentials_before_runtime_cleanup():
    tree = ast.parse(ORCHESTRATOR.read_text(encoding="utf-8"))
    handle_create = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "handle_create"
    )
    source = ast.get_source_segment(
        ORCHESTRATOR.read_text(encoding="utf-8"),
        handle_create,
    )

    assert source.index(
        "resolve_interactive_bootstrap_credentials(base)"
    ) < source.index("reset_local_runtime_for_create()")
