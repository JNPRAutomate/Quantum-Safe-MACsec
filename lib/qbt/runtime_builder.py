"""Build the single-file Junos QBT operation/event runtime."""

from pathlib import Path


BASE = Path(__file__).resolve().parents[2]
CORE = BASE / "artifacts/qkd_onbox.py"

TRANSPORT = r'''
def etsi_get(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in ("9.1.1.10", "9.1.1.11") or parsed.port != 443:
        raise ValueError("Unapproved local QBT ETSI endpoint")
    endpoint = parsed.path.rsplit("/", 1)[-1]
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    body = None
    method = "GET"
    headers = {}
    if endpoint == "enc_keys":
        query["number"] = ["1"]
        if "key_size" in query:
            query["size"] = query.pop("key_size")
    elif endpoint == "dec_keys":
        identifiers = query.pop("key_ID", [])
        if not identifiers:
            raise ValueError("QBT DEC request requires a Key-ID")
        body = json.dumps({"key_IDs": [{"key_ID": value} for value in identifiers]})
        method = "POST"
        headers["Content-Type"] = "application/json"
        query.pop("key_size", None)
    else:
        raise ValueError("Only ETSI enc_keys/dec_keys are allowed")
    control = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        control.settimeout(10)
        control.connect("/run/qbt-etsi/transport.sock")
        message, ancillary, flags, _address = control.recvmsg(
            256, socket.CMSG_SPACE(array.array("i").itemsize), socket.MSG_CMSG_CLOEXEC
        )
    finally:
        control.close()
    descriptors = array.array("i")
    for level, kind, data in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            descriptors.frombytes(data[:len(data) - len(data) % descriptors.itemsize])
    if message != b"OK" or flags & socket.MSG_CTRUNC or len(descriptors) != 1:
        for descriptor in descriptors:
            os.close(descriptor)
        raise RuntimeError("QBT ETSI socket helper rejected the connection")
    transport = socket.socket(fileno=descriptors[0])
    try:
        transport.settimeout(60)
        context = ssl.create_default_context(cafile=CA)
        context.load_cert_chain(CERT, KEY)
        secured = context.wrap_socket(transport, server_hostname=parsed.hostname)
    except BaseException:
        transport.close()
        raise
    connection = http.client.HTTPSConnection(parsed.hostname, timeout=60)
    connection.sock = secured
    path = parsed.path
    encoded_query = urllib.parse.urlencode(query, doseq=True)
    if encoded_query:
        path += "?" + encoded_query
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = requests.Response()
        result.status_code = response.status
        result.reason = response.reason
        result._content = response.read()
        result.url = url
        result.headers.update(response.getheaders())
        return result
    finally:
        connection.close()
'''


def build_qbt_onbox(output=None):
    source = CORE.read_text(encoding="utf-8")
    source = source.replace("qkd_onbox.py", "qbt_onbox.py")
    source = source.replace("qkd_onbox_config.json", "qbt_onbox_config.json")
    source = source.replace("qkd_onbox_inventory.json", "qbt_onbox_inventory.json")
    source_version = 'EARLY_SCRIPT_VERSION = "ver3.3.4.1"'
    if source.count(source_version) != 1:
        raise ValueError("Unexpected shared on-box runtime version layout")
    source = source.replace(
        source_version,
        'EARLY_SCRIPT_VERSION = "qbt_ver1.0"',
        1,
    )
    cak_validation = """        # Junos can surface the CAK name in different normalized hex lengths
        # depending on platform/output format. Accept the observed 32/64-char
        # forms and only warn on truly unexpected lengths.
        if len(cak_name) not in (32, 64):
"""
    qbt_cak_validation = """        # EVO Junos may display the CAK name as a shortened 62-character token.
        # MKA confirmation still requires a matching configured CKN prefix or suffix.
        if len(cak_name) not in (32, 62, 64):
"""
    if source.count(cak_validation) != 1:
        raise ValueError("Unexpected shared runtime CAK validation layout")
    source = source.replace(cak_validation, qbt_cak_validation, 1)
    if source.count("qkd_debug_") != 1:
        raise ValueError("Unexpected shared on-box runtime debug log layout")
    source = source.replace("qkd_debug_", "qbt_debug_", 1)
    imports = (
        "import requests\n"
        "import array\n"
        "import http.client\n"
        "import socket\n"
        "import ssl\n"
        "import urllib.parse\n"
    )
    if source.count("import requests\n") != 1:
        raise ValueError("Unexpected shared on-box runtime import layout")
    source = source.replace("import requests\n", imports, 1)
    old_transport = '''def etsi_get(url):
    if CONFIG.get("etsi_transport") == "qbt-local-socket":
        from qbt_etsi_client import get
        return get(url, cert=(CERT, KEY), verify=CA, timeout=60)
    return requests.get(url, cert=(CERT, KEY), verify=CA, timeout=5)
'''
    if source.count(old_transport) != 1:
        raise ValueError("Unexpected shared on-box ETSI transport layout")
    source = source.replace(old_transport, TRANSPORT.lstrip(), 1)
    if "from qbt_etsi_client import" in source or "qbt_runtime_core.py" in source:
        raise ValueError("QBT runtime must be a single self-contained script")
    source = "\n".join(line.rstrip(" \t") for line in source.splitlines()) + "\n"
    destination = Path(output) if output else BASE / "artifacts/qbt_onbox.py"
    destination.write_text(source, encoding="utf-8")
    destination.chmod(0o755)
    return destination
