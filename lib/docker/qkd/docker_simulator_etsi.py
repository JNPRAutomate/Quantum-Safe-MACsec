"""Pure conformance check for observed PhioTX key_fetch ETSI014 responses.

This does not implement a KME or prove QKD provenance. It validates the exact
paired response requirement for a future vendor adapter integration test:
GET /api/v1/keys/<slave_sae>/enc_keys?number=1&size=256 on the master,
GET /api/v1/keys/<master_sae>/dec_keys?key_ID=<returned_id> on the slave,
using the respective registered SAE mTLS identities. Both must return HTTP 200
and {"keys": [{"key_ID": "...", "key": "<base64 32 bytes>"}]}.
No network requests, SQL schema or random-key substitutes are supplied here.
"""

import base64
import binascii
import hmac


def validate_correlated_keys(enc_status, enc_response, dec_status, dec_response):
    """Require one matching 256-bit key and ID without returning/logging key bytes."""
    if type(enc_status) is not int or type(dec_status) is not int or (enc_status, dec_status) != (200, 200):
        raise ValueError("Paired ETSI014 fetches must both return HTTP 200")
    observed = []
    for response in (enc_response, dec_response):
        if not isinstance(response, dict) or not isinstance(response.get("keys"), list):
            raise ValueError("ETSI014 response must contain a keys list")
        keys = response["keys"]
        if len(keys) != 1 or not isinstance(keys[0], dict):
            raise ValueError("ETSI014 response must contain exactly one key object")
        key_id, content = keys[0].get("key_ID"), keys[0].get("key")
        if not isinstance(key_id, str) or not key_id.strip():
            raise ValueError("ETSI014 key_ID must be a nonempty string")
        if not isinstance(content, str):
            raise ValueError("ETSI014 key must be base64 text")
        try:
            decoded = base64.b64decode(content, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("ETSI014 key must be valid base64 text") from error
        if len(decoded) != 32:
            raise ValueError("PhioTX key_fetch requires exactly 256 bits")
        observed.append((key_id, decoded))
    if observed[0][0] != observed[1][0] or not hmac.compare_digest(observed[0][1], observed[1][1]):
        raise ValueError("ETSI014 master/slave key IDs and bytes are not correlated")
    return observed[0][0]
