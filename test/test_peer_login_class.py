from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
USERS_TEMPLATE = ROOT / "config" / "templates" / "common" / "users.j2"


def test_peer_login_class_limits_shell_access_to_qkd_transport_commands():
    template = USERS_TEMPLATE.read_text(encoding="utf-8")

    assert "permissions shell" in template
    assert "scp -(f|t) /var/tmp/qkd_peer_" in template
    assert "op qkd_onbox.py action status iface" in template
    assert "scp .*" not in template
    assert "install-key" not in template
    assert 'deny-commands "file .*"' in template