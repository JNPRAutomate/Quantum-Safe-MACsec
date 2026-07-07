import requests

r = requests.get(
    "https://10.123.252.11/api/v1/keys/test/enc_keys",
    verify="/var/home/admin/certs/client-root-ca.crt",
    cert=(
        "/var/home/admin/certs/acx7332-p1.crt",
        "/var/home/admin/certs/acx7332-p1.key"
    )
)

print(r.status_code)
print(r.text)