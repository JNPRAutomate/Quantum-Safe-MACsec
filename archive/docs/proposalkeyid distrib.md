This architectural request addresses a well-known technical challenge in Quantum-Safe MACsec (QS-MACsec) deployments. [1] 
By default, the standard MACsec control plane (IEEE 802.1X / MKA) does not possess a native field or payload mechanism to share external identifiers like a Quantum Key Distribution (QKD) key-ID. Because MACsec operates strictly at Layer 2, routing standard Layer 3 IPsec IKEd (Internet Key Exchange daemon) exchanges down to provision an L2 encryption engine requires specific structural bridging. [2, 3, 4, 5] 
Breaking down the implementation requirements reveals a clear path forward.
------------------------------
## Architecture Overview

       +--------------------------------------------+

       |             IKEd (IKEv2 Daemon)            |
       |  - Handles ETSI GS QKD 014 REST-API calls  |
       |  - Fetches symmetric keys using Key-IDs    |
       +---------------------+----------------------+
                             | 
                     (Internal Inter-Process / IPC)
                             |
       +---------------------v----------------------+

       |          L2 Mechanism (MKA / LLDP)         |
       |  - Encapsulates and syncs Key-ID over wire |
       +---------------------+----------------------+
                             |
       +---------------------v----------------------+

       |           QS-MACsec Engine (PHY/L2)        |
       |  - Applies Quantum-Safe SAK to Ethernet    |
       +--------------------------------------------+

------------------------------
## Step 1: The Layer 2 Mechanism for key-ID Synchronization
Because MKA (MACsec Key Agreement) does not support out-of-the-box metadata for external key sync, you have two distinct approaches for your L2 mechanism: [2] 

* Approach A: Custom MKA TLV (Recommended)
* Extend the standard IEEE 802.1X MKA protocol by creating a vendor-specific or custom Type-Length-Value (TLV) payload.
   * During the peer discovery phase, the Key Server device generates or fetches a unique key-ID from the QKD node and embeds it inside this custom MKA TLV.
   * Pros: Keeps the entire key negotiation state machine unified inside MKA; retains strict L2 alignment. [4] 
* Approach B: Custom LLDP TLV (Alternative)
* Utilize Link Layer Discovery Protocol (LLDP) to broadcast the key-ID to the directly connected peer before initiating MACsec.
   * Cons: Introduces a dependency on an entirely separate protocol frame, meaning you must manage race conditions where LLDP updates arrive slower than MACsec negotiation.

## Step 2: Re-using IKEd for the ETSI API Integration
You do not need to rewrite a REST API client from scratch for the L2 layer. Instead, reuse your existing IPsec Internet Key Exchange daemon (IKEd), which already incorporates logic for interacting with ETSI GS QKD 014 API servers. [2] 

   1. Intercept the Flow: Configure IKEd so that it doesn't just apply keys to an IPsec Security Association (SA), but can act as a centralized Key Management System (KMS) broker. [2, 6] 
   2. Establish an Internal IPC Link: Create an internal communication channel—such as a local UNIX domain socket or Netlink interface—between IKEd and your MACsec/MKA control daemon.
   3. The Operational Workflow:
   * Step 1: The IKEd component communicates with the local QKD system via the ETSI GS QKD 014 REST-API to acquire a valid key-ID.
      * Step 2: IKEd pushes this key-ID across the internal socket down to the MACsec daemon.
      * Step 3: The MACsec daemon packs this string into the L2 mechanism (Custom MKA TLV) and transmits it across the wire to the peer switch or firewall.
      * Step 4: The peer receives the frame at Layer 2, extracts the key-ID, and passes it upward to its own local IKEd daemon via its local socket.
      * Step 5: The peer's IKEd sends a targeted target-fetch request back to its respective ETSI QKD node using that identical key-ID to retrieve the corresponding symmetric key material.
      * Step 6: Both local daemons program the matching keys directly into their respective hardware MACsec cryptographic engines as the Secure Association Key (SAK). [2, 7] 
   
------------------------------
## Critical Engineering Guardrails

* Hitless Key Rollover: Ensure that when IKEd triggers a periodic key update from the ETSI provider, the custom L2 mechanism establishes a secondary SAK in the hardware engine before deleting the active one. Testing protocols verify that proper sequencing yields zero packet drops during QS-MACsec transitions. [2] 
* Packet Fragmentation Risks: Quantum-safe metadata can be quite large. Ensure that your custom Layer 2 MKA frame containing the key-ID does not exceed your standard link MTU sizes, or force the interface to accommodate small Jumbo frames if necessary. [8] 
* Security Domain Isolation: Ensure the internal IPC socket between IKEd and the L2 daemon is heavily sandboxed, verifying that only raw identifiers (key-IDs) pass over the L2 wire rather than the actual raw keys themselves. [2] 

To help flesh out this implementation plan, let me know:

* What operating system or network operating system (NOS) base is your IKEd running on?
* Are you utilizing an open-source MKA daemon like wpa_supplicant, or a proprietary L2 control plane?
* What specific version of the ETSI GS QKD API is your target node utilizing?


[1] [https://www.juniper.net](https://www.juniper.net/content/dam/www/assets/white-papers/us/en/2022/validation-of-quantum-safe-macsec-white-paper.pdf)
[2] [https://www.juniper.net](https://www.juniper.net/content/dam/www/assets/white-papers/us/en/2022/validation-of-quantum-safe-macsec-white-paper.pdf)
[3] [https://www.rambus.com](https://www.rambus.com/blogs/macsec/)
[4] [https://www.alliedtelesis.com](https://www.alliedtelesis.com/wp-content/uploads/2026/05/macsec_feature_overview_guide.pdf)
[5] [https://www.youtube.com](https://www.youtube.com/watch?v=Nl1tKbBeq0o&t=42)
[6] [https://www.paloaltonetworks.com](https://www.paloaltonetworks.com/cyberpedia/what-is-ike)
[7] [https://www.rambus.com](https://www.rambus.com/blogs/macsec/)
[8] [https://dl.acm.org](https://dl.acm.org/doi/10.1145/3733825.3765281)
