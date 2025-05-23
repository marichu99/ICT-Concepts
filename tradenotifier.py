import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import time
from dotenv import load_dotenv
import os

load_dotenv()

# Email configuration
EMAIL_ADDRESS = "marichufx@gmail.com"
EMAIL_PASSWORD = os.getenv("APP_PASSWORD")
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
NOTIFY_EMAIL = "martinmaati31@gmail.com"

# Notification function
def send_email_notification(subject, body):
    try:
        # Set up the MIME
        msg = MIMEMultipart()
        msg["From"] = EMAIL_ADDRESS
        msg["To"] = NOTIFY_EMAIL
        msg["Subject"] = subject

        # Attach the email body
        msg.attach(MIMEText(body, "plain"))

        # Connect to the server
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.send_message(msg)
        print("Notification email sent successfully.")
    except Exception as e:
        print(f"Error sending email: {e}")





