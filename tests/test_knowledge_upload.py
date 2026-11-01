"""ContextWeave finance/health upload routes and validation."""
from __future__ import annotations

import base64
import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("TABLE_NAME", "test-table")
os.environ.setdefault("ARTICLES_BUCKET", "test-bucket")
os.environ.setdefault("CONTEXTWEAVE_FINANCE_BUCKET", "contextweave-finance-test")
os.environ.setdefault("CONTEXTWEAVE_HEALTH_BUCKET", "contextweave-health-test")

import admin_api  # noqa: E402
import knowledge_upload  # noqa: E402


def _call(path, body, *, s3_client=None):
    if s3_client is not None:
        admin_api.s3 = s3_client
    event = {
        "httpMethod": "POST",
        "path": path,
        "headers": {"origin": "https://example.test"},
        "body": json.dumps(body),
    }
    resp = admin_api.lambda_handler(event, None)
    return resp["statusCode"], json.loads(resp["body"] or "null")


def test_parse_upload_accepts_pdf():
    data = b"%PDF-1.4 minimal"
    body = {
        "filename": "doc.pdf",
        "contentType": "application/pdf",
        "contentBase64": base64.b64encode(data).decode("ascii"),
    }
    decoded, ct, name, err = knowledge_upload.parse_upload(body)
    assert err is None
    assert decoded == data
    assert ct == "application/pdf"
    assert name == "doc.pdf"


@pytest.mark.parametrize("ct", ["image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp", "text/plain"])
def test_parse_upload_accepts_allowed_types(ct):
    data = b"x"
    _, out_ct, _, err = knowledge_upload.parse_upload({
        "filename": "f.bin",
        "contentType": ct,
        "contentBase64": base64.b64encode(data).decode("ascii"),
    })
    assert err is None
    assert out_ct == ct


def test_parse_upload_normalises_image_jpg():
    _, ct, _, err = knowledge_upload.parse_upload({
        "filename": "photo.jpg",
        "contentType": "image/jpg",
        "contentBase64": base64.b64encode(b"abc").decode("ascii"),
    })
    assert err is None
    assert ct == "image/jpeg"


@pytest.mark.parametrize("body,msg", [
    ({}, "filename required"),
    ({"filename": "a.pdf", "contentType": "application/pdf"}, "contentBase64 required"),
    ({"filename": "a.pdf", "contentType": "application/zip", "contentBase64": "YQ=="}, "contentType not allowed"),
    ({"filename": "../evil.pdf", "contentType": "application/pdf", "contentBase64": "YQ=="}, "filename required"),
    ({"filename": "a.pdf", "contentType": "application/pdf", "contentBase64": "!!!"}, "not valid base64"),
])
def test_parse_upload_rejects_bad_input(body, msg):
    _, _, _, err = knowledge_upload.parse_upload(body)
    assert err and msg in err


def test_build_object_key_uses_upload_prefix():
    fixed = datetime(2026, 3, 5, 12, 0, tzinfo=timezone.utc)
    key = knowledge_upload.build_object_key("scan.pdf", now=fixed)
    assert key.startswith("uploads/2026/03/")
    assert key.endswith("-scan.pdf")


def test_finance_upload_puts_object_in_configured_bucket():
    mock_s3 = MagicMock()
    mock_s3.put_object.return_value = {"ETag": '"abc123"'}
    payload = b"hello"
    code, body = _call(
        "/admin/knowledge/finance/upload",
        {
            "filename": "note.txt",
            "contentType": "text/plain",
            "contentBase64": base64.b64encode(payload).decode("ascii"),
        },
        s3_client=mock_s3,
    )
    assert code == 201
    assert body["bucket"] == "contextweave-finance-test"
    assert body["key"].startswith("uploads/")
    assert body["key"].endswith("-note.txt")
    assert body["etag"] == "abc123"
    mock_s3.put_object.assert_called_once()
    kwargs = mock_s3.put_object.call_args.kwargs
    assert kwargs["Bucket"] == "contextweave-finance-test"
    assert kwargs["Body"] == payload
    assert kwargs["ContentType"] == "text/plain"


def test_health_upload_uses_health_bucket():
    mock_s3 = MagicMock()
    mock_s3.put_object.return_value = {"ETag": '"x"'}
    code, body = _call(
        "/admin/knowledge/health/upload",
        {
            "filename": "labs.png",
            "contentType": "image/png",
            "contentBase64": base64.b64encode(b"\x89PNG").decode("ascii"),
        },
        s3_client=mock_s3,
    )
    assert code == 201
    assert body["bucket"] == "contextweave-health-test"
    assert mock_s3.put_object.call_args.kwargs["Bucket"] == "contextweave-health-test"


def test_upload_returns_500_when_bucket_env_missing(monkeypatch):
    monkeypatch.delenv("CONTEXTWEAVE_FINANCE_BUCKET", raising=False)
    code, body = _call(
        "/admin/knowledge/finance/upload",
        {
            "filename": "a.pdf",
            "contentType": "application/pdf",
            "contentBase64": "YQ==",
        },
        s3_client=MagicMock(),
    )
    assert code == 500
    assert "not configured" in body["error"]
