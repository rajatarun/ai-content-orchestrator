"""AdminApiFunction IAM must grant KMS for SSE-KMS ContextWeave bucket uploads."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = REPO_ROOT / "template.yaml"

KMS_ACTIONS = (
    "kms:Encrypt",
    "kms:Decrypt",
    "kms:GenerateDataKey",
    "kms:GenerateDataKeyWithoutPlaintext",
    "kms:DescribeKey",
)


def _admin_api_function_block() -> str:
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    start = text.index("  AdminApiFunction:")
    end = text.index("\n  GenerateDraftsFunction:", start)
    return text[start:end]


def test_contextweave_docs_encryption_key_parameter():
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "ContextWeaveDocsEncryptionKeyArn:" in text
    assert (
        "arn:aws:kms:us-east-1:239571291755:key/49eac3db-1f3a-4c91-98ac-61ae722dcaff"
        in text
    )


def test_admin_api_kms_statement_for_contextweave_uploads():
    block = _admin_api_function_block()
    assert "Resource: !Ref ContextWeaveDocsEncryptionKeyArn" in block
    for action in KMS_ACTIONS:
        assert action in block, f"missing {action} on AdminApiFunction KMS statement"
    assert block.count("kms:GenerateDataKey") == 1
