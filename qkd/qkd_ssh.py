# SSH SCP runtime to send key-id across
import time
import paramiko
from paramiko import SSHException, AuthenticationException
from qkd_runtime import *

def createSSHClient(device, username, password, port=22, retries=3, delay=5, timeout=30):
    """
    Create an SSH connection to a device with retries and timeout.

    Junos bundled Paramiko may not support auth_timeout, so do not use it.
    """
    last_error = None

    for attempt in range(1, retries + 1):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            client.connect(
                hostname=device,
                port=port,
                username=username,
                password=password,
                timeout=timeout,
                banner_timeout=timeout
            )
            return client

        except (SSHException, AuthenticationException, OSError) as e:
            last_error = e
            try:
                client.close()
            except Exception:
                pass

            print(f"SSH connection error to {device}, attempt {attempt}/{retries}: {e}")

            if attempt < retries:
                time.sleep(delay)

    raise SSHException(f"Unable to connect to {device} after {retries} attempts: {last_error}")
