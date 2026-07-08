try:
    import jcs
    ONBOX = True
except ImportError:
    ONBOX = False

CUR_DIR = "/var/home/admin"

CERTS_DIR = f"{CUR_DIR}/certs/"
OFFBOX_CERTS_DIR = "./certs/"

KEYID_JSON_FILENAME = f"{CUR_DIR}/{{}}last_key.json"

CKN_PREFIX = "abcd1234abcd5678abcd1234abcd5678"

LOG_FILENAME = f"{CUR_DIR}/qkd.log"