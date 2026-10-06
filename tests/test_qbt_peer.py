from lib.qbt.peer import configure_peers, has_ake_key_pairs

PROVISIONED = """\
AKE Keys Summary
\u250c\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510
\u2502 Key Type            \u2502 Key Id \u2502 Key Fingerprint \u2502 Detail \u2502
\u2502 CRYSPEN_ML_KEM_1024 \u2502 31fdcae3 \u2502 S256B64:abc= \u2502 Public Key of qbt-evo2[7ef5] \u2502
\u2502 CRYSPEN_ML_KEM_1024 \u2502 b618e3c5 \u2502 --- \u2502 Self Key Pair \u2502
\u2502 ECDSA_P384          \u2502 cf4f816b \u2502 S256B64:def= \u2502 Public Key of qbt-evo2[7ef5] \u2502
\u2502 ECDSA_P384          \u2502 f1242a8f \u2502 --- \u2502 Self Key Pair \u2502
"""


def test_a_provisioned_kme_is_recognised_from_the_reported_key_types():
    assert has_ake_key_pairs(PROVISIONED)


def test_missing_or_partial_self_key_pairs_are_not_accepted():
    only_one = "\n".join(
        line for line in PROVISIONED.splitlines() if "CRYSPEN" not in line
    )
    only_peer_keys = "\n".join(
        line for line in PROVISIONED.splitlines() if "Self Key Pair" not in line
    )

    assert not has_ake_key_pairs(only_one)
    assert not has_ake_key_pairs(only_peer_keys)
    assert not has_ake_key_pairs("No keys found")


def test_rerun_on_a_provisioned_kme_does_not_generate_new_key_pairs():
    commands = []

    class Client:
        pass

    def run(_client, command, **_kwargs):
        commands.append(command)
        if "list-keys" in command:
            return PROVISIONED
        if "export-public-keys" in command or "cat /tmp/qbt-public-keys.json" in command:
            return "{}"
        if "list-peers" in command:
            return "qbt-evo1 qbt-evo2 https://10.38.112.10:4004 https://10.38.112.11:4004"
        if "list-saes" in command:
            return "sae-001 sae-002"
        return ""

    configure_peers({"EVO1": Client(), "EVO2": Client()}, run)

    assert not any("generate-key-pairs" in command for command in commands)
    assert not any("add-peer-id-string" in command for command in commands)


def test_rerun_asserts_the_peer_url_instead_of_comparing_it():
    commands = []

    def run(_client, command, **_kwargs):
        commands.append(command)
        if "list-keys" in command:
            return PROVISIONED
        if "list-peers" in command:
            return "qbt-evo1 qbt-evo2"
        if "list-saes" in command:
            return "sae-001 sae-002"
        if "cat /tmp/qbt-public-keys.json" in command:
            return "{}"
        return ""

    configure_peers({"EVO1": object(), "EVO2": object()}, run)

    updates = [command for command in commands if "update-peer" in command]
    assert any("qbt-evo2 -u https://10.38.112.11:4004" in command for command in updates)
    assert any("qbt-evo1 -u https://10.38.112.10:4004" in command for command in updates)
