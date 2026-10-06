#!/usr/bin/env python3
"""Authenticated ETSI requests over the endpoint-limited local socket helper."""

import argparse
import array
import base64
import http.client
import json
import os
from pathlib import Path
import socket
import ssl
import urllib.parse


def request(address, certs, sae, operation, ids=None):
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as control:
        control.settimeout(10)
        control.connect("/run/qbt-etsi/transport.sock")
        descriptors = array.array("i")
        message, ancillary, flags, _ = control.recvmsg(
            256, socket.CMSG_SPACE(descriptors.itemsize), socket.MSG_CMSG_CLOEXEC
        )
        for level, kind, data in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                descriptors.frombytes(data[:len(data) - len(data) % descriptors.itemsize])
        if message != b"OK" or flags & socket.MSG_CTRUNC or len(descriptors) != 1:
            for descriptor in descriptors:
                os.close(descriptor)
            raise RuntimeError("Socket helper failed: " + message.decode(errors="replace"))
    transport = socket.socket(fileno=descriptors[0])
    try:
        transport.settimeout(60)
        context = ssl.create_default_context(cafile=str(certs / "ca.pem"))
        context.load_cert_chain(str(certs / "sae.pem"), str(certs / "sae.key"))
        secured = context.wrap_socket(transport, server_hostname=address)
    except BaseException:
        transport.close()
        raise
    connection = http.client.HTTPSConnection(address, timeout=60)
    connection.sock = secured
    query = urllib.parse.urlencode({"number": 4, "size": 256})
    body = None
    method = "GET"
    if ids is not None:
        method = "POST"
        query = ""
        body = json.dumps({"key_IDs": [{"key_ID": value} for value in ids]})
    url = "/api/v1/keys/" + urllib.parse.quote(sae, safe="") + "/" + operation
    if query:
        url += "?" + query
    try:
        connection.request(method, url, body, {"Content-Type": "application/json"})
        response = connection.getresponse()
        data = response.read()
        if response.status != 200:
            raise RuntimeError(f"ETSI HTTP {response.status}: {data[:1000].decode(errors='replace')}")
        result = json.loads(data)
    finally:
        connection.close()
    keys = result.get("keys")
    if not isinstance(keys, list) or len(keys) != 4:
        raise ValueError("Expected exactly four keys")
    identifiers = []
    for item in keys:
        identifiers.append(item["key_ID"])
        if len(base64.b64decode(item["key"], validate=True)) != 32:
            raise ValueError("Expected 32-byte key")
    if len(set(identifiers)) != 4 or (ids is not None and set(ids) != set(identifiers)):
        raise ValueError("Key-ID batch mismatch")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", required=True)
    parser.add_argument("--certs", type=Path, required=True)
    parser.add_argument("--peer-sae", required=True)
    parser.add_argument("--ids", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    ids = json.loads(args.ids.read_text()) if args.ids else None
    result = request(args.address, args.certs, args.peer_sae, "dec_keys" if ids else "enc_keys", ids)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(result, stream)
    print("ETSI batch accepted: four distinct Key-IDs, each 32 bytes; bytes withheld")


if __name__ == "__main__":
    main()
