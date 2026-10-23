import pytest
import kme_orchestrator

from lib.kme import install_host


def prepare_host(monkeypatch):
    monkeypatch.setattr(install_host, "load_yaml", lambda _path: {})
    monkeypatch.setattr(install_host, "load_state_for_install_host", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(install_host, "resolve_os_family", lambda *_args: "ubuntu")
    monkeypatch.setattr(
        install_host,
        "install_host_ubuntu",
        lambda *_args, **_kwargs: pytest.fail("must not install packages in verify-only mode"),
    )


def test_verify_only_records_success_without_installing(monkeypatch):
    prepare_host(monkeypatch)
    writes = []
    monkeypatch.setattr(install_host, "verify_installation", lambda *_args, **_kwargs: {"docker_access": True})
    monkeypatch.setattr(install_host, "write_install_host_state", lambda **kwargs: writes.append(kwargs))

    result = install_host.run_install_host("lab.yaml", verify_only=True)

    assert result["verification"] == {"docker_access": True}
    assert len(writes) == 1
    assert writes[0]["verification"]["docker_access"] is True


def test_verify_only_failure_does_not_record_success(monkeypatch):
    prepare_host(monkeypatch)

    def fail_verification(*_args, **_kwargs):
        raise RuntimeError("Docker daemon access denied")

    monkeypatch.setattr(install_host, "verify_installation", fail_verification)
    monkeypatch.setattr(
        install_host,
        "write_install_host_state",
        lambda **_kwargs: pytest.fail("must not record success after failed verification"),
    )

    with pytest.raises(RuntimeError, match="access denied"):
        install_host.run_install_host("lab.yaml", verify_only=True)


def test_verification_checks_docker_daemon_access(monkeypatch):
    commands = []
    monkeypatch.setattr(
        install_host,
        "verify_remote_tool",
        lambda _config, _name, command, **_kwargs: commands.append(command) or True,
    )

    result = install_host.verify_installation({})

    assert "docker info --format '{{.ServerVersion}}'" in commands
    assert result["docker_access"] is True


@pytest.mark.parametrize("verification_fails", [False, True])
def test_create_verifies_skipped_install_before_build_env(monkeypatch, verification_fails):
    calls = []

    def fake_load(module, symbol):
        def step(**kwargs):
            calls.append((symbol, kwargs))
            if symbol == "run_install_host" and verification_fails:
                raise RuntimeError("host verification failed")
        return step

    monkeypatch.setattr(kme_orchestrator, "load_symbol", fake_load)
    args = kme_orchestrator.build_parser().parse_args([
        "create", "--skip-bootstrap", "--skip-install-host", "--skip-build-image",
        "--skip-cert-install", "--skip-db-init", "--no-deploy",
    ])
    if verification_fails:
        with pytest.raises(RuntimeError, match="host verification failed"):
            kme_orchestrator.cmd_create(args)
        assert [name for name, _kwargs in calls] == ["run_install_host"]
    else:
        kme_orchestrator.cmd_create(args)
        assert [name for name, _kwargs in calls] == ["run_install_host", "run_build_env"]
    assert calls[0][1]["verify_only"] is True


def test_install_host_parser_accepts_verify_only():
    args = kme_orchestrator.build_parser().parse_args(["install-host", "--verify-only"])
    assert args.verify_only is True