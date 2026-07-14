# Product Requirement Statement

Implement native Junos QKD-assisted MACsec key management.

The feature introduces a qkd-macsec control daemon responsible for:

- ETSI GS QKD 014 key retrieval from the local KME using mTLS
- SAE-to-SAE key-id coordination over authenticated peer signaling
- make-before-break MACsec key rotation
- runtime installation of QKD-derived CAK/CKN into MACsec/MKA
- operational visibility through CLI/YANG/gNMI
- failure-safe behavior preserving current secure connectivity

## Core architectural principle
Configuration defines intent.
qkmd manages key lifecycle.
MACsec/MKA encrypts traffic.
KME provides key material.
Peer signaling exchanges only key-id.
No secret crosses the router-to-router link.
No per-rotation commit.
No script.

## Engineerign effort to implement
1. mgd / CLI / YANG
   Add configuration hierarchy, operational commands, and RPCs.

2. qkmd native daemon
   Implement state machine, timers, peer sessions, failure handling.

3. ETSI GS QKD 014 client library
   Implement /enc_keys and /dec_keys interaction with KME over mTLS.

4. Certificate/key manager integration
   Use Junos-native certificate store and trust anchor handling.

5. MACsec/MKA integration
   Install QKD-derived CAK/CKN into MACsec runtime without per-rotation commit.

6. Peer signaling protocol
   Native qkmd-to-qkmd protocol, or future MKA TLV extension.

7. Telemetry
   CLI, syslog, traceoptions, YANG state, gNMI counters.

8. HA behavior
   RE switchover, GRES/NSR behavior, daemon restart recovery.

9. Platform capability matrix
   ACX/MX/QFX/SRX support and cipher availability.

10. Productization/roadmap
   Feature request, platform ownership, testing, documentation.

## recommendation
Phase 1:
  Native qkmd daemon + CLI/YANG + KME client + peer signaling over mTLS.

Phase 2:
  Tight MACsec/MKA runtime integration with no commit-per-rotation.

Phase 3:
  Standardize or vendor-extend MKA with a QKD key coordination TLV.

# QKMP = Quantum Key Management Protocol

## Introduction
Native Junos feature made of:
- a new Junos control-plane daemon
- a CLI/YANG configuration model
- operational commands
- a KME/ETSI-014 client
- peer-to-peer key-id signaling
- integration with MACsec/MKA key installation

The basic principle remains exactly what we validated in the lab: the secret key never crosses the router-to-router link.
Only the key-id is exchanged between the two routers.
Each router gets the actual key material from its own local KME.
MACsec/MKA uses that key material to build and rotate the secure link.
The MACsec tunnel is built from a control-plane phase and a data-plane phase; CAK/CKN come from QKD, while MACsec itself remains standard. It also states that only the key-id is transported between the two SAE/router sides, not the secret key.
## Feature name and components
qkd-macsec-control
- qkmd       Native Junos daemon
- qkmctl     Operational CLI/RPC frontend
- qkm.yang   Configuration/state/RPC YANG model
- qkm-trace  Traceoptions, syslog, event reporting

* qkmd talks to the KME using ETSI GS QKD 014.
* qkmd talks to the peer router/SAE to exchange only the key-id.
* qkmd installs and rotates MACsec key material.
* mgd exposes configuration and operational state through CLI/YANG/NETCONF/gNMI.
* MACsec/MKA keeps handling the actual secure channel.

Junos already exposes configuration, operational state, operational commands, and Junos extensions using native YANG modules, so a real implementation should have its own configuration/state/RPC schema instead of relying on files under /var/db/scripts.

## high level architecture
For a physical link:
ACX1 ---------------------------- ACX2
 et-2/0/2                         et-2/0/2
And each router has its own KME:
ACX1 <---- mTLS / ETSI-014 ----> KME1
ACX2 <---- mTLS / ETSI-014 ----> KME2

The complete master/slave control flow:

ACX1/qkmd                         ACX2/qkmd
   |                                  |
   | POST /enc_keys to KME1           |
   | gets key + key-id                |
   |                                  |
   | ---- QKMP KEY_OFFER key-id ----> |
   |                                  |
   |                                  | POST /dec_keys key-id to KME2
   |                                  | gets matching key
   |                                  |
   | <--- QKMP KEY_ACK key-id --------|
   |                                  |
 install standby CA/key-chain      install standby CA/key-chain
 switch active CA at same epoch    switch active CA at same epoch

one side fetches the key from its KME, sends only the key ID to the peer, and the peer verifies that key ID with its own KME before both sides bring up or rotate the MACsec tunnel.

Note: the master router never sends CAK to the peer. Instead it sends: 
* key-id
* key-index
* epoch/start-time
* lifetime
* algorithm/cipher intent
* CA label
* sequence/generation

J1 fetches the key from KME1, sends only key-id to J2, J2 queries KME2 with that key-id and both bring up MACsec with syncronized material.

## DWhy This Should Not Be Plain 802.1X
802.1X/MKA is an authentication and MACsec key agreement/control mechanism. QKD/ETSI is a key delivery and key synchronization system (SAE coordination). 802.1X does not carry application-defined metadata such as a QKD key-id, and if it derives keys by itself, it bypasses or conflicts with the QKD key-delivery model.
The secret is key delivery: the peer must know which key-id to use, and not that 802.1X generates another key schedule autonomously. 

### Option A — Separate Native QKD-MACsec Control Protocol
qkmd <---- mTLS/gRPC/QUIC/TCP ----> qkmd

#### Pros
* Clean architecture.
* Easy to debug.
* Independent from current MKA standards.
* Can later support PQC-enabled transport.
* Does not require changing MKA immediately.

#### Cons
It is a new control-plane protocol running in parallel with MKA.

### Option B — Extend MKA with a New QKD TLV
Example new MKA TLV:
* QKD-Key-Coordination-TLV
  * sae-id
  * key-id
  * key-generation
  * activation-time
  * validity
  * kme-profile
  * algorithm


#### Pros

Key coordination remains inside the MACsec/MKA domain.
Cleaner from a protocol-design perspective.
Better candidate for future standardization.

#### Cons

Requires changes to MKA.
Requires vendor/platform support.
Likely requires standardization or at least a vendor extension.

MKA would need a new TLV carrying values such as key_id, key_index, and validity, plus new behavior where one peer announces the key-id and the other confirms that it can retrieve the same key.

### Option C — Local REST/gRPC Runtime API on Junos EVO
If the platform supports it, another possible design is:
https://router/qkd-macsec/key-id
grpc://router/qkdmacsec.Control/OfferKey

#### Pros
Modern application interface.
Clean external integration.
Could work well on Junos EVO-style platforms.

#### Cons
Depends heavily on platform/runtime support.
Less portable across classic Junos platforms.

## Proposed Junos CLI Configuration
Create a new hierarchy under:
[security qkd-macsec]

### Global configuration
set security qkd-macsec enable
set security qkd-macsec traceoptions file qkd-macsec.log size 10m files 10
set security qkd-macsec traceoptions flag kme-api
set security qkd-macsec traceoptions flag peer-signaling
set security qkd-macsec traceoptions flag key-install
set security qkd-macsec traceoptions flag failover
#### Purpose
Enable the feature globally.
Enable tracing for KME communication.
Enable tracing for router-to-router key-id signaling.
Enable tracing for MACsec key installation.
Enable tracing for failover/error conditions.

### KME profile
set security qkd-macsec kme-profile KME1 url https://100.123.252.10:8443
set security qkd-macsec kme-profile KME1 local-sae sae_001
set security qkd-macsec kme-profile KME1 peer-sae sae_002
set security qkd-macsec kme-profile KME1 protocol etsi-gs-qkd-014
set security qkd-macsec kme-profile KME1 tls client-certificate sae_001
set security qkd-macsec kme-profile KME1 tls client-key sae_001_key
set security qkd-macsec kme-profile KME1 tls trusted-ca qkd_ca_bundle
set security qkd-macsec kme-profile KME1 tls server-name kme1.lab.local
set security qkd-macsec kme-profile KME1 connect-timeout 5
set security qkd-macsec kme-profile KME1 request-timeout 10
set security qkd-macsec kme-profile KME1 retry 3


Which KME to use.
Which local SAE identity the router represents.
Which remote SAE/key consumer is expected.
Which ETSI QKD API profile is used.
Which client certificate/key to use for mTLS.
Which CA bundle validates the KME server certificate.
Timeout/retry behavior.

Note: the QKD server interface follows ETSI 014 and that mTLS/certificate trust is a prerequisite before any key can be delivered.

### Peer profile
set security qkd-macsec peer ACX2 address 100.123.170.12
set security qkd-macsec peer ACX2 transport grpc-mtls
set security qkd-macsec peer ACX2 port 9443
set security qkd-macsec peer ACX2 tls local-certificate sae_001
set security qkd-macsec peer ACX2 tls trusted-ca device_ca_bundle
set security qkd-macsec peer ACX2 expected-sae sae_002
set security qkd-macsec peer ACX2 hold-time 15
set security qkd-macsec peer ACX2 keepalive 5

This defines: 
* The remote router/SAE endpoint.
* The transport used for key-id signaling.
* The mTLS identity used between routers.
* The expected peer SAE identity.
* Keepalive and hold-time behavior.

### QKD-MACsec Policy
set security qkd-macsec policy QKD-POLICY1 mode master
set security qkd-macsec policy QKD-POLICY1 kme-profile KME1
set security qkd-macsec policy QKD-POLICY1 peer ACX2
set security qkd-macsec policy QKD-POLICY1 cipher-suite gcm-aes-xpn-256
set security qkd-macsec policy QKD-POLICY1 cak-length 32
set security qkd-macsec policy QKD-POLICY1 ckn-length 32
set security qkd-macsec policy QKD-POLICY1 rekey-interval 60
set security qkd-macsec policy QKD-POLICY1 activation-skew 5
set security qkd-macsec policy QKD-POLICY1 key-pool-size 2
set security qkd-macsec policy QKD-POLICY1 max-installed-keys 2
set security qkd-macsec policy QKD-POLICY1 standby-ca-count 1
set security qkd-macsec policy QKD-POLICY1 failure-action keep-current-key
set security qkd-macsec policy QKD-POLICY1 no-key-action hold-macsec
set security qkd-macsec policy QKD-POLICY1 replay-protection enable

This defines:
Whether this side initiates key-id offers.
Which KME profile to use.
Which peer profile to use.
Which MACsec cipher suite to use.
How often to rotate.
How many keys can be installed.
How many standby CAs are maintained.
What to do if KME/peer/key retrieval fails.

The CAK/CKN length rules matter. Your existing QKD MACsec documentation states that CKN length is recommended at 64 hex digits/32 octets and that CAK length is important for interoperability, especially with AES-128 vs AES-256 cases.

### Interface binding
set security qkd-macsec interface et-2/0/2 policy QKD-POLICY1
set security qkd-macsec interface et-2/0/2 connectivity-association-prefix QKD_CA
set security qkd-macsec interface et-2/0/2 role master
set security qkd-macsec interface et-2/0/2 peer-interface et-2/0/2
set security qkd-macsec interface et-2/0/2 include-sci
set security qkd-macsec interface et-2/0/2 must-secure

### On the peer side
set security qkd-macsec policy QKD-POLICY1 mode slave
set security qkd-macsec interface et-2/0/2 role slave

This maps the QKD-MACsec policy to the physical MACsec interface.

# Required Parameters
## SAE Identity Parameters
* local-sae
* peer-sae
* sae-certificate
* sae-private-key
* sae-trust-anchor
These are needed because ETSI QKD systems operate in terms of SAE/KME identities.Consumer names and certificates must match the Juniper/QKD configuration.

## KME Parameters
* kme-url
* protocol etsi-gs-qkd-014
* enc-keys-endpoint
* dec-keys-endpoint
* server-name/SAN validation
* client-auth certificate
* trusted-ca-bundle
* timeout
* retry
* backoff

Used for: 
* Getting new encryption keys from the local KME.
* Retrieving peer-confirmed keys by key-id.
* Validating KME server identity.
* Authenticating the router/SAE to the KME.

## MACsec Parameters
* interface
* cipher-suite
* cak-length
* ckn-length
* connectivity-association-prefix
* mka transmit-interval
* sak-rekey-interval
* include-sci
* xpn enable/disable
* must-secure/should-secure
* exclude-protocol lldp/lacp/etc

These parameters are needed to bind QKD key management to the MACsec data plane.

## Key Lifecycle Parameters
rekey-interval
activation-time
make-before-break
standby-ca-count
max-installed-keys
key-validity-window
key-retire-delay
rollback-on-failure

The make-before-break model is important. Update the standby CA first, then switch the active association, so the MACsec tunnel does not flap during rotation.

## Failure Policy Parameters

failure-action keep-current-key | fallback-static | bring-down
kme-unreachable-action keep-current-key
peer-unreachable-action do-not-activate-new-key
key-mismatch-action reject
clock-skew-action reject | warn

### For production, defaults
failure-action keep-current-key
peer-unreachable-action do-not-activate-new-key
key-mismatch-action reject
clock-skew-action reject
### Reason
If the KME or peer is temporarily unreachable, do not install a wrong key.
If both sides cannot confirm the same key-id, do not activate the new CA.
Preserve the current secure state whenever possible.

# Operational CLI Commands
## show commands 
show security qkd-macsec summary
show security qkd-macsec interface et-2/0/2
show security qkd-macsec keys
show security qkd-macsec keys detail
show security qkd-macsec peer ACX2
show security qkd-macsec kme-profile KME1
show security qkd-macsec statistics
show security qkd-macsec history
show security qkd-macsec failures

## request commands
request security qkd-macsec interface et-2/0/2 rotate-key
request security qkd-macsec interface et-2/0/2 prefetch-key
request security qkd-macsec interface et-2/0/2 resync-peer
request security qkd-macsec interface et-2/0/2 clear-state
request security qkd-macsec kme-profile KME1 test-connectivity
request security qkd-macsec peer ACX2 test-signaling

## example output
admin@acx1> show security qkd-macsec interface et-2/0/2

Interface: et-2/0/2
Role: master
Peer: ACX2
KME profile: KME1
Local SAE: sae_001
Peer SAE: sae_002
MACsec CA active: QKD_CA1
MACsec CA standby: QKD_CA2
Current key-id: 8f8e4c1a-...
Next key-id: 93ad72b9-...
Last rotation: 2026-07-11 06:01:00 CEST
Next rotation: 2026-07-11 06:02:00 CEST
KME status: up
Peer signaling: up
mTLS: valid
Last failure: none

# Native Daemon State Machine
The qkmd daemon should maintain a state machine per interface/policy binding.
INIT
  -> KME_TLS_CHECK
  -> PEER_SESSION_UP
  -> FETCH_ENC_KEY
  -> OFFER_KEY_ID
  -> WAIT_PEER_ACK
  -> INSTALL_STANDBY_CA
  -> ARM_ACTIVATION
  -> SWITCH_ACTIVE_CA
  -> RETIRE_OLD_CA
  -> STEADY

  Error state: 
- KME_DOWN
- PEER_DOWN
- KEY_ID_NOT_FOUND
- KEY_MISMATCH
- MACSEC_INSTALL_FAIL
- MKA_NOT_LIVE
- CLOCK_SKEW
- CERT_EXPIRED
- TLS_VALIDATION_FAIL

# Configuration Model: Persistent Intent, Runtime Keys
in a native implementation, I would still separate: Persistent configuration from Runtime key state.

## persistent config
security qkd-macsec ...
security macsec ...
certificates/profiles ...
## runtime state
current key-id
next key-id
actual CAK/CKN material
activation epoch
peer ACK state
KME state
MACsec install state
The key material should not be written into normal human configuration every rotation. The commit database should define intent, not constantly change because a key rotated.

Configuration defines the desired behavior.
qkmd manages the key lifecycle.
The actual secret material lives in a secure runtime key store.
MACsec/MKA receives a key handle/reference.
The user sees key-id, status, lifetime, and rotation history — not necessarily the raw secret.

This avoids the ugly model of generating new set security authentication-key-chains ... configuration every 60 seconds and committing constantly.

# MACsec/MKA Integration
The clean model would be to add a new MACsec security mode.
## example
set security macsec connectivity-association QKD_CA security-mode qkd-cak
set security macsec connectivity-association QKD_CA qkd-policy QKD-POLICY1
or
set security macsec interfaces et-2/0/2 qkd-key-management QKD-POLICY1

Meaning
MACsec knows this CA is externally supplied by qkd-macsec.
qkmd supplies CAK/CKN/key-id at runtime.
MKA uses the supplied material to maintain the secure channel.
No per-rotation commit is required.

This is the architectural difference between:automation around Junos and Junos being QKD-aware for MACsec

# QKMP Protocol Messages
A minimal protocol between routers could use authenticated messages over mTLS.

## HELLO
{
  "type": "HELLO",
  "version": 1,
  "local_sae": "sae_001",
  "peer_sae": "sae_002",
  "interface": "et-2/0/2",
  "capabilities": [
    "etsi-014",
    "macsec-xpn-256",
    "make-before-break"
  ]
}
Purpose: 
Establish peer identity.
Validate SAE mapping.
Negotiate protocol version and capabilities.

## KEY OFFER
{
  "type": "KEY_OFFER",
  "session_id": "uuid",
  "generation": 42,
  "key_id": "uuid-or-kme-key-id",
  "activation_time": "2026-07-11T06:04:00+02:00",
  "valid_until": "2026-07-11T06:10:00+02:00",
  "cipher_suite": "gcm-aes-xpn-256",
  "ca_name": "QKD_CA2",
  "ckn": "derived-or-label",
  "nonce": "..."
}
Purpose
Master tells the peer which QKD key-id should be used next.
No secret material is included.

## KEY_ACK
{
  "type": "KEY_ACK",
  "session_id": "uuid",
  "generation": 42,
  "key_id": "same-key-id",
  "status": "accepted",
  "installed_ca": "QKD_CA2"
}
Purpose
Slave confirms it successfully retrieved the same key-id from its KME.
Slave confirms it installed the standby MACsec CA.

## KEY_ACTIVATE
{
  "type": "KEY_ACTIVATE",
  "generation": 42,
  "activate_at": "2026-07-11T06:04:00+02:00"
}
Purpose
Both routers activate the new CA at the same planned time.

## KEY_STATUS
{
  "type": "KEY_STATUS",
  "generation": 42,
  "active": true,
  "mka_status": "live",
  "macsec_rx": "ok",
  "macsec_tx": "ok"
}

Purpose
Runtime health and confirmation after activation.
All these messages should run over an authenticated channel, ideally mTLS. Optionally, message-level signing/HMAC could be added

# Commit validation
During a commit check Junos should validate:
local-sae is defined.
peer-sae is defined.
KME URL is syntactically valid.
Client certificate exists.
Client key exists.
Trusted CA bundle exists.
KME server-name/SAN validation is configured.
Interface supports MACsec.
Cipher suite is supported by the platform.
max-installed-keys >= 2 when make-before-break is enabled.
activation-skew is compatible with system clock/NTP.
role master/slave is not ambiguous.
Example failure: 
[edit security qkd-macsec interface et-2/0/2]
  'policy QKD-POLICY1'
    qkd-macsec requires max-installed-keys >= 2 when make-before-break is enabled
error: configuration check-out failed

# telemetry and state export
State should be visible through CLI, YANG, gNMI, and syslog.

Example YANG/gNMI paths:
/qkd-macsec/interfaces/interface[name=et-2/0/2]/state/kme-status
/qkd-macsec/interfaces/interface[name=et-2/0/2]/state/current-key-id
/qkd-macsec/interfaces/interface[name=et-2/0/2]/state/next-key-id
/qkd-macsec/interfaces/interface[name=et-2/0/2]/state/last-rotation
/qkd-macsec/interfaces/interface[name=et-2/0/2]/state/rotation-failures
/qkd-macsec/interfaces/interface[name=et-2/0/2]/state/mka-status

## Counters:
kme_enc_requests
kme_dec_requests
kme_failures
peer_key_offers_sent
peer_key_offers_received
key_acks_sent
key_acks_received
macsec_install_success
macsec_install_failure
rotation_success
rotation_failure

## syslog examples:
QKMD_KME_TLS_UP: KME profile KME1 TLS session established
QKMD_KEY_OFFER_SENT: interface et-2/0/2 generation 42 key-id 8f8e...
QKMD_KEY_ACK_RECEIVED: peer ACX2 accepted generation 42
QKMD_MACSEC_CA_INSTALLED: standby CA QKD_CA2 installed on et-2/0/2
QKMD_ROTATION_COMPLETE: active CA switched to QKD_CA2
QKMD_KEY_MISMATCH: peer rejected key-id 8f8e...


# Final User Experience
The final user should configure intent only.
Example ACX1
set security qkd-macsec enable

set security qkd-macsec kme-profile KME1 url https://100.123.252.10:8443
set security qkd-macsec kme-profile KME1 local-sae sae_001
set security qkd-macsec kme-profile KME1 peer-sae sae_002
set security qkd-macsec kme-profile KME1 protocol etsi-gs-qkd-014
set security qkd-macsec kme-profile KME1 tls client-certificate sae_001
set security qkd-macsec kme-profile KME1 tls client-key sae_001_key
set security qkd-macsec kme-profile KME1 tls trusted-ca qkd_ca_bundle

set security qkd-macsec peer ACX2 address 100.123.170.12
set security qkd-macsec peer ACX2 transport grpc-mtls
set security qkd-macsec peer ACX2 expected-sae sae_002

set security qkd-macsec policy P1 mode master
set security qkd-macsec policy P1 kme-profile KME1
set security qkd-macsec policy P1 peer ACX2
set security qkd-macsec policy P1 rekey-interval 60
set security qkd-macsec policy P1 max-installed-keys 2
set security qkd-macsec policy P1 cipher-suite gcm-aes-xpn-256
set security qkd-macsec policy P1 failure-action keep-current-key

set security qkd-macsec interface et-2/0/2 policy P1
set security qkd-macsec interface et-2/0/2 connectivity-association-prefix QKD_CA
set security qkd-macsec interface et-2/0/2 role master
set security qkd-macsec interface et-2/0/2 must-secure

commit
ACX2:
set security qkd-macsec enable

set security qkd-macsec kme-profile KME2 url https://100.123.252.11:8443
set security qkd-macsec kme-profile KME2 local-sae sae_002
set security qkd-macsec kme-profile KME2 peer-sae sae_001
set security qkd-macsec kme-profile KME2 protocol etsi-gs-qkd-014
set security qkd-macsec kme-profile KME2 tls client-certificate sae_002
set security qkd-macsec kme-profile KME2 tls client-key sae_002_key
set security qkd-macsec kme-profile KME2 tls trusted-ca qkd_ca_bundle

set security qkd-macsec peer ACX1 address 100.123.170.11
set security qkd-macsec peer ACX1 transport grpc-mtls
set security qkd-macsec peer ACX1 expected-sae sae_001

set security qkd-macsec policy P1 mode slave
set security qkd-macsec policy P1 kme-profile KME2
set security qkd-macsec policy P1 peer ACX1
set security qkd-macsec policy P1 rekey-interval 60
set security qkd-macsec policy P1 max-installed-keys 2
set security qkd-macsec policy P1 cipher-suite gcm-aes-xpn-256
set security qkd-macsec policy P1 failure-action keep-current-key

set security qkd-macsec interface et-2/0/2 policy P1
set security qkd-macsec interface et-2/0/2 connectivity-association-prefix QKD_CA
set security qkd-macsec interface et-2/0/2 role slave
set security qkd-macsec interface et-2/0/2 must-secure

commit

## the operator uses:
request security qkd-macsec interface et-2/0/2 start
show security qkd-macsec interface et-2/0/2
show security mka sessions summary
show security macsec connections

