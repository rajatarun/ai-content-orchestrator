"""Keep openapi/content-orchestrator.yaml honest against the code and the stack.

The documented endpoint table in CLAUDE.md listed four routes and described
`/admin` as "List / create articles". The handler serves thirteen paths, and
articles are under `/admin/articles`. That table was wrong for as long as it
existed because nothing ever compared it to the code -- which is the failure
mode a second hand-written description of an API invites.

So the spec is not checked by reading it. It is checked against two things
that cannot lie:

* the route literals in ``src/*.py`` -- what the Lambda handlers actually
  dispatch on, in both directions, so neither an undocumented route nor a
  documented-but-nonexistent one survives;
* ``template.yaml`` -- API Gateway has to route a path before a handler can
  see it, and the ``Outputs`` a harness resolves its coordinates from.

None of it needs AWS or a deployed stack.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = REPO_ROOT / "openapi" / "content-orchestrator.yaml"
TEMPLATE_PATH = REPO_ROOT / "template.yaml"
SRC = REPO_ROOT / "src"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import stack_env  # noqa: E402

HTTP_METHODS = ("get", "post", "patch", "put", "delete")


@pytest.fixture(scope="module")
def spec() -> dict:
    with open(SPEC_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _handler_routes() -> tuple[set, set]:
    """Route literals the handlers dispatch on: (exact matches, prefixes).

    The handlers are uniform about this -- every route is either
    ``path == "/literal"`` or ``path.startswith("/literal/")`` -- so reading
    the literals out of the source is exact, not a heuristic. If that style
    changes, this returns fewer routes and the round-trip test below fails
    loudly rather than quietly checking nothing.
    """
    exact, prefixes = set(), set()
    for py in sorted(SRC.glob("*.py")):
        text = py.read_text(encoding="utf-8")
        exact |= set(re.findall(r'path\s*==\s*"(/[^"]*)"', text))
        exact |= set(re.findall(r'path\s*!=\s*"(/[^"]*)"', text))
        prefixes |= set(re.findall(r'path\.startswith\(\s*"(/[^"]*)"', text))
    return exact, prefixes


def _spec_operations(spec: dict) -> list:
    return [(p, m) for p, item in spec["paths"].items()
            for m in item if m in HTTP_METHODS]


def test_route_literals_were_actually_found():
    """Guard the guard: an empty extraction would make every test below vacuous."""
    exact, prefixes = _handler_routes()
    assert len(exact) >= 8, f"only found {sorted(exact)} -- the extraction regex has gone stale"
    assert len(prefixes) >= 3, f"only found {sorted(prefixes)} -- the extraction regex has gone stale"


def test_every_documented_path_is_served_by_a_handler(spec):
    """A path in the spec that no handler dispatches on is a 404 with docs."""
    exact, prefixes = _handler_routes()
    for path in spec["paths"]:
        if "{" not in path:
            assert path in exact, (
                f"{path} is documented but no handler has `path == \"{path}\"`. "
                f"Handler literals: {sorted(exact)}"
            )
        else:
            stem = path[:path.index("{")]  # "/admin/articles/{id}/events" -> "/admin/articles/"
            assert stem in prefixes, (
                f"{path} is documented but no handler has "
                f"`path.startswith(\"{stem}\")`. Handler prefixes: {sorted(prefixes)}"
            )


def test_every_handler_route_is_documented(spec):
    """The other direction: an undocumented route is one no client will call."""
    exact, prefixes = _handler_routes()
    documented = set(spec["paths"])
    documented_stems = {p[:p.index("{")] for p in documented if "{" in p}

    for literal in exact:
        assert literal in documented, (
            f"src/ dispatches on {literal} but openapi/ does not document it"
        )
    for prefix in prefixes:
        assert prefix in documented_stems, (
            f"src/ dispatches on paths under {prefix} but openapi/ documents "
            f"none of them (documented prefixes: {sorted(documented_stems)})"
        )


def _template_api_paths() -> set:
    return set(re.findall(r"^\s+Path:\s*(\S+)\s*$", TEMPLATE_PATH.read_text(encoding="utf-8"), re.M))


def test_api_gateway_routes_every_documented_path(spec):
    """API Gateway has to route it before the handler ever sees it.

    /admin/{proxy+} is a greedy catch-all, so every /admin subpath is covered
    by it; everything else has to be named explicitly in template.yaml.
    """
    template_paths = _template_api_paths()
    assert template_paths, "parsed no Path: entries from template.yaml"
    has_admin_proxy = "/admin/{proxy+}" in template_paths
    normalised = {re.sub(r"\{[^}]+\}", "{}", p) for p in template_paths}

    for path in spec["paths"]:
        if path.startswith("/admin/") and has_admin_proxy:
            continue
        assert re.sub(r"\{[^}]+\}", "{}", path) in normalised, (
            f"{path} is documented but API Gateway does not route it -- no "
            f"matching Path: in template.yaml ({sorted(template_paths)})"
        )


def test_admin_operations_require_both_credentials(spec):
    """Admin routes inherit the API's key + authorizer; /public and /site opt out.

    Getting this backwards in the spec is not cosmetic: a generated client that
    omits the credentials gets a 403 it has no reason to expect, and one that
    sends a bearer token to /public/subscribe leaks it to an endpoint that
    never needed it.
    """
    default = spec.get("security")
    assert default, "the spec declares no default security scheme"
    for path, method in _spec_operations(spec):
        op = spec["paths"][path][method]
        if path.startswith("/admin"):
            assert "security" not in op, (
                f"{method.upper()} {path} overrides the default security; admin "
                f"routes inherit ApiKeyRequired + the SIWE authorizer"
            )
        else:
            assert op.get("security") == [], (
                f"{method.upper()} {path} is a public route but does not declare "
                f"`security: []`, so a generated client will demand credentials "
                f"the endpoint does not take"
            )


def _template_output_names() -> set:
    """Top-level keys of template.yaml's Outputs block.

    Regex rather than yaml.safe_load: the template is full of !Sub/!Ref tags a
    safe loader rejects, and a permissive loader that maps unknown tags to None
    reports every !Sub-valued field as absent -- which is how a check like this
    ends up asserting nothing.
    """
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    block = text.split("\nOutputs:\n", 1)
    assert len(block) == 2, "template.yaml has no top-level Outputs: block"
    names = set()
    for line in block[1].splitlines():
        if line and not line.startswith(" ") and not line.startswith("#"):
            break
        m = re.match(r"^  ([A-Za-z][A-Za-z0-9]*):\s*$", line)
        if m:
            names.add(m.group(1))
    return names


def test_stack_env_reads_only_outputs_the_template_publishes():
    published = _template_output_names()
    assert published, "parsed no outputs from template.yaml -- the parser is broken"
    wanted = set(stack_env.REQUIRED_OUTPUTS.values()) | set(stack_env.OPTIONAL_OUTPUTS.values())
    missing = sorted(wanted - published)
    assert not missing, (
        f"scripts/stack_env.py reads stack outputs that template.yaml does not "
        f"publish: {missing}. Either add the Output or stop reading it."
    )


def test_required_outputs_cover_the_api_and_the_table_indexes():
    """The table name alone is not enough to query a table keyed by its GSIs."""
    required = set(stack_env.REQUIRED_OUTPUTS.values())
    for needed in ("ApiBaseUrl", "ContentTableName", "ContentTableStatusUpdatedIndex",
                   "ContentTableStatusPublishedIndex", "ArticlesBucketName", "ArticlesPrefix"):
        assert needed in required, f"{needed} is not resolved from the stack"


def test_gsi_outputs_name_indexes_the_table_actually_defines():
    """An output naming a nonexistent index is worse than no output at all."""
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    defined = set(re.findall(r"^\s+- IndexName:\s*(\S+)\s*$", text, re.M))
    assert defined, "parsed no IndexName entries from template.yaml"
    for output in ("ContentTableStatusUpdatedIndex", "ContentTableStatusPublishedIndex"):
        m = re.search(rf"^  {output}:\n(?:.*\n)*?^    Value:\s*(\S+)\s*$", text, re.M)
        assert m, f"template.yaml has no {output} output with a literal Value"
        assert m.group(1) in defined, (
            f"{output} names index {m.group(1)!r}, which the table does not "
            f"define (defined: {sorted(defined)})"
        )


def test_no_hardcoded_articles_bucket_left_in_the_resources():
    """The bucket was a literal with the account id in it, in five places.

    A literal like that cannot be discovered by anything outside the file and
    makes the stack undeployable in a second account. It is a parameter now,
    with the same value as its default.
    """
    resources = TEMPLATE_PATH.read_text(encoding="utf-8").split("\nResources:\n", 1)[1]
    assert "tarun-rag-docs-239571291755" not in resources, (
        "a hardcoded articles bucket is back in Resources; reference the "
        "ArticlesBucket parameter instead"
    )


def test_openapi_spec_path_output_points_at_the_spec():
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    m = re.search(r"^  OpenApiSpecPath:\n(?:.*\n)*?^    Value:\s*(\S+)\s*$", text, re.M)
    assert m, "template.yaml has no OpenApiSpecPath output with a literal Value"
    assert (REPO_ROOT / m.group(1)).is_file(), (
        f"OpenApiSpecPath points at {m.group(1)}, which does not exist"
    )
