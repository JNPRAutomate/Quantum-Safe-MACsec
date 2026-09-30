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
    "QKD_UPLOAD_USER",
    "QKD_UPLOAD_PASSWORD",
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


def test_bootstrap_user_override_precedes_environment_and_inventory(monkeypatch):
    monkeypatch.setenv("QKD_BOOTSTRAP_USER", "env-bootstrap")
    monkeypatch.delenv("QKD_BOOTSTRAP_PASSWORD", raising=False)
    prompts = []

    credentials = qkd_orchestrator.resolve_interactive_bootstrap_credentials(
        {
            "secrets": {
                "bootstrap_user": "inventory-bootstrap",
            }
        },
        bootstrap_user_override="labuser",
        input_fn=lambda _: pytest.fail("username prompt was not expected"),
        password_fn=lambda prompt: prompts.append(prompt) or "lab-secret",
        interactive=True,
    )

    assert credentials == ("labuser", "lab-secret")
    assert prompts == ["Bootstrap password for labuser: "]


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


def test_upload_credentials_reuse_bootstrap_identity(monkeypatch):
    clear_credential_environment(monkeypatch)

    credentials = qkd_orchestrator.resolve_interactive_upload_credentials(
        "root",
        bootstrap_user="root",
        bootstrap_password="bootstrap-secret",
        password_fn=lambda _: pytest.fail("upload prompt was not expected"),
        interactive=False,
    )

    assert credentials == ("root", "bootstrap-secret")


def test_upload_credentials_prompt_separately_for_different_user(monkeypatch):
    clear_credential_environment(monkeypatch)
    prompts = []

    credentials = qkd_orchestrator.resolve_interactive_upload_credentials(
        "labuser",
        bootstrap_user="root",
        bootstrap_password="bootstrap-secret",
        password_fn=lambda prompt: prompts.append(prompt) or "upload-secret",
        interactive=True,
    )

    assert credentials == ("labuser", "upload-secret")
    assert prompts == ["Upload password for labuser: "]


def test_upload_credentials_fail_without_tty(monkeypatch):
    clear_credential_environment(monkeypatch)

    with pytest.raises(RuntimeError, match="Missing upload password"):
        qkd_orchestrator.resolve_interactive_upload_credentials(
            "labuser",
            bootstrap_user="root",
            bootstrap_password="bootstrap-secret",
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


def function_source(name):
    source_text = ORCHESTRATOR.read_text(encoding="utf-8")
    tree = ast.parse(source_text)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return ast.get_source_segment(source_text, function)


def test_remote_clean_resolves_interactive_credentials(monkeypatch):
    clear_credential_environment(monkeypatch)
    received = {}
    monkeypatch.setattr(
        qkd_orchestrator, "load_inventory_base", lambda: {"secrets": {}}
    )
    monkeypatch.setattr(
        qkd_orchestrator,
        "resolve_interactive_bootstrap_credentials",
        lambda _base, bootstrap_user_override=None: (
            bootstrap_user_override or "root",
            "prompted",
        ),
    )
    monkeypatch.setattr(
        qkd_orchestrator,
        "handle_clean",
        lambda args: received.update(vars(args)),
    )
    monkeypatch.setattr(
        qkd_orchestrator.sys,
        "argv",
        ["qkd_orchestrator.py", "clean", "--bootstrap-user", "admin"],
    )

    qkd_orchestrator.main()

    assert received["clean_user"] == "admin"
    assert received["clean_password"] == "prompted"


def test_local_only_clean_never_prompts(monkeypatch):
    received = {}
    monkeypatch.setattr(
        qkd_orchestrator,
        "resolve_interactive_bootstrap_credentials",
        lambda *_args, **_kwargs: pytest.fail("local clean must not prompt"),
    )
    monkeypatch.setattr(
        qkd_orchestrator,
        "handle_clean",
        lambda args: received.update(vars(args)),
    )
    monkeypatch.setattr(
        qkd_orchestrator.sys,
        "argv",
        ["qkd_orchestrator.py", "clean", "--local-only"],
    )

    qkd_orchestrator.main()

    assert received["local_only"] is True
    assert "clean_password" not in received


def test_deploy_prompts_after_dry_run_and_preview_return():
    source = function_source("handle_deploy")
    dry_run_return = source.index(
        'print_step_banner("0/5", "PREVIEW OR DRY-RUN", "END")'
    )
    prompt = source.index(
        "resolve_interactive_bootstrap_credentials("
    )

    assert dry_run_return < prompt
    assert "bootstrap_user_override=bootstrap_user" in source[prompt:]


def test_deploy_onbox_accepts_prompted_bootstrap_credentials(monkeypatch):
    clear_credential_environment(monkeypatch)
    monkeypatch.setattr(qkd_orchestrator, "load_inventory_base", lambda: {})

    qkd_orchestrator.deploy_onbox(
        log=None,
        devices={},
        artifacts={},
        bootstrap_user="root",
        bootstrap_password="prompted-secret",
    )


def test_deploy_passes_prompted_bootstrap_credentials_to_onbox():
    source = function_source("handle_deploy")
    call_start = source.index("deploy_onbox(")
    call_end = source.index(")", call_start)
    deploy_call = source[call_start:call_end]

    assert "bootstrap_user=bootstrap_user" in deploy_call
    assert "bootstrap_password=bootstrap_password" in deploy_call
    assert "upload_user=upload_user" in deploy_call
    assert "upload_password=upload_password" in deploy_call


def test_deploy_onbox_separates_upload_and_install_sessions(
    monkeypatch,
    tmp_path,
):
    clear_credential_environment(monkeypatch)
    monkeypatch.setattr(qkd_orchestrator, "load_inventory_base", lambda: {})
    opened_users = []
    upload_users = []
    shell_users = []

    class Response:
        def __init__(self, text):
            self._text = text

        def itertext(self):
            return iter([self._text])

    class Rpc:
        def __init__(self, user):
            self.user = user

        def cli(self, _command, format=None):
            return Response("Routing Engine 0")

        def request_shell_execute(self, command):
            shell_users.append(self.user)
            return Response("__QKD_ONBOX_INSTALL_OK__")

    class Device:
        def __init__(self, *, host, user, passwd, port, gather_facts):
            self.user = user
            self.rpc = Rpc(user)

        def open(self):
            opened_users.append(self.user)

        def close(self):
            pass

    class Scp:
        def __init__(self, dev):
            self.dev = dev

        def __enter__(self):
            upload_users.append(self.dev.user)
            return self

        def __exit__(self, *_args):
            return False

        def put(self, _source, remote_path):
            pass

    class Log:
        def info(self, _message):
            pass

        def debug(self, _message):
            pass

        def error(self, _message):
            pass

    script = tmp_path / "qkd_onbox.py"
    script.write_text("# test\n", encoding="utf-8")
    monkeypatch.setattr(qkd_orchestrator, "Device", Device)
    monkeypatch.setattr(qkd_orchestrator, "SCP", Scp)

    qkd_orchestrator.deploy_onbox(
        log=Log(),
        devices={"EVO1": {"ip": "192.0.2.1", "hostname": "evo1"}},
        artifacts={"EVO1": {"script": script}},
        bootstrap_user="root",
        bootstrap_password="root-secret",
        upload_user="labuser",
        upload_password="lab-secret",
    )

    assert opened_users == ["labuser", "root"]
    assert upload_users == ["labuser"]
    assert shell_users == ["root"]


def test_validate_uses_interactive_fallback_when_all_passwords_are_missing():
    source = function_source("handle_validate")

    assert "not (bootstrap_user and bootstrap_password)" in source
    assert "and not script_password" in source
    assert "resolve_interactive_bootstrap_credentials(inventory_base)" in source
