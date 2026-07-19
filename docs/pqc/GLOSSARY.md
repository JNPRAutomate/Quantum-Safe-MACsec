# Glossary

## Document Classification

- Document type: Architectural Glossary (LLD Support Artifact)
- Architectural layer: shared terminology across QKD/KME/PQC design documents
- Normative scope: canonical definitions used by architecture and LLD documents
- Out of scope: troubleshooting or procedural instructions

## A

**AN (Association Number)**  
MACsec association number used for active/previous SAK tracking.

## C

**CA (Connectivity Association)**  
MACsec security association context bound to interfaces and keychains.

**CAK (Connectivity Association Key)**  
Key material used in MKA to derive and validate secure connectivity state.

**CKN (Connectivity Association Key Name)**  
Identifier associated with CAK usage in MKA sessions.

## D

**DEC (`dec_keys`)**  
KME API path/operation used by receiver side to fetch decrypt key material for a given `key_id`.

## E

**ENC (`enc_keys`)**  
KME API path/operation used by sender side to fetch encrypt key material and a new `key_id`.

**ETSI GS QKD 014**  
Standardized API model for QKD key delivery workflows between secured application entities and KMEs.

## H

**Hitless Rotation**  
Key transition model designed to avoid traffic interruption while moving from active to next key.

## K

**KME (Key Management Entity)**  
Service that stores/distributes key material and exposes API endpoints for key retrieval.

**Key-ID (`key_id`)**  
Identifier that binds both peers to the same key material lifecycle without exchanging raw key bytes directly.

## M

**MACsec (IEEE 802.1AE)**  
Layer-2 encryption for Ethernet links.

**MKA (MACsec Key Agreement, IEEE 802.1X)**  
Control protocol used to coordinate MACsec keying state and secure connectivity.

## P

**PKI (Public Key Infrastructure)**  
Certificate/trust model used for secure mTLS communications with KME services.

**PQC (Post-Quantum Cryptography)**  
Cryptographic approach resilient against quantum-enabled adversaries.

## Q

**QKD (Quantum Key Distribution)**  
Mechanism for generating/distributing high-entropy symmetric key material through quantum-assisted key exchange infrastructure.

**QKD On-Box Script (`qkd_onbox.py`)**  
Per-device runtime script that coordinates key installation, scheduling, and MACsec operational actions.

## S

**SAE (Secure Application Entity)**  
Endpoint identity that requests keys from KME APIs and applies them to application/security workflows.

**SAK (Secure Association Key)**  
Active MACsec data-plane key used for packet protection.

## T

**Trust Bundle**  
Certificate bundle deployed to establish trust anchors across Juniper and KME-side mTLS endpoints.
