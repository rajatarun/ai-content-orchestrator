import os
import json
import boto3
from logger import get_logger

log = get_logger("publisher")
s3 = boto3.client("s3")

def publish_article_to_s3(article: dict) -> dict:
    bucket = os.environ.get("ARTICLES_BUCKET")
    prefix = os.environ.get("ARTICLES_PREFIX", "docs/articles")

    if not bucket:
        raise RuntimeError("ARTICLES_BUCKET not set")

    article_id = article.get("id")
    if not article_id:
        raise RuntimeError("Article missing id")

    key = f"{prefix}/{article_id}.json"

    body = json.dumps(article, ensure_ascii=False, default=str).encode("utf-8")

    log.info("Uploading article JSON to S3", extra={
        "bucket": bucket,
        "key": key,
        "bytes": len(body)
    })

    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json"
    )

    return {"bucket": bucket, "key": key}