import boto3
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from logger import get_logger

log = get_logger("mailer")
_ses = boto3.client("ses")

def send_email(from_email: str, to_emails: list, subject: str, body_text: str, body_html: str = None) -> None:
    log.info("Sending email via SES", extra={"from": from_email, "to_count": len(to_emails), "subject": subject[:120]})
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = ", ".join(to_emails)

    msg.attach(MIMEText(body_text or "", "plain", "utf-8"))
    if body_html:
        msg.attach(MIMEText(body_html, "html", "utf-8"))

    resp = _ses.send_raw_email(Source=from_email, Destinations=to_emails, RawMessage={"Data": msg.as_string()})
    log.info("SES send_raw_email ok", extra={"message_id": resp.get("MessageId")})
