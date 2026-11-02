"""AdminApiFunction IAM must grant KMS for SSE-KMS ContextWeave bucket uploads."""

from __future__ import annotations

import re
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

_KMS_STATEMENT = re.compile(
    r"- Statement:\n"
    r"            Effect: Allow\n"
    r"            Action:\n"
    r"(?P<actions>(?:              - kms:[^\n]+\n)+)"
    r"            Resource: !Ref ContextWeaveDocsEncryptionKeyArn\n"
)


def _admin_api_function_block() -> str:
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    start = text.index("  AdminApiFunction:")
    end = text.index("\n  GenerateDraftsFunction:", start)
    return text[start:end]


def _contextweave_kms_statement_actions(block: str) -> list[str]:
    matches = list(_KMS_STATEMENT.finditer(block))
    assert len(matches) == 1, (
        "expected exactly one KMS statement on ContextWeaveDocsEncryptionKeyArn, "
        f"found {len(matches)}"
    )
    return re.findall(r"- (kms:\S+)", matches[0].group("actions"))


def test_contextweave_docs_encryption_key_parameter():
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "ContextWeaveDocsEncryptionKeyArn:" in text
    assert (
        "arn:aws:kms:us-east-1:239571291755:key/49eac3db-1f3a-4c91-98ac-61ae722dcaff"
        in text
    )


def test_admin_api_kms_statement_for_contextweave_uploads():
    block = _admin_api_function_block()
    actions = _contextweave_kms_statement_actions(block)
    assert actions == list(KMS_ACTIONS)
