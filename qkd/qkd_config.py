# initial macsec provisioning

import time

from jnpr.junos.utils.config import Config
from lxml.builder import E
from qkd_runtime import *

def check_and_apply_initial_config(dev, targets_dict, log):
    """
    Check if the initial macsec configuration is already applied and 
    apply it on the device if not.
    This function will be trigerred only one time on the 1st script run.
    """
    device_name= dev.facts['hostname'].split('-re')[0]
    device_ip = targets_dict[device_name]["ip"]
    c_a= targets_dict["CA_server"]["c_a"] # connectivity-association
    interfaces = targets_dict[device_name]["interfaces"]
    kme_name = targets_dict[device_name]["kme"]["kme_name"]
    kme_port = targets_dict[device_name]["kme"]["kme_port"] 
    start_time = targets_dict["system"]["event_options"]["start_time"]

    # Check if configuration is already present on device
    config_check = dev.rpc.get_config(filter_xml=E.configuration(E.security(E.macsec(E('connectivity-association', E.name(c_a))))))
    if config_check.find('.//name') is not None:
        log.info("Initial macsec configuration already applied on the device: {}.".format(device_name))
        return

    # defining the list of commands to be applied for the initial macsec configuration
    initial_macsec_commands = [
        f"set security macsec connectivity-association {c_a} cipher-suite gcm-aes-xpn-256",
        f"set security macsec connectivity-association {c_a} security-mode static-cak",
        f"set security macsec connectivity-association {c_a} pre-shared-key ckn abcd1234abcd5678abcd1234abcd5678abcd1234abcd5678abcd1234abcd5678",
        f"set security macsec connectivity-association {c_a} pre-shared-key cak abcd1234abcd5678abcd1234abcd5678abcd1234abcd5678abcd1234abcd5678"
    ]
    master_name = targets_dict["qkd_roles"]["master"]
    slave_name = targets_dict["qkd_roles"]["slave"]

    initial_macsec_commands.append(
        f"set system static-host-mapping {master_name} inet {targets_dict[master_name]['ip']}"
    )
    initial_macsec_commands.append(
        f"set system static-host-mapping {slave_name} inet {targets_dict[slave_name]['ip']}"
    )
       
    for interface in interfaces:
        initial_macsec_commands.extend([
            f"set security macsec interfaces {interface} apply-macro qkd kme-ca false",
            f"set security macsec interfaces {interface} apply-macro qkd kme-host {kme_name}",
            f"set security macsec interfaces {interface} apply-macro qkd kme-port {kme_port}",
            f"set security macsec interfaces {interface} connectivity-association {c_a}",
            f"set security macsec interfaces {interface} apply-macro qkd kme-keyid-check true",
        ])

    if ONBOX:
        initial_macsec_commands.extend([
            # even-options configuration
            f"set event-options generate-event every10mins time-interval 600 start-time {start_time}",
            f"set event-options policy qkd events every10mins",
            f"set event-options policy qkd then event-script qkd.py",
            f"set event-options event-script file qkd.py python-script-user admin",
            f"set event-options traceoptions file script.log",
            f"set event-options traceoptions file size 10m",
        ])

    log.info("Applying initial macsec configuration on the device: {}.".format(device_name))
    try:
        dev.timeout = 300
        with Config(dev) as cu:
            cu.lock()
            for command in initial_macsec_commands:
                cu.load(command, format='set')
            cu.commit()
            log.info("Initial macsec configuration applied successfully on the device: {}.".format(device_name))
    except Exception as e:
        log.error(f'Initial macsec configuration commit failed: {e}')
    finally:
        try:
            cu.unlock()
        except Exception:
            pass

        time.sleep(60)
