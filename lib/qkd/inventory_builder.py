import yaml
import os
import secrets
from pathlib import Path
from lib.common.settings import CONFIG


# ----------------------------------------
# GENERATE MACSEC KEYS (STATIC MODE)
# ----------------------------------------

def generate_macsec_keys():
    return {
        "ckn": secrets.token_hex(8),   # 64-bit
        "cak": secrets.token_hex(16)   # 128-bit
    }

# ----------------------------------------
# ASSIGN LINKS TOPOLOGY
# ----------------------------------------
         
def assign_links(devices, pairs, topology, hub=None):
    
    # index devices by name (fast lookup)
    dev_map = {d["name"]: d for d in devices}

    # initialize links
    for d in devices:
        d["links"] = []
        d["_if_idx"] = 0  # internal pointer
    
    # connectivity association per link, not per device, this guarantees correct key rotation for multiple links in macsec environment
    ca_counter = 1 # global CA allocator per link
    
    for a, b in pairs:

        if topology == "hub":
            if a == hub:
                master, slave = a, b
            else:
                master, slave = b, a
        else:
            # default rule: first element = master
            master, slave = a, b


        # assign interface sequentially
        
        # ----------------------------
        # MASTER interface assignment (SAFE)
        # ----------------------------
        if dev_map[master]["_if_idx"] >= len(dev_map[master]["interfaces"]):   
            raise ValueError(
                            f"{master} has not enough interfaces for its links "
                            f"(needed={dev_map[master]['_if_idx'] + 1}, "
                            f"available={len(dev_map[master]['interfaces'])})"
            )

        m_if = dev_map[master]["interfaces"][dev_map[master]["_if_idx"]]
        dev_map[master]["_if_idx"] += 1
        
        # ----------------------------
        # SLAVE interface assignment (SAFE)
        # ----------------------------

        if dev_map[slave]["_if_idx"] >= len(dev_map[slave]["interfaces"]):
            raise ValueError(
                f"{slave} has not enough interfaces for its links "
                f"(needed={dev_map[slave]['_if_idx'] + 1}, "
                f"available={len(dev_map[slave]['interfaces'])})"
            )

        s_if = dev_map[slave]["interfaces"][dev_map[slave]["_if_idx"]]
        dev_map[slave]["_if_idx"] += 1
        
        # ----------------------------
        # Assign links
        # ----------------------------
        
        # ✅ CA allocation PER LINK
        ca_a = f"CA{ca_counter}"
        ca_b = f"CA{ca_counter + 1}"
        ca_counter += 2

        # master side
        dev_map[master]["links"].append({
            "peer": slave,
            "peer_ip": dev_map[slave]["ip"],    
            "peer_interface": s_if,
            "peer_sae": dev_map[slave]["sae_id"],
            "role": "master",
            "interface": m_if,
            "ca_names": [ca_a, ca_b]
        })

        # slave side
        dev_map[slave]["links"].append({
            "peer": master,
            "peer_ip": dev_map[master]["ip"],    
            "peer_interface": m_if,
            "peer_sae": dev_map[master]["sae_id"],
            "role": "slave",
            "interface": s_if,
            "ca_names": [ca_a, ca_b] # same CA this is critical! 
        })

# ----------------------------------------
# BUILD INVENTORY
# ----------------------------------------

def build_inventory(devices_list, pairs, out_dir=CONFIG["runtime_dir"], mode="qkd"):

    devices_yaml = {"devices": {}}

    if mode not in ["static", "qkd"]:
            raise ValueError(f"Invalid mode: {mode}")

    # ✅ generate ONE shared key for all devices (important!) only for static mode
    keys = generate_macsec_keys() if mode == "static" else None

    for dev in devices_list:

        macsec_block = {
            "ca_name": dev.get("ca_name", "CA1"),
            "mode": mode
        }

        # ✅ static only
        if mode == "static":
            macsec_block["ckn"] = keys["ckn"]
            macsec_block["cak"] = keys["cak"]

        ###
        device_entry = {
            "platform": dev["platform"],
            "ip": dev["ip"],
            "auth": dev["auth"],
            "script_user": dev.get("script_user"),
            "ssh_trust": dev.get("ssh_trust"),
            "macsec": macsec_block
        }
        ###

        # ✅ links (core of your new design)
        # this automatically exports also ca_names
        if "links" in dev:
            device_entry["links"] = dev["links"]

        # ✅ qkd mode only
        if mode == "qkd":
            device_entry["qkd"] = {
                "sae_id": dev.get("sae_id", dev["name"])
            }
            device_entry["kme"] = {
                "ip": dev["kme_ip"]
            }

        devices_yaml["devices"][dev["name"]] = device_entry

    topology_yaml = {
        "qkd": {
            "pairs": pairs
        }
    }

    os.makedirs(out_dir, exist_ok=True)

    with open(f"{out_dir}/devices.yaml", "w") as f:
        yaml.dump(devices_yaml, f, sort_keys=False)

    with open(f"{out_dir}/topology.yaml", "w") as f:
        yaml.dump(topology_yaml, f, sort_keys=False)

    print(f"✅ Inventory generated ({mode})")

# ----------------------------------------
# BUILD TOPOLOGY
# ----------------------------------------

def build_pairs(devices, topology_type, hub=None):

    names = [d["name"] for d in devices]
    pairs = []

    if topology_type == "pair":
        if len(names) != 2:
            raise ValueError("Pair topology requires exactly 2 devices")
        pairs.append([names[0], names[1]])

    elif topology_type == "chain":
        for i in range(len(names) - 1):
            pairs.append([names[i], names[i+1]])

    elif topology_type == "ring":
        for i in range(len(names)):
            pairs.append([names[i], names[(i+1) % len(names)]])

    elif topology_type == "hub":
        if not hub:
            raise ValueError("Hub topology requires --hub")

        for n in names:
            if n != hub:
                pairs.append([hub, n])

    else:
        raise ValueError("Invalid topology")

    return pairs


# ----------------------------------------
# BUILD RUNTIME PKI PROFILE
# ----------------------------------------

def build_runtime_pki_profile(profile, out_dir):

    if profile not in ["self_signed", "hierarchical_ca"]:
        raise ValueError(
            f"Invalid PKI profile: {profile}"
        )

    if profile == "self_signed":
        data = {
            "pki": {
                "profile": "self_signed",
                "source_config": "config/pki/self_signed.yml",
                "output_dir": "certs/self_signed",

                "juniper": {
                    "certs_dir": "certs/self_signed",
                    "trust_bundle": "certs/self_signed/offbox_rootCA.crt",
                    "ca_cert": "offbox_rootCA.crt",
                },

                "kme": {
                    "certs_dir": "certs/self_signed/kme",
                    "trust_bundle": "certs/self_signed/offbox_rootCA.crt",
                    "runtime_root_crt": "root.crt",
                },
            }
        }

    elif profile == "hierarchical_ca":
        data = {
            "pki": {
                "profile": "hierarchical_ca",
                "source_config": "config/pki/hierarchical_ca.yml",
                "output_dir": "certs/hierarchical_ca",

                "juniper": {
                    "certs_dir": "certs/hierarchical_ca/juniper_pki/certs",
                    "trust_bundle": (
                        "certs/hierarchical_ca/trust_exchange/"
                        "install_on_juniper/trusted-kme-ca-bundle.crt"
                    ),
                    "ca_cert": "trusted-kme-ca-bundle.crt",
                },

                "kme": {
                    "certs_dir": "certs/hierarchical_ca/kme_pki/certs",
                    "trust_bundle": (
                        "certs/hierarchical_ca/trust_exchange/"
                        "install_on_kme/trusted-juniper-ca-bundle.crt"
                    ),
                    "runtime_root_crt": "root.crt",
                },
            }
        }

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    out_file = out_dir / "pki_profile.yaml"

    with open(out_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            data,
            f,
            sort_keys=False
        )

    print(f"✅ Runtime PKI profile generated ({profile})")
    
def validate_qkd_policy(policy):
    """
    Validate runtime QKD policy values.
    """

    required_keys = [
        "rekey_enabled",
        "interval_seconds",
        "key_batch_size",
        "max_installed_keys",
        "key_ttl_seconds",
        "purge_on_kme_loss",
        "purge_after_seconds",
    ]

    for key in required_keys:
        if key not in policy:
            raise ValueError(f"Missing qkd_policy.{key}")

    if int(policy["interval_seconds"]) < 1:
        raise ValueError("qkd_policy.interval_seconds must be >= 1")

    if int(policy["key_batch_size"]) < 1:
        raise ValueError("qkd_policy.key_batch_size must be >= 1")

    if int(policy["max_installed_keys"]) < 1:
        raise ValueError("qkd_policy.max_installed_keys must be >= 1")

    if int(policy["key_batch_size"]) > int(policy["max_installed_keys"]):
        raise ValueError(
            "qkd_policy.key_batch_size cannot be greater than "
            "qkd_policy.max_installed_keys"
        )

    if int(policy["key_ttl_seconds"]) < 0:
        raise ValueError("qkd_policy.key_ttl_seconds cannot be negative")

    if int(policy["purge_after_seconds"]) < 0:
        raise ValueError("qkd_policy.purge_after_seconds cannot be negative")

    if bool(policy["purge_on_kme_loss"]) and int(policy["purge_after_seconds"]) < 1:
        raise ValueError(
            "qkd_policy.purge_after_seconds must be >= 1 when "
            "qkd_policy.purge_on_kme_loss is true"
        )


def build_runtime_qkd_policy(
    out_dir,
    policy_template,
    rekey_enabled=None,
    interval_seconds=None,
    key_batch_size=None,
    max_installed_keys=None,
    key_ttl_seconds=None,
    purge_on_kme_loss=None,
    purge_after_seconds=None,
):
    """
    Build config/runtime/qkd_policy.yaml from the default policy template.

    Source:
        config/inventory/qkd_policy.yaml

    Destination:
        config/runtime/qkd_policy.yaml

    CLI values override the template only when explicitly provided.
    """

    policy = policy_template.get("qkd_policy", {}).copy()

    if not policy:
        raise ValueError(
            "Missing qkd_policy section in config/inventory/qkd_policy.yaml"
        )

    overrides = {
        "rekey_enabled": rekey_enabled,
        "interval_seconds": interval_seconds,
        "key_batch_size": key_batch_size,
        "max_installed_keys": max_installed_keys,
        "key_ttl_seconds": key_ttl_seconds,
        "purge_on_kme_loss": purge_on_kme_loss,
        "purge_after_seconds": purge_after_seconds,
    }

    for key, value in overrides.items():
        if value is not None:
            policy[key] = value

    validate_qkd_policy(policy)

    runtime_policy = {
        "qkd_policy": policy
    }

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    out_file = out_dir / "qkd_policy.yaml"

    with open(out_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            runtime_policy,
            f,
            sort_keys=False
        )

    print("✅ Runtime QKD policy generated")

    return runtime_policy


# ----------------------------------------
# Wrapper top level builder 
# ----------------------------------------

def build_full_inventory(devices, topology, hub, mode, out_dir, pki_profile="self_signed"):

    pairs = build_pairs(devices, topology, hub)

    assign_links(devices, pairs, topology, hub)

    build_inventory(devices, pairs, out_dir=out_dir, mode=mode)
    
    build_runtime_pki_profile(pki_profile, out_dir)

    return pairs