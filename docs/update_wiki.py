"""Sync public Obsidian notes into the MkDocs wiki.

The sync preserves the vault's folder tree while filtering private notes and
private markdown blocks before writing files into the wiki directory.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
import stat
from typing import Any

import frontmatter
import mistune
from wheel.bdist_wheel import remove_readonly

VAULT_ROOT = Path("C:/Users/visio/OneDrive/Documents/Obsidian Notes/Hearth")
WIKI_ROOT = Path("../wiki").resolve()

MARKDOWN_EXTENSIONS = {".md", ".markdown"}
COPY_NON_MARKDOWN_FILES = False
DELETE_EMPTY_DIRECTORIES = True

SECRET_TAG = "#secret"
WORKSHOP_TAG = "#workshop"
SECRET_HEADER = "Secret"
WORKSHOP_HEADER = "Workshop"

FILTER_RULES = (
    (SECRET_TAG, SECRET_HEADER),
    (WORKSHOP_TAG, WORKSHOP_HEADER),
)

EXCLUDED_ROOT_FOLDERS = {
    ".obsidian",
    "Administration",
    "Spiritual",
    "Workshop",
    "Writing",
}

FRONTMATTER_TAG_KEYS = {"tag", "tags"}
ALLOW_BARE_FRONTMATTER_TAGS = True


MARKDOWN = mistune.create_markdown(renderer="ast")
ATX_HEADING_RE = re.compile(r"^(?P<marks>#{1,6})[ \t]+(?P<title>.*?)[ \t]*#*[ \t]*$")
SETEXT_HEADING_RE = re.compile(r"^[ \t]*(?P<marks>=+|-+)[ \t]*$")
FENCED_CODE_RE = re.compile(r"^[ \t]*(```|~~~)")
SINGLE_BULLET_RE = re.compile(r"^[ \t]*[-*][ \t]+")
OBSIDIAN_CALLOUT_RE = re.compile(r"^[ \t]*>[ \t]*\[![^\]]+\]")
BLOCKQUOTE_RE = re.compile(r"^[ \t]*>")
BLOCK_START_RE = re.compile(
    r"^[ \t]*(#{1,6}[ \t]+|>|[-+*][ \t]+|\d+\.[ \t]+|([-*_][ \t]*){3,}$)"
)


@dataclass
class SyncStats:
    copied: int = 0
    filtered: int = 0
    skipped_by_folder: int = 0
    skipped_by_frontmatter: int = 0


def markdown_text_contains_tag(markdown_text: str, tag: str) -> bool:
    return re.compile(rf"(?<![\w-]){re.escape(tag)}(?![\w-])").search(markdown_text) is not None


def frontmatter_value_matches_tag(value: Any, tag: str) -> bool:
    """Return True when a frontmatter tag value matches the configured tag."""
    if isinstance(value, str):
        value = str(value).strip()
        if value == tag or markdown_text_contains_tag(value, tag):
            return True
        return ALLOW_BARE_FRONTMATTER_TAGS and value == tag.removeprefix("#")

    if isinstance(value, Iterable):
        return any(frontmatter_value_matches_tag(item, tag) for item in value)

    return False


def frontmatter_has_tag(metadata: dict[str, Any], tag: str) -> bool:
    """Check configured frontmatter tag fields for a tag."""
    return any(
        key in metadata and frontmatter_value_matches_tag(metadata[key], tag)
        for key in FRONTMATTER_TAG_KEYS
    )


def token_plain_text(token: dict[str, Any]) -> str:
    """Extract visible text from a mistune AST token."""
    text_parts: list[str] = []

    if "raw" in token:
        text_parts.append(str(token["raw"]))

    for child in token.get("children", []):
        text_parts.append(token_plain_text(child))

    return "".join(text_parts)


def markdown_heading_level_and_title(lines: list[str], index: int) -> tuple[int, str] | None:
    """Return the markdown heading at a line, supporting ATX and setext styles."""
    atx_match = ATX_HEADING_RE.match(lines[index])
    if atx_match:
        return len(atx_match.group("marks")), atx_match.group("title").strip()

    if index + 1 < len(lines):
        setext_match = SETEXT_HEADING_RE.match(lines[index + 1])
        if setext_match:
            level = 1 if setext_match.group("marks").startswith("=") else 2
            return level, lines[index].strip()

    return None


def remove_header_sections(markdown_text: str, header: str) -> str:
    """Remove headers with a title and all content below them at that level."""
    lines = markdown_text.splitlines(keepends=True)
    output: list[str] = []
    index = 0
    skipping_level: int | None = None

    while index < len(lines):
        heading = markdown_heading_level_and_title(lines, index)

        if heading is not None:
            level, title = heading

            if skipping_level is not None and level <= skipping_level:
                skipping_level = None

            if title == header:
                skipping_level = level
                index += 2 if index + 1 < len(lines) and SETEXT_HEADING_RE.match(lines[index + 1]) else 1
                continue

        if skipping_level is None:
            output.append(lines[index])

        index += 1

    return "".join(output)


def block_is_paragraph(block: str) -> bool:
    """Use mistune to determine whether a source block is a single paragraph."""
    tokens = [token for token in MARKDOWN(block) if token["type"] != "blank_line"]
    return len(tokens) == 1 and tokens[0]["type"] == "paragraph"


def paragraph_candidate_end(lines: list[str], start: int) -> int:
    """Find the end of a plain markdown paragraph candidate."""
    end = start

    while end < len(lines):
        line = lines[end]

        if not line.strip():
            break

        if end > start and (BLOCK_START_RE.match(line) or FENCED_CODE_RE.match(line)):
            break

        if end + 1 < len(lines) and SETEXT_HEADING_RE.match(lines[end + 1]):
            break

        end += 1

    return end


def obsidian_callout_end(lines: list[str], start: int) -> int:
    """Find the end of an Obsidian callout block."""
    end = start + 1

    while end < len(lines) and BLOCKQUOTE_RE.match(lines[end]):
        end += 1

    return end


def remove_paragraphs_with_tag(markdown_text: str, tag: str) -> str:
    """Remove full markdown paragraphs containing a tag."""
    lines = markdown_text.splitlines(keepends=True)
    kept_lines: list[str] = []
    index = 0
    in_fenced_code = False

    while index < len(lines):
        line = lines[index]

        if FENCED_CODE_RE.match(line):
            in_fenced_code = not in_fenced_code
            kept_lines.append(line)
            index += 1
            continue

        if OBSIDIAN_CALLOUT_RE.match(line):
            end = obsidian_callout_end(lines, index)
            callout = "".join(lines[index:end])

            if markdown_text_contains_tag(callout, tag):
                index = end
                continue

            kept_lines.extend(lines[index:end])
            index = end
            continue

        if SINGLE_BULLET_RE.match(line):
            if markdown_text_contains_tag(line, tag):
                index += 1
                continue

            kept_lines.append(line)
            index += 1
            continue

        if in_fenced_code or not line.strip() or BLOCK_START_RE.match(line):
            kept_lines.append(line)
            index += 1
            continue

        if index + 1 < len(lines) and SETEXT_HEADING_RE.match(lines[index + 1]):
            kept_lines.extend(lines[index : index + 2])
            index += 2
            continue

        end = paragraph_candidate_end(lines, index)
        paragraph = "".join(lines[index:end])

        if markdown_text_contains_tag(paragraph, tag) and block_is_paragraph(paragraph):
            index = end
            continue

        kept_lines.extend(lines[index:end])
        index = end

    return "".join(kept_lines)


def filter_markdown(markdown_text: str, rules: Iterable[tuple[str, str]]) -> str:
    """Apply all configured tag and header filters to markdown body text."""
    MARKDOWN(markdown_text)
    filtered = markdown_text

    for tag, header in rules:
        filtered = remove_header_sections(filtered, header)
        filtered = remove_paragraphs_with_tag(filtered, tag)

    return filtered


def render_markdown(post: frontmatter.Post, body: str) -> str:
    if not post.metadata:
        return body

    return frontmatter.dumps(frontmatter.Post(body, **post.metadata))


def is_in_excluded_root_folder(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    root_folder = relative.parts[0] if len(relative.parts) > 1 else None
    return root_folder in EXCLUDED_ROOT_FOLDERS


def copy_markdown_file(source: Path, destination: Path, stats: SyncStats) -> None:
    post = frontmatter.loads(source.read_text(encoding="utf-8"))
    tags_to_exclude = [tag for tag, _header in FILTER_RULES]

    if any(frontmatter_has_tag(post.metadata, tag) for tag in tags_to_exclude):
        if destination.exists() and destination.is_file():
            destination.unlink()
        
        stats.skipped_by_frontmatter += 1
        return

    filtered_body = filter_markdown(post.content, FILTER_RULES)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_markdown(post, filtered_body), encoding="utf-8")

    stats.copied += 1
    if filtered_body != post.content:
        stats.filtered += 1


def copy_regular_file(source: Path, destination: Path, stats: SyncStats) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    stats.copied += 1


def remove_excluded_output_roots() -> None:
    for folder in EXCLUDED_ROOT_FOLDERS:
        target = WIKI_ROOT / folder
        if target.exists():
            shutil.rmtree(target, onerror=remove_readonly)


def remove_empty_directories(root: Path) -> None:
    for directory in sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True):
        if not any(directory.iterdir()):
            try:
                print(f'WARN: Removed empty folder {directory}')
                os.chmod(directory, stat.S_IWRITE)
                directory.rmdir()
            except OSError:
                pass


def sync_vault_to_wiki() -> SyncStats:
    if not VAULT_ROOT.exists():
        raise FileNotFoundError(f"Vault root does not exist: {VAULT_ROOT}")

    WIKI_ROOT.mkdir(parents=True, exist_ok=True)
    remove_excluded_output_roots()

    stats = SyncStats()

    for source in VAULT_ROOT.rglob("*"):
        if source.is_dir():
            continue

        if is_in_excluded_root_folder(source, VAULT_ROOT):
            stats.skipped_by_folder += 1
            continue

        destination = WIKI_ROOT / source.relative_to(VAULT_ROOT)

        if source.suffix.lower() in MARKDOWN_EXTENSIONS:
            copy_markdown_file(source, destination, stats)
        elif COPY_NON_MARKDOWN_FILES:
            copy_regular_file(source, destination, stats)

    if DELETE_EMPTY_DIRECTORIES:
        remove_empty_directories(WIKI_ROOT)

    return stats


if __name__ == "__main__":
    result = sync_vault_to_wiki()
    print(f"Copied files: {result.copied}")
    print(f"Filtered markdown files: {result.filtered}")
    print(f"Skipped by excluded root folder: {result.skipped_by_folder}")
    print(f"Skipped by frontmatter tags: {result.skipped_by_frontmatter}")
