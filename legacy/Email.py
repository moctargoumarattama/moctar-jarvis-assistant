import smtplib
from email.message import EmailMessage

def send_email(receiver_email, subject, body):
    # SMTP Configuration
    smtp_server = 'smtp.gmail.com'
    port = 587
    sender_email = 'yourmailid@gmail.com' #replace your mail id
    password = 'your_generated_app_password'

    # Send email
    try:
        with smtplib.SMTP(smtp_server, port) as server:
            server.starttls()
            server.login(sender_email, password)

            msg = EmailMessage()
            msg.set_content(body)
            msg['Subject'] = subject
            msg['From'] = sender_email
            msg['To'] = receiver_email

            server.send_message(msg)
            print("Email sent successfully!")
    except Exception as e:
        print(f"An error occurred: {e}")
