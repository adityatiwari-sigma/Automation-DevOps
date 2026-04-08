import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def get_env_var(var_name):
    env_path = "/home/adityatiwari/Documents/AOPS/.env"
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                if line.startswith(f"{var_name}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get(var_name)

sender_email = get_env_var("GMAIL_ADDRESS")
sender_password = get_env_var("GMAIL_APP_PASSWORD")

print(f"Testing with: {sender_email}")

msg = MIMEMultipart()
msg['From'] = f"AOPS Test <{sender_email}>"
msg['To'] = sender_email
msg['Subject'] = "AOPS Webhook Test Email"
msg.attach(MIMEText("This is a direct test of the smtplib configuration from AOPS.", 'plain'))

try:
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender_email, sender_password)
        server.send_message(msg)
    print("SUCCESS: Email sent!")
except Exception as e:
    print(f"FAILED: {e}")
