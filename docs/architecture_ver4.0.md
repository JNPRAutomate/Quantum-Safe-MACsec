# Quantum-Safe MACsec Integration for JUNOS Devices

This document provides an analysis of the `JNPRAutomate/Quantum-Safe-MACsec` project, focusing on its architecture, key components, and inferred operational steps.

## 1. Project Overview

The `Quantum-Safe-MACsec` project is a Python 3 script designed to enable JUNOS devices to securely fetch key material from Quantum Key Distribution (QKD) systems and Key Management Entities (KME). This key material is then used to update the static MACsec (Media Access Control Security) Ciphersuite Access Key (CAK) on the JUNOS devices, enhancing network security with quantum-safe principles. The project leverages libraries from the "ETSI GS QKD 014 v1.1.1 - Reference Implementation" public repository.

**Key Goals:**
* Integrate QKD/KME with JUNOS devices.
* Automate MACsec static CAK updates using quantum-safe keys.
* Provide a secure communication framework for key exchange.

## 2. Key Features

* **QKD/KME Integration:** Facilitates interaction with QKD systems and KME for key material retrieval.
* **MACsec CAK Update:** Automates the process of updating MACsec static CAKs on JUNOS devices.
* **Secure Communication:** Implements a robust security model using PKI and MTLS.
* **Python-based:** Developed entirely in Python 3, making it flexible and extensible.
* **Simulation Capability:** Includes a QKD simulator for testing and development.

## 3. Architecture

The project's architecture is centered around orchestrating secure key material distribution from QKD/KME infrastructure to JUNOS network devices. It relies on a strong Public Key Infrastructure (PKI) for mutual authentication and secure communication.

```mermaid
graph TD
    subgraph QKD/KME Infrastructure
        QKD_System[QKD System]
        KME[Key Management Entity]
    end

    subgraph Quantum-Safe-MACsec Script (Python)
        direction LR
        PKI_Module[pki.py: PKI Management] --> KME_Orchestrator
        QKD_Orchestrator[qkd_orchestrator.py: QKD Orchestration] --> KME_Orchestrator
        KME_Orchestrator[kme_orchestrator.py: KME Interaction] --> Provisioning_Module
        Inventory_Builder[inventory_builder.py: Device Inventory] --> Provisioning_Module
        Onbox_Builder[onbox_builder.py: On-device Script Generation] --> Provisioning_Module
        Provisioning_Module[provisioning.py: Device Provisioning] --> JUNOS_Devices
        QKD_Sim[qkd_sim: QKD Simulator]
        Logger[logger.py: Logging]
        Settings[settings.py: Configuration]
        Rendering[rendering.py: Template Rendering]
    end

    QKD_System -- Provides Key Material --> KME
    KME -- Securely Delivers Key Material --> KME_Orchestrator
    KME_Orchestrator -- Authenticates via MTLS --> KME
    Provisioning_Module -- Configures MACsec --> JUNOS_Devices[JUNOS Devices]
```

### 3.1. Security Model

The project employs a robust security model to ensure the integrity and confidentiality of key material and communication:

* **PKI (Public Key Infrastructure):**
    * Utilizes a single root Certificate Authority (CA).
    * All participating entities (e.g., KME, orchestrator, JUNOS devices) are signed by this CA.
    * The `pki.py` module likely handles certificate generation, signing, and management.
* **MTLS (Mutual Transport Layer Security):**
    * The Secure Access Entity (SAE), which represents the script's interaction component, authenticates to the KME using MTLS. This ensures both client and server verify each other's identity.
* **Risks & Production Notes (as per repository):**
    * **Current Risk:** The root key is currently copied (intended for lab use only).
    * **Production Requirement:** In a production environment, a Hardware Security Module (HSM) must be used, and the CA key must be rigorously protected.

### 3.2. Core Components

* **`kme_orchestrator.py`**: Manages communication and key material exchange with the Key Management Entity (KME).
* **`qkd_orchestrator.py`**: Likely handles the orchestration of QKD processes, potentially initiating key requests or monitoring QKD status.
* **`provisioning.py`**: Responsible for taking the fetched key material and provisioning it onto the JUNOS devices, likely updating MACsec configurations.
* **`pki.py`**: Contains functions for Public Key Infrastructure management, such as certificate generation, signing, and validation.
* **`inventory_builder.py`**: Used to create or manage an inventory of JUNOS devices that the system will interact with.
* **`onbox_builder.py`**: Potentially generates scripts or configuration snippets that are pushed directly to the JUNOS devices.
* **`qkd_sim` (directory)**: Houses a QKD simulator, useful for testing and development without requiring a physical QKD setup.
* **`logger.py`**: Provides logging functionalities for tracking operations and debugging.
* **`settings.py`**: Stores configuration parameters for the project.
* **`rendering.py`**: Likely used for templating configuration files or scripts that are deployed to JUNOS devices.

## 4. Step-by-Step Usage (Inferred)

The following steps outline a plausible workflow for setting up and using the Quantum-Safe MACsec integration. These are inferred based on the file structure and project description.

### 4.1. Prerequisites

1. **Python 3:** Ensure Python 3 is installed on your system.
2. **Dependencies:** Install required Python libraries.
    ```bash
    pip install -r requirements.txt
    ```
3. **JUNOS Devices:** Have accessible JUNOS devices configured for MACsec, or ready to be configured.
4. **QKD/KME Infrastructure:** A QKD system and KME must be operational and accessible, or the `qkd_sim` can be used for testing.

### 4.2. Setup and Configuration

1. **Clone the Repository:**
    ```bash
    git clone https://github.com/JNPRAutomate/Quantum-Safe-MACsec.git
    cd Quantum-Safe-MACsec
    ```
2. **Configure Settings:**
    * Edit `settings.py` to configure parameters such as KME endpoints, device connection details (e.g., NETCONF credentials, SSH keys), and logging preferences.
    * Review and adjust any configuration files within the `config` directory as needed.
3. **Establish PKI:**
    * Utilize `pki.py` to generate a root CA, and then issue certificates for the KME and the orchestrator script itself.
    * Ensure these certificates are properly deployed to the KME and configured for the script's MTLS client.
    * **Example (conceptual):**
        ```bash
        python pki.py --generate-ca
        python pki.py --issue-cert --entity kme --ca-cert <ca_cert> --ca-key <ca_key>
        python pki.py --issue-cert --entity orchestrator --ca-cert <ca_cert> --ca-key <ca_key>
        # Deploy generated certificates and keys to respective entities.
        ```
4. **Build Device Inventory:**
    * Use `inventory_builder.py` to define your JUNOS devices, including their hostnames, IP addresses, and any specific group assignments. This inventory will be used by the `provisioning.py` script.
    * **Example (conceptual):**
        ```bash
        python inventory_builder.py --add-device --name junos-router-1 --ip 192.168.1.1 --group macsec-enabled
        ```

### 4.3. Operation

1. **Start QKD/KME:** Ensure your QKD system and KME are running and ready to provide key material. If testing, you might need to activate the `qkd_sim`.
2. **Orchestrate Key Material Fetch:**
    * The `kme_orchestrator.py` and `qkd_orchestrator.py` scripts will interact with the KME to request and receive quantum-safe key material. This typically involves MTLS authentication.
    * **Example (conceptual):**
        ```bash
        python kme_orchestrator.py --request-key --kme-endpoint https://kme.example.com --cert <orchestrator_cert> --key <orchestrator_key>
        ```
3. **Provision JUNOS Devices:**
    * Once key material is obtained, `provisioning.py` will use the device inventory to connect to the target JUNOS devices.
    * It will then apply the new MACsec static CAK, potentially using templates rendered by `rendering.py` and possibly leveraging `onbox_builder.py` for on-device script generation or direct configuration pushes.
    * **Example (conceptual):**
        ```bash
        python provisioning.py --update-macsec --key-material <received_key> --device-group macsec-enabled
        ```
4. **Monitor and Log:**
    * Review the logs generated by `logger.py` to ensure operations are proceeding as expected and to troubleshoot any issues.

## 5. Security Considerations

* **Key Protection:** The most critical aspect is the protection of the root CA key and any other private keys used in the system. For production, HSMs are mandatory.
* **Network Security:** Ensure secure network connectivity between the orchestrator script, KME, and JUNOS devices.
* **Access Control:** Implement strict access control to the server running the orchestrator script and its configuration files.
* **Auditing:** Regularly audit logs for any unusual activity or failed key updates.
* **Vulnerability Management:** Keep all components, including Python and its libraries, updated to mitigate known vulnerabilities.

## 6. License

This project is released under the **MIT License**. Refer to the `LICENSE` file in the main branch of the repository for full details.

## 7. Contributing

While specific contribution guidelines are not provided in the retrieved context, standard open-source practices would apply:
1. Fork the repository.
2. Create a new branch for your features or bug fixes.
3. Submit pull requests with clear descriptions of your changes.
4. Ensure your code adheres to the project's style and passes any existing tests.
