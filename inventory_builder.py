import yaml
import os
import secrets

from settings import CONFIG


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
# Wrapper top level builder 
# ----------------------------------------

def build_full_inventory(devices, topology, hub, mode, out_dir):

    pairs = build_pairs(devices, topology, hub)

    assign_links(devices, pairs, topology, hub)

    build_inventory(devices, pairs, out_dir=out_dir, mode=mode)

    return pairs