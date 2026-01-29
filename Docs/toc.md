# **Table of contents**

# 1. Introduction
## 1.1 MACsec Overview
## 1.2 PQC and MACsec integration
## 1.3 Terminology and Entities involved
## 1.4 Static CAK

---

# 2. The core python MACsec/QKD script
## 2.1 Release Notes for MACSEC Configuration Script
### 2.1.1 Version 3.0.0
### 2.1.2 Overview
#### 2.1.2.1 New Features
#### 2.1.2.2 Features and Enhancements
#### 2.1.2.3 Usage
#### 2.1.2.4 Configuration
#### 2.1.2.5 Error Handling
#### 2.1.2.6 Known Issues
#### 2.1.2.7 Future Improvements
#### 2.1.2.8 Contribution
#### 2.1.2.9 License

---

## 2.2 Script details 
### 2.2.1 Entities involved in the communication 
### 2.2.2 REST APIs used in key exchange
### 2.2.3 QKD-MACsec key exchange automation
#### 2.2.3.1 Key exchange mechanism between 2 Juniper devices
### 2.2.4 Script functions
#### 2.2.4.1 Main KME /SAE interaction
#### 2.2.4.2 Detailed View
#### 2.2.4.3 Function Purpose
#### 2.2.4.4 Parameters
#### 2.2.4.5 Function Steps
### 2.2.5 Multi threading function
### 2.2.6 Certificate life cycle management (LCM)
#### 2.2.6.1 Generate client function
#### 2.2.6.2 Is valid certificate function 
#### 2.2.6.3 Create SSH client function
#### 2.2.6.4 Get certificates function
#### 2.2.6.5 Upload certificates function
#### 2.2.6.6 Fetch CA certificate function
#### 2.2.6.7 Renew certificates function
#### 2.2.6.8 Should check certs function
### 2.2.7 Logging and tracing
#### 2.2.7.1 Initialize logging function
#### 2.2.7.2 Get Args function
### 2.2.8 Initial configuration
#### 2.2.8.1 Check and apply initial config function
##### 2.2.8.1.1 Function Purpose
##### 2.2.8.1.2 Parameters
##### 2.2.8.1.3 Function Steps
### 2.2.9 Configurations
#### 2.2.9.1 Macsec configuration function
#### 2.2.9.2 Event-options
#### 2.2.9.3 Other necessary config
#### 2.2.9.4 Description of the macros
### 2.2.10 Key Management functions 
#### 2.2.10.1 Fetch KME key function
#### 2.2.10.2 Get previous key IDs function
#### 2.2.10.3 Save key IDs function

---

# 3. Appendix

## 3.1 Glossary of MACsec Concepts
### 3.1.1 AN - Association Number
### 3.1.2 CA - Connectivity Association
### 3.1.3 CAK - Connectivity Association Key
### 3.1.4 CKN - Connectivity Association Key Name
### 3.1.5 ICV - Integrity Check Value
### 3.1.6 MKA - MACsec Key Agreement
### 3.1.7 SA - Secure Association
### 3.1.8 SAK - Secure Association Key
### 3.1.9 SC - Secure Channel
### 3.1.10 SCI - Secure Channel Identifier
### 3.1.11 SecTAG - MAC Security Tag
### 3.1.12 XPN - Packet Number and eXtended Packet Number

---

## 3.2 Imported Packages and Modules Synthesis
#### 3.2.1.1 `requests`
#### 3.2.1.2 `jnpr.junos (Device, Config)`
#### 3.2.1.3 `paramiko (SSHException)`
#### 3.2.1.4 `scp (SCPClient, SCPException)`
#### 3.2.1.5 `lxml (etree, E)`
#### 3.2.1.6 `argparse`
#### 3.2.1.7 `threading (Thread, Lock)`
#### 3.2.1.8 `logging (handlers)`
#### 3.2.1.9 `json`
#### 3.2.1.10 `copy`
#### 3.2.1.11 `base64`
#### 3.2.1.12 `uuid`
#### 3.2.1.13 `re`
#### 3.2.1.14 `subprocess`
#### 3.2.1.15 `os`
#### 3.2.1.16 `OpenSSL (crypto)`
#### 3.2.1.17 `datetime`
#### 3.2.1.18 `schedule`
#### 3.2.1.19 `Profile`
#### 3.2.1.20 `time`

---

## 3.3 Logs 
### 3.3.1 Sample logs
#### 3.3.1.1 SAE_A (127 secs interval)
#### 3.3.1.2 SAE_B (61 seconds interval)

---
