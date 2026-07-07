"""
Runtime identity and path helpers.
"""

BASE_DIR = "/var/home/admin"


def get_base_dir():
    return BASE_DIR


def get_certs_dir():
    return f"{BASE_DIR}/certs/"


def get_log_file():
    return f"{BASE_DIR}/qkd_test.log"


def get_keyid_file(device_name):
    return f"{BASE_DIR}/{device_name}last_key.json"

