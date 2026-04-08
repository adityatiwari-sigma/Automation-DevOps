import paramiko
import sys
import os

if len(sys.argv) < 6:
    print("Usage: ssh_helper.py <ip> <user> <ssh_pw> <sudo_pw> <command>")
    sys.exit(1)

ip = sys.argv[1]
user = sys.argv[2]
ssh_pw = sys.argv[3]
sudo_pw = sys.argv[4]
command = sys.argv[5]

try:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    # Connect
    if ssh_pw and ssh_pw != "YOUR_SSH_PASSWORD":
        client.connect(ip, username=user, password=ssh_pw, timeout=10)
    else:
        # Fallback to key auth if no password provided
        client.connect(ip, username=user, timeout=10)
        
    if sudo_pw and sudo_pw != "YOUR_SUDO_PASSWORD_IF_NEEDED" and sudo_pw != "":
        # Escape double quotes in the command for bash -c
        escaped_command = command.replace('"', '\\"')
        full_cmd = f"echo '{sudo_pw}' | sudo -S bash -c \"{escaped_command}\""
    else:
        full_cmd = command
    
    stdin, stdout, stderr = client.exec_command(full_cmd)
    
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8').strip()
    err = stderr.read().decode('utf-8').strip()
    
    if out:
        print(out)
    if err and "sudo: a terminal is required" not in err:
        print(err, file=sys.stderr)
        
    sys.exit(exit_status)
    
except Exception as e:
    print(f"SSH Exception: {e}", file=sys.stderr)
    sys.exit(1)
finally:
    client.close()
