"""Site-wide settings the resume website reads at runtime.

The website (rajatarun/resume) is a static export: anything decided at build
time is baked into its HTML until the next deploy. These settings are the
exception -- the admin UI writes them through ``PATCH /admin/settings`` and the
homepage reads them through ``GET /site/settings`` on every load, so a change
is live on the next page view with no rebuild.

Today there is one: ``homeVariant``, which homepage design to show. The design
names themselves live in the website (``lib/featureFlags.ts``, alongside the
components they select), so this only checks the *shape* of a name; the
website falls back to its default for a name it does not know.

Stored as a single ContentTable item, ``pk="SETTINGS"``, ``sk="site"``.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

SETTINGS_KEY = {"pk": "SETTINGS", "sk": "site"}

# What the admin UI may change. Anything else in a PATCH body is refused
# rather than ignored, so a typo in a field name is a 400, not a silent no-op.
EDITABLE_FIELDS = ("homeVariant",)

# A design name: lowercase, starts with a letter, letters/digits/hyphens.
DESIGN_NAME = re.compile(r"^[a-z][a-z0-9-]{0,31}$")

# What GET /site/settings exposes. updatedBy (a wallet address) stays admin-only.
PUBLIC_FIELDS = ("homeVariant", "updatedAt")


def validate_patch(body: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return ``(patch, None)`` for a usable body, or ``(None, error)``."""
    if not isinstance(body, dict) or not body:
        return None, "body must be a JSON object with at least one setting"

    unknown = sorted(set(body) - set(EDITABLE_FIELDS))
    if unknown:
        return None, f"unknown setting(s): {', '.join(unknown)}; editable: {', '.join(EDITABLE_FIELDS)}"

    patch: Dict[str, Any] = {}
    if "homeVariant" in body:
        value = body["homeVariant"]
        if not isinstance(value, str) or not DESIGN_NAME.match(value.strip().lower()):
            return None, "homeVariant must be a design name: lowercase letters, digits and hyphens"
        patch["homeVariant"] = value.strip().lower()

    return patch, None


def public_view(item: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The settings as the website sees them. Every field present, null when unset."""
    item = item or {}
    return {field: item.get(field) for field in PUBLIC_FIELDS}


def admin_view(item: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    view = public_view(item)
    view["updatedBy"] = (item or {}).get("updatedBy")
    return view


def caller_identity(event: Dict[str, Any]) -> Optional[str]:
    """Who made an admin request, as the SIWE authorizer reported it (best effort)."""
    authorizer = (event.get("requestContext") or {}).get("authorizer") or {}
    for key in ("principalId", "address", "sub"):
        value = authorizer.get(key)
        if isinstance(value, str) and value:
            return value
    return None
