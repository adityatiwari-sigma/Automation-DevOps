"""
ssh_helper.py — executes a single command on the remote server via SSH.

Usage:  python3 ssh_helper.py "<shell command>"
        python3 ssh_helper.py "systemctl reload php8.4-fpm"

Credentials are read from config.json — never passed as command-line arguments.
"""

import sys
import os

import paramiko

# Reach config_loader from the remediate/ subdirectory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
import config_loader


def run_remote(command: str) -> int:
    cfg        = config_loader.load()
    ip         = cfg["network"]["remote_ip"]
    user       = cfg["ssh"]["user"]
    ssh_pw     = cfg["ssh"].get("password", "")
    sudo_pw    = cfg["ssh"].get("sudo_password", "")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        connect_kwargs = {"username": user, "timeout": 15}
        # Ignore password if it looks like an SSH key
        if ssh_pw and not ssh_pw.startswith(("ssh-rsa", "AAAAB3Nza", "ecdsa-sha2-nistp256")):
            connect_kwargs["password"] = ssh_pw
        else:
            # Explicitly load key if password is empty or invalid
            key_path = os.path.expanduser("~/.ssh/id_rsa")
            if os.path.exists(key_path):
                connect_kwargs["key_filename"] = key_path
                
        client.connect(ip, **connect_kwargs)

        if sudo_pw:
            # Wrap in sudo -S; escape single quotes in the command
            safe_cmd = command.replace("'", "'\\''")
            full_cmd = f"echo '{sudo_pw}' | sudo -S bash -c '{safe_cmd}'"
        else:
            full_cmd = command

        _, stdout, stderr = client.exec_command(full_cmd)
        exit_status = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace").strip()
        err = stderr.read().decode("utf-8", errors="replace").strip()

        if out:
            print(out)
        if err and "sudo: a terminal is required" not in err:
            print(err, file=sys.stderr)

        return exit_status

    except Exception as exc:
        print(f"SSH error ({ip}): {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: ssh_helper.py <command>", file=sys.stderr)
        sys.exit(1)
    sys.exit(run_remote(sys.argv[1]))
