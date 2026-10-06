"""Repeatable paired four-key ETSI acceptance, keeping key bytes out of logs."""

import base64
import json
from pathlib import Path
import shlex
import tempfile
import uuid

from scp import SCPClient


PROBE_SCRIPT = Path(__file__).resolve().parents[2] / "artifacts/qbt_etsi_probe.py"


def paired_probe(clients, run, transfer):
    if set(clients) != {"EVO1", "EVO2"}:
        raise ValueError("Paired acceptance requires both EVOs")
    root = "/var/db/qbt-etsi/client"
    # The probe client is installed here on every run: its script and its own
    # copy of the CA and SAE identity, taken from the certificates deployed for
    # the runtime, so a clean router works and a PKI rotation is never tested
    # against the previous PKI.
    for name, sae in (("EVO1", "sae-001"), ("EVO2", "sae-002")):
        certs = "/var/db/scripts/certs"
        run(
            clients[name],
            "set -eu; test -d /var/db/qbt-etsi; "
            f"mkdir -p {root}; chown etsi_user {root}; chmod 700 {root}",
        )
        transfer(clients[name], PROBE_SCRIPT, f"{root}/probe.py")
        run(
            clients[name],
            "set -eu; "
            f"chown etsi_user {root}/probe.py; chmod 600 {root}/probe.py; "
            f"cat {certs}/qbt-ca.pem > {root}/ca.pem; "
            f"cat {certs}/{sae}.crt > {root}/sae.pem; "
            f"cat {certs}/{sae}.key > {root}/sae.key; "
            f"chown etsi_user {root}/ca.pem {root}/sae.pem {root}/sae.key; "
            f"chmod 600 {root}/ca.pem {root}/sae.pem {root}/sae.key",
        )
    suffix = uuid.uuid4().hex
    enc_path = root + "/enc-" + suffix + ".json"
    dec_path = root + "/dec-" + suffix + ".json"
    ids_path = root + "/ids-" + suffix + ".json"
    def command(address, sae, output, ids=None):
        args = [
            "/usr/bin/python3", root + "/probe.py", "--address", address,
            "--certs", root, "--peer-sae", sae, "--output", output,
        ]
        if ids:
            args.extend(["--ids", ids])
        return shlex.join(["su", "-s", "/bin/sh", "etsi_user", "-c", shlex.join(args)])
    try:
        with tempfile.TemporaryDirectory(prefix="qbt-paired-") as folder:
            folder = Path(folder)
            print(run(clients["EVO1"], command("9.1.1.10", "sae-002", enc_path), timeout=120))
            with SCPClient(clients["EVO1"].get_transport()) as scp:
                scp.get(enc_path, str(folder / "enc.json"))
            enc = json.loads((folder / "enc.json").read_text())
            ids = [item["key_ID"] for item in enc["keys"]]
            (folder / "ids.json").write_text(json.dumps(ids))
            transfer(clients["EVO2"], folder / "ids.json", ids_path)
            run(clients["EVO2"], "chown etsi_user " + ids_path)
            print(run(clients["EVO2"], command("9.1.1.11", "sae-001", dec_path, ids_path), timeout=120))
            with SCPClient(clients["EVO2"].get_transport()) as scp:
                scp.get(dec_path, str(folder / "dec.json"))
            dec = json.loads((folder / "dec.json").read_text())
            left = {item["key_ID"]: base64.b64decode(item["key"], validate=True) for item in enc["keys"]}
            right = {item["key_ID"]: base64.b64decode(item["key"], validate=True) for item in dec["keys"]}
            if len(left) != 4 or left != right or any(len(value) != 32 for value in left.values()):
                raise ValueError("Four-key paired acceptance failed")
            print("[PASS] Four distinct 256-bit keys match across EVO1/EVO2; key bytes withheld")
    finally:
        run(clients["EVO1"], "rm -f " + enc_path)
        run(clients["EVO2"], "rm -f " + dec_path + " " + ids_path)
