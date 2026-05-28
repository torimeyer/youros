"""
Identity consistency tests.

These tests make sure that instance-specific strings (like "torios" or
hardcoded "yourOS" used as a role label) do not sneak back into source code.

The product name "yourOS" is allowed in product-description text.  What is
NOT allowed is:
  - "Loading torios" (a specific user's instance name baked into a loading
    screen instead of reading instanceName from the store)
  - Using a raw string literal "yourOS" as the role label in a chat/transcript
    component (e.g. `role === "user" ? "You" : "yourOS"`) instead of the
    store value
  - Backend services using "torios" as a hardcoded default for instance_name

These patterns are caught by regex scan over the relevant source trees.
"""

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _frontend_src() -> Path:
    return REPO_ROOT / "app" / "src"


def _api_root() -> Path:
    return REPO_ROOT / "api"


def _source_files(
    root: Path,
    suffixes: tuple,
    exclude_substrings: list,
) -> list:
    """Return all files under *root* with matching suffix, skipping exclusions."""
    result = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in suffixes:
            continue
        rel = str(path.relative_to(root))
        if any(sub in rel for sub in exclude_substrings):
            continue
        result.append(path)
    return result


def _scan(files, pattern):
    """Return (file, line_number, line_text) for every match."""
    hits = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                hits.append((path, lineno, line.rstrip()))
    return hits


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

class TestNoHardcodedInstanceName:
    """Ban strings that embed a specific user's instance name in production code."""

    def test_no_loading_torios_in_frontend(self):
        """'Loading torios...' must not appear in the loading screen.
        The loading text must use the instanceName store value, not a
        hardcoded string.  This test looks for "Loading torios" (case-
        insensitive) which was the exact regression fixed in commit 2.
        """
        src = _frontend_src()
        if not src.exists():
            return  # running outside repo, skip

        files = _source_files(
            src,
            suffixes=(".tsx", ".ts"),
            exclude_substrings=["node_modules", ".test.", ".spec."],
        )

        # Match "Loading torios" literally - "torios" is an instance name,
        # not a product name, so it must never be hardcoded in UI strings.
        pattern = re.compile(r"Loading\s+torios", re.IGNORECASE)
        hits = _scan(files, pattern)

        assert hits == [], (
            "Found hardcoded 'Loading torios' in frontend source. "
            "Use instanceName from the app store instead.\n"
            + "\n".join("  {}:{}: {}".format(p, n, l) for p, n, l in hits)
        )

    def test_no_hardcoded_role_label_myos(self):
        """The chat/transcript role label must not be a hardcoded brand string.
        It must read from the instanceName store.

        Catches the pattern: {msg.role === "user" ? "You" : "yourOS"}
        (or the old "myOS" form) which would bake a brand name into JSX
        instead of reading from the store.
        """
        src = _frontend_src()
        if not src.exists():
            return

        files = _source_files(
            src,
            suffixes=(".tsx", ".ts"),
            exclude_substrings=["node_modules", ".test.", ".spec."],
        )

        # Pattern: colon followed by brand name in double quotes (ternary role label).
        pattern = re.compile(r':\s*"(myOS|yourOS)"')
        hits = _scan(files, pattern)

        assert hits == [], (
            "Found hardcoded brand name used as a role label in frontend source. "
            "Use instanceName from the app store instead.\n"
            + "\n".join("  {}:{}: {}".format(p, n, l) for p, n, l in hits)
        )

    def test_no_torios_as_instance_name_default_in_services(self):
        """Backend services must not use 'torios' as a hardcoded default for
        instance_name.  The only correct default is 'yourOS'.

        This test is deliberately narrow: it only checks service files for
        the pattern  instance_name ... "torios"  on the same line, which
        would indicate the wrong fallback is being used.

        Broader occurrences of 'torios' in comments, docstrings, URLs,
        label maps, and test fixtures are expected and not scanned here.
        """
        services_root = _api_root() / "services"
        if not services_root.exists():
            return

        files = _source_files(
            services_root,
            suffixes=(".py",),
            exclude_substrings=[".venv", "__pycache__"],
        )

        # Look for instance_name being assigned the literal "torios" string.
        # e.g.: instance_name = "torios"  or  get("instance_name", "torios")
        pattern = re.compile(r'instance_name[^#\n]*["\']torios["\']', re.IGNORECASE)
        hits = _scan(files, pattern)

        assert hits == [], (
            "Found backend service code that uses 'torios' as a hardcoded "
            "instance_name default. Use 'yourOS' as the default fallback.\n"
            + "\n".join("  {}:{}: {}".format(p, n, l) for p, n, l in hits)
        )
