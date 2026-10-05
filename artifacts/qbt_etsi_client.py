"""Requests-compatible local QBT GET transport for the existing rolling runtime."""

import http.client
import urllib.parse

import requests

from qbt_etsi_probe import connect_tls


def get(url, *, cert, verify, timeout):
    target = urllib.parse.urlsplit(url)
    if target.scheme != "https" or target.hostname not in ("9.1.1.10", "9.1.1.11") or target.port != 443:
        raise ValueError("Unapproved QBT ETSI endpoint")
    if not isinstance(verify, str) or not isinstance(cert, tuple):
        raise ValueError("Explicit CA and client certificate required")
    query = urllib.parse.parse_qs(target.query)
    if "key_size" in query:
        query["size"] = query.pop("key_size")
    connection = http.client.HTTPSConnection(target.hostname, timeout=timeout)
    connection.sock = connect_tls(target.hostname, verify, cert[0], cert[1])
    connection.sock.settimeout(timeout)
    try:
        connection.request("GET", target.path + "?" + urllib.parse.urlencode(query, doseq=True))
        response = connection.getresponse()
        result = requests.Response()
        result.status_code = response.status
        result._content = response.read()
        result.url = url
        result.headers.update(dict(response.getheaders()))
        return result
    finally:
        connection.close()
