import os
import datetime as dt
from logger import get_logger
from db import list_published_between, list_subscribers
from gemini_client import gemini_generate_json
from mailer import send_email

log = get_logger("newsletter")

def newsletter_handler(event, context):
    action = (event or {}).get("action") or "generate"
    log.info("newsletter_handler", extra={"action": action})

    if action == "generate":
        date_from = (event or {}).get("from") or (dt.date.today() - dt.timedelta(days=7)).isoformat()
        date_to = (event or {}).get("to") or dt.date.today().isoformat()

        posts = list_published_between(date_from, date_to, limit=50)
        log.info("newsletter_posts", extra={"count": len(posts), "from": date_from, "to": date_to})

        prompt = {
            "task": "weekly_newsletter",
            "instructions": [
                "Write a concise, skimmable weekly newsletter summarizing the posts.",
                "Return JSON only."
            ],
            "context": {
                "week_start": date_from,
                "week_end": date_to,
                "posts": [{"title": p.get("title",""), "url": p.get("publishedUrl","")} for p in posts]
            },
            "schema": {"subject":"string","body_text":"string","body_html":"string"}
        }

        result = gemini_generate_json(prompt, use_search=False)
        log.info("newsletter_generated", extra={"subject": (result.get("subject","")[:120])})
        return {"ok": True, "from": date_from, "to": date_to, "preview": result}

    if action == "send":
        from_email = os.environ.get("SES_FROM_EMAIL","")
        subject = (event or {}).get("subject","Weekly roundup")
        body_text = (event or {}).get("body_text","")
        body_html = (event or {}).get("body_html","")

        subs = [s.get("email") for s in list_subscribers() if s.get("email")]
        log.info("newsletter_send_request", extra={"from": from_email, "subs": len(subs), "subject": subject[:120]})

        if not from_email:
            return {"ok": False, "error": "SES_FROM_EMAIL not set"}
        if not subs:
            return {"ok": False, "error": "No subscribers"}

        send_email(from_email, subs, subject, body_text, body_html)
        return {"ok": True, "sent": len(subs)}

    return {"ok": False, "error": "Unknown action"}
