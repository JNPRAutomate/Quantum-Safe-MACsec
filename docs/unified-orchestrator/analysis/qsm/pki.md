# PKI generation, deployment and inspection

[Index](README.md) · [KME installation](kme_orchestrator.md) · [Schemas](inventories.md)

## Ownership and command boundaries

QKD create owns local PKI generation. KME install-certs consumes generated material; tools/cert_manager inspects it but does not generate/renew it. There is no dedicated PKI-container lifecycle in these entry points. Sources: `qkd_orchestrator.py:1325`, `qkd_orchestrator.py:1353`, `lib/kme/cert_install.py:522`, `tools/cert_manager.py:484`.

```text
python3 qkd_orchestrator.py create --inventory NAME --pki-profile self_signed
python3 qkd_orchestrator.py create --inventory NAME --pki-profile hierarchical_ca
python3 -m lib.qkd.pki_hierarchical [--config PATH] [--clean] [--verify]
python3 kme_orchestrator.py install-certs [--config PATH] [--skip-cert-san-validation] [--dry-run]
python3 tools/cert_manager.py INPUT [INPUT ...] [-r] [--json] [--password-prompt] [--strict]
```

Sources: `qkd_orchestrator.py:301`, `qkd_orchestrator.py:314`, `lib/qkd/pki_hierarchical.py:880`, `kme_orchestrator.py:402`, `tools/cert_manager.py:444`.

## Self-signed profile: actual implementation, not YAML intent

The self-signed builder reads global `CONFIG`/`PKI` constants, supplied device records and its arguments. It does **not** load `config/pki/self_signed.yml` or profile YAML files in the normal QKD create path. Therefore the YAML's declared 3650-day root/profile/safety settings are not automatically honored by this builder. Sources: `qkd_orchestrator.py:1356`, `lib/qkd/pki_self_signed.py:289`, `lib/qkd/pki_self_signed.py:1`, `config/pki/self_signed.yml:19`.

Ordered generation:

1. Ensure `certs/self_signed`; create fresh root certificate/private key with configured country/org/RSA key size/validity. Sources: `lib/qkd/pki_self_signed.py:312`, `lib/qkd/pki_self_signed.py:29`, `lib/common/settings.py:29`.
2. For every supplied device, resolve SAE ID/IP; create `<SAE>/<SAE>.crt`, `.key`, `.pem`, with combined PEM holding private key and certificate. Sources: `lib/qkd/pki_self_signed.py:324`, `lib/qkd/pki_self_signed.py:209`.
3. Build unique KME IP inventory, assign sequential KME names, generate KME `.crt`, `.key`, `.pem` under `kme/`. No KME endpoints is an error. Source: `lib/qkd/pki_self_signed.py:246`, `lib/qkd/pki_self_signed.py:366`.
4. Leaves share the same builder, include client/server EKU and SAN, are signed with SHA-256 and generated RSA 4096 by current settings. TLS server hostname/IP verification depends on correct KME SAN. Sources: `lib/qkd/pki_self_signed.py:84`, `lib/qkd/pki_self_signed.py:110`, `lib/qkd/pki_self_signed.py:174`, `lib/common/settings.py:35`.

Default names `offbox_rootCA.crt`/`.key`, SAE `sae-` with pad 3 and KME `kme-` with pad 3 come from settings. Private keys are serialized unencrypted; combined PEMs are equally sensitive. Sources: `lib/common/settings.py:41`, `lib/common/settings.py:44`, `lib/common/settings.py:49`, `lib/qkd/pki_self_signed.py:71`, `lib/qkd/pki_self_signed.py:196`, `lib/qkd/pki_self_signed.py:209`.

QKD create's marker/signature prevents rebuilding an unchanged signature, but the builder itself is not an existing-file-preserving CA service: invoking generation on a changed signature can replace trust material without a coordinated rolling trust exchange. Sources: `qkd_orchestrator.py:1325`, `qkd_orchestrator.py:1350`, `lib/qkd/pki_self_signed.py:318`.

## Hierarchical profile

Default config is `config/pki/hierarchical_ca.yml`; it defines **two independent domains**, KME and Juniper, each root → issuing CA → leaf, plus opposite-domain trust exchange. Sources: `lib/qkd/pki_hierarchical.py:793`, `config/pki/hierarchical_ca.yml:24`, `config/pki/hierarchical_ca.yml:44`, `config/pki/hierarchical_ca.yml:77`.

### Standalone CLI knobs

- `--config PATH`: default hierarchical config constant. Determines output directory, device source, domains, profiles, layout, crypto and validity. Source: `lib/qkd/pki_hierarchical.py:885`.
- `--clean`: false; removes configured output directory recursively **before** rebuilding. This is unrelated to QKD remote clean. Sources: `lib/qkd/pki_hierarchical.py:891`, `lib/qkd/pki_hierarchical.py:799`.
- `--verify`: false; after generation, verify both certificate trees' signatures. Does not test deployed KME/router TLS connectivity. Sources: `lib/qkd/pki_hierarchical.py:897`, `lib/qkd/pki_hierarchical.py:857`.

### Ordered implementation

1. Load config, select output, optionally remove it, load all profile files. Sources: `lib/qkd/pki_hierarchical.py:793`, `lib/qkd/pki_hierarchical.py:771`.
2. Require `pki.devices_source`, load runtime devices, derive unique KME leaf endpoints and Juniper SAE identities. Sources: `lib/qkd/pki_hierarchical.py:805`, `lib/qkd/pki_hierarchical.py:237`, `lib/qkd/pki_hierarchical.py:269`.
3. Build KME domain, then Juniper domain. Per domain: derive paths; load CA/leaf profiles; select key size/hash; create root and issuing CA; issue leaves and chain/PEM files. Sources: `lib/qkd/pki_hierarchical.py:598`, `lib/qkd/pki_hierarchical.py:639`, `lib/qkd/pki_hierarchical.py:827`, `lib/qkd/pki_hierarchical.py:839`.
4. Build trust exchange: KME receives Juniper issuing/root bundle according to include toggles; routers receive KME issuing/root bundle. Sources: `lib/qkd/pki_hierarchical.py:720`, `lib/qkd/pki_hierarchical.py:851`.
5. Optional verify both trees, print output directory. Source: `lib/qkd/pki_hierarchical.py:857`.

### Config and profile key semantics

Crypto defaults RSA 4096/SHA-256; configured validity root 3650, issuing 1825, leaf 365 days. Subject defaults country/org/OU; root and issuing common names/filename prefixes/path lengths are domain-specific. Layout gives separate CA/cert and trust-exchange directories. Sources: `config/pki/hierarchical_ca.yml:9`, `config/pki/hierarchical_ca.yml:14`, `config/pki/hierarchical_ca.yml:19`, `config/pki/hierarchical_ca.yml:24`, `config/pki/hierarchical_ca.yml:91`.

Profile consumption: `key_usage` booleans create X.509 KeyUsage; `extended_key_usage.enabled/server_auth/client_auth` controls EKU; `basic_constraints.ca/path_length/critical` controls CA constraints; subject SAN derives from device hostname/DNS/IP and domain leaf toggles. Profile metadata such as declared allowed-hash lists, output toggles or TLS-role descriptors should not be assumed to enforce policy simply because present in YAML. Actual implementation uses explicit extension builders and output-writing logic. Sources: `lib/qkd/pki_hierarchical.py:127`, `lib/qkd/pki_hierarchical.py:143`, `lib/qkd/pki_hierarchical.py:163`, `lib/qkd/pki_hierarchical.py:179`, `lib/qkd/pki_hierarchical.py:434`, `config/pki/profiles/kme_server.yml:40`.

The declared `pki.safety.overwrite_existing`, chmod strings and `export_ca_private_keys` are not consulted in the generation path as a generalized safety policy; file writers apply their own modes, generate fresh material and write outputs. Do not rely on `overwrite_existing: false` to preserve an existing CA across an explicit rebuild. Sources: `config/pki/hierarchical_ca.yml:105`, `lib/qkd/pki_hierarchical.py:38`, `lib/qkd/pki_hierarchical.py:54`, `lib/qkd/pki_hierarchical.py:598`, `lib/qkd/pki_hierarchical.py:793`.

## On-router PKI installation

Provisioning resolves selected runtime profile and SAE-specific paths, accepts compatible SAE filename aliases, copies leaf cert/private key and trust file into configured remote cert dir, sets ownership/modes and synchronizes certs to peer RE as needed. They are filesystem certificates used by Python requests, not evidence of a configured Junos native IKE/IPsec PKI enrollment. Sources: `lib/qkd/provisioning.py:445`, `lib/qkd/provisioning.py:474`, `lib/qkd/provisioning.py:575`, `lib/qkd/provisioning.py:279`, `artifacts/qkd_onbox.py:236`, `artifacts/qkd_onbox.py:5809`.

Normal push order copies certificates before script registration/MACsec commits; if later commits fail, copied certificates can remain. AO preparation also pushes certs without MACsec setup. Sources: `lib/qkd/provisioning.py:1139`, `lib/qkd/provisioning.py:941`.

## KME trust staging

Self-signed profile: stage generated KME leaves and public root renamed `root.crt`. Hierarchical: stage KME leaf/chain/PEM and trust material for Juniper clients. Verify SAN IP before remote destructive replacement unless bypassed. Runtime Compose mounts cert directory read-only; KME TLS env selects leaf certificate, private key and `root.crt`. Sources: `lib/kme/cert_install.py:309`, `lib/kme/cert_install.py:331`, `lib/kme/cert_install.py:415`, `lib/kme/cert_install.py:522`, `lib/kme/compose.py:354`.

Do not transfer root/issuing private signing keys to routers or KME runtime volumes. The manual instruction helper explicitly warns against that. It also prints a legacy restart suggestion using `compose down -v`, which risks losing DB volumes; prefer understanding the KME-only restart implementation before following that text literally. Sources: `lib/kme/instructions.py:83`, `lib/kme/instructions.py:140`, `lib/kme/instructions.py:147`, `lib/kme/restart.py:362`.

## Inspection tools and failure semantics

`tools/cert_manager.py` accepts one or more input files/directories; recursion false; JSON false; password None or secure prompt false; strict false. It finds PEM blocks, loads certificates/private keys, reports subject/issuer/SAN/validity/key/extension information and parse errors. No files yields 1; strict returns 1 if parse errors exist; password flags conflict. It is an analyzer, not enrollment, revocation, certificate deployment or continuous expiry remediation. Sources: `tools/cert_manager.py:54`, `tools/cert_manager.py:83`, `tools/cert_manager.py:368`, `tools/cert_manager.py:444`, `tools/cert_manager.py:484`.

`tools/cert_report_filter.py` consumes the analyzer JSON, filters severity/expiry/unencrypted-key/identifier policy and emits text/JSON to stdout or file; malformed input returns 2, filtered errors return 1. It does not alter certificate material. Sources: `tools/cert_report_filter.py:243`, `tools/cert_report_filter.py:261`.

## Lifecycle risks and cleanup

- Runtime signature checks do not establish certificate validity, matching current profile content, revocation state or unexpired lifetime; cert_manager can inspect, but create reuse is marker/signature-based. Sources: `qkd_orchestrator.py:113`, `qkd_orchestrator.py:1343`, `tools/cert_manager.py:215`.
- No renewal daemon/CA service/container or revocation-publication flow is wired into these orchestrators. Scope evidence: create generation dispatch and PKI standalone CLI, `qkd_orchestrator.py:1353`, `lib/qkd/pki_hierarchical.py:880`.
- `clean --pki` removes controller cert tree contents, without disabling existing Linux KME use or home seed state; remote clean always removes router cert directories, independently of pki flag. Sources: `lib/qkd/clean.py:118`, `lib/qkd/clean.py:448`, `lib/qkd/clean.py:1021`.
- KME destroy does not remove remote cert bind directory; removing a container is not revocation or secure erasure of private material. Source: `lib/kme/destroy.py:13`.

## Declared profile knobs versus effective controls

| Profile fields | Actually consumed? | Evidence |
|---|---|---|
| basic_constraints.ca/path_length/critical | Yes; path_length only when ca=true | `lib/qkd/pki_hierarchical.py:163`, `lib/qkd/pki_hierarchical.py:469` |
| key_usage booleans/critical | Yes; encipher_only/decipher_only meaningful only with key_agreement | `lib/qkd/pki_hierarchical.py:127`, `lib/qkd/pki_hierarchical.py:473` |
| extended_key_usage.enabled/server_auth/client_auth | Yes; no enabled usages means no extension | `lib/qkd/pki_hierarchical.py:143` |
| default_key_size | Root-profile fallback only if global crypto key_size absent | `lib/qkd/pki_hierarchical.py:639` |
| subject_key_identifier.enabled / authority_key_identifier.enabled | Toggle not read; extensions added unconditionally in leaf builder | `lib/qkd/pki_hierarchical.py:475` |
| subject_alt_name.enabled/include_dns/include_ip | Profile toggles not read by leaf builder; domain leaf options passed explicitly | `lib/qkd/pki_hierarchical.py:434`, `lib/qkd/pki_hierarchical.py:695` |
| profile/certificate_type/default_validity_days/allowed_hash_algorithms | Metadata, not independent validation/selection controls in domain build | `lib/qkd/pki_hierarchical.py:598`, `lib/qkd/pki_hierarchical.py:639` |
| certificate_outputs.generate_* / tls_role.* | Declarative metadata; writer chooses outputs and EKU builder uses extended_key_usage | `lib/qkd/pki_hierarchical.py:143`, `lib/qkd/pki_hierarchical.py:434` |

