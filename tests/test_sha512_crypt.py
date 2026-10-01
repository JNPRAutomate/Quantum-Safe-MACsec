import re
import shutil
import subprocess

import pytest

from lib.common.script_user_bootstrap import encrypted_junos_password, sha512_crypt


def test_sha512_crypt_matches_reference_vector():
    # Reference vector from the SHA-crypt specification (default 5000 rounds).
    assert sha512_crypt("Hello world!", "saltstring") == (
        "$6$saltstring$svn8UoSVapNtMuq1ukKS4tPQd8iKwSMHWjl/O817G3uBnIFNjnQJ"
        "uesI68u4OTLiBFdcbYEdFCoEOfaS35inz1"
    )


def test_sha512_crypt_truncates_salt_to_16_chars():
    long_salt = "abcdefghijklmnopqrstuvwxyz"
    assert sha512_crypt("pw", long_salt) == sha512_crypt("pw", long_salt[:16])


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not available")
@pytest.mark.parametrize("password", ["example-secret", "p@ss w0rd/ü", "x" * 100])
def test_sha512_crypt_matches_openssl(password):
    salt = "Qkd0Lab1Salt2345"
    result = subprocess.run(
        ["openssl", "passwd", "-6", "-salt", salt, "-stdin"],
        input=password + "\n",
        capture_output=True,
        text=True,
        check=True,
    )
    assert sha512_crypt(password, salt) == result.stdout.strip()


def test_encrypted_junos_password_format_and_random_salt():
    first = encrypted_junos_password("example-secret")
    second = encrypted_junos_password("example-secret")
    pattern = r"\$6\$[./0-9A-Za-z]{16}\$[./0-9A-Za-z]{86}"
    assert re.fullmatch(pattern, first)
    assert re.fullmatch(pattern, second)
    assert first != second
    salt = first.split("$")[2]
    assert sha512_crypt("example-secret", salt) == first
