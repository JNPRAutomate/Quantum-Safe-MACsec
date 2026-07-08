#!/usr/bin/env python3

"""
Macsec with keys from QKD

This script allows the JUNOS device to fetch
keys from KME and update the MACSEC CAK accordingly.

This is an event script and should be kept in "/var/db/scripts/events/qkd.py"
This script can be scheduled using event-options config in JUNOS.
Currently the user is harcoded as "lab".

Copyright 2025 Juniper Networks, Inc. All rights reserved.
Licensed under the Juniper Networks Script Software License (the "License").
You may not use this script file except in compliance with the License, which
is located at
http://www.juniper.net/support/legal/scriptlicense/
Unless required by applicable law or otherwise agreed to in writing by the
parties, software distributed under the License is distributed on an "AS IS"
BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

Date: 2026-07-07
Version: 3.2.2

"""
__version__ = "v3.2.2"
import warnings
warnings.filterwarnings("ignore",category=DeprecationWarning)
warnings.filterwarnings("ignore",message=".*TripleDES.*")

from qkd_process import process

from qkd_config import check_and_apply_initial_config

from qkd_logging import initialize_logging

from qkd_targets import load_targets

from qkd_certs import (
    renew_certificates,
    should_check_certs,
)

from threading import Thread
from jnpr.junos import Device
import argparse
from qkd_runtime import *

threads = []

# Decorator func for threading
def background(func):
    def bg_func(*args, **kwargs):
        t = Thread(target=func, args=args, kwargs=kwargs)
        t.daemon=True
        t.start()
        threads.append(t)
    return bg_func


@background
def req_thread(tnum, reqs, targets_dict, log):
    for device in reqs:
        print(device)
        with Device(host=targets_dict[device]['ip'], user=targets_dict["secrets"]["username"], password=targets_dict["secrets"]["password"], port=22) as dev:
            if should_check_certs():
                renew_certificates(dev, log, targets_dict=targets_dict)
            
            log.debug("Running initial configuration check")
            check_and_apply_initial_config(dev, targets_dict, log)
            log.debug("Starting QKD process")
            process(dev, targets_dict, log)
            log.debug("QKD processing completed")


def get_args():
    """
    Defines and parses command-line arguments for the script.
    
    Returns:
        argparse.Namespace: Parsed command-line arguments
    """
    parser = argparse.ArgumentParser(
        description="Script to configure devices with QKD MACSEC and fetch keys from KME."
    )

    # Number of threads to run in parallel
    parser.add_argument(
        "-t", "--threads",
        type=int,
        default=1,
        help="Number of threads to use for processing devices (default: 1)."
    )

    # Verbosity level: can be increased with multiple -v flags
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="Increase logging verbosity. Use -v, -vv, -vvv for more detailed logs."
    )

    # Trace flag: output debug-level logs to a file
    parser.add_argument(
        "-tr", "--trace",
        action="store_true",
        help="Enable trace logging and dump debug-level logs to trace.log file."
    )

    return parser.parse_args()

def main():

    args = get_args()

    log = initialize_logging(args)

    targets_dict = load_targets()
    
    if not ONBOX:
        log.info("Offbox approach taken")
        # execute the process function for all the devices on multiple threads
        #dlist = [d for d in targets_dict["qkd_roles"]["additional_slave_SAE_IDs"]]
        dlist = list(targets_dict["qkd_roles"].get("additional_slave_SAE_IDs",[]))
        dlist.insert(0, targets_dict["qkd_roles"]["slave"])
        dlist.insert(0, targets_dict["qkd_roles"]["master"])
        # Calculate reqs per thread and launch threads
        if args.threads:
            maxthreads = args.threads
        else:
            maxthreads = targets_dict["system"]["maxthreads"]
        rpt = int(len(dlist) / maxthreads)
        if len(dlist) % maxthreads > 0:
            rpt += 1
        n = 0
        while dlist:
            sreqs = dlist[:rpt]
            dlist = dlist[rpt:]
            req_thread(n, sreqs, targets_dict, log)
            n += 1
        log.info("Waiting on {0} threads".format(len(threads)))
        for t in threads:
            t.join()
        
    elif ONBOX:
        log.info("Onbox approach taken")
        try:
            with Device() as dev:
                check_and_apply_initial_config(dev, targets_dict, log)
                process(dev, targets_dict, log)
        except Exception as e:
            log.error(f"Failed to process host: {str(e)}")

if __name__ == '__main__':
    main()
