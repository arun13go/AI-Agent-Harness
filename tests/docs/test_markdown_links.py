import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).parents[2]
ROOT_DOCUMENTS = (ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "AGENTS.md")
MARKDOWN_FILES = ROOT_DOCUMENTS + tuple(sorted((ROOT / "docs").rglob("*.md")))
LOCAL_LINK = re.compile(r"\]\((?!https?://|mailto:|#)(?P<target>[^)#]+)(?:#[^)]+)?\)")


def test_local_markdown_links_resolve() -> None:
    missing: list[str] = []

    for document in MARKDOWN_FILES:
        content = document.read_text(encoding="utf-8")
        for match in LOCAL_LINK.finditer(content):
            target = unquote(match.group("target").strip().strip("<>"))
            if not (document.parent / target).exists():
                missing.append(f"{document.relative_to(ROOT)} -> {target}")

    assert not missing, "Missing local Markdown targets:\n" + "\n".join(missing)
