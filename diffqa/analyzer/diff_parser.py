"""Parser for Git unified diffs — extracts structured file, hunk, route, and component metadata.

Supports:
- Standard ``git diff`` and ``git format-patch`` output.
- Automatic detection of UI components, style files, and API routes.
- File-based routing inference (Next.js pages/, app/, SvelteKit routes/, etc.).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field


# ── Data Models ─────────────────────────────────────────────────────────────


class ChangedHunk(BaseModel):
    """A single contiguous block of changes within a file."""

    source_start: int
    source_length: int
    target_start: int
    target_length: int
    added_lines: List[str] = Field(default_factory=list)
    removed_lines: List[str] = Field(default_factory=list)


class ChangedFile(BaseModel):
    """Metadata for a single file touched in the diff."""

    path: str
    old_path: Optional[str] = None
    change_type: str = "modified"  # added | modified | deleted | renamed
    added_count: int = 0
    removed_count: int = 0
    hunks: List[ChangedHunk] = Field(default_factory=list)
    detected_routes: List[str] = Field(default_factory=list)
    is_ui_component: bool = False
    is_api_route: bool = False
    is_style_file: bool = False
    is_test_file: bool = False
    is_config_file: bool = False

    @property
    def total_changes(self) -> int:
        return self.added_count + self.removed_count


class ParsedDiff(BaseModel):
    """Structured representation of an entire git diff."""

    files: List[ChangedFile] = Field(default_factory=list)
    total_added: int = 0
    total_removed: int = 0
    raw_diff: str = ""

    @property
    def file_paths(self) -> List[str]:
        return [f.path for f in self.files]

    @property
    def ui_files(self) -> List[ChangedFile]:
        return [f for f in self.files if f.is_ui_component]

    @property
    def api_files(self) -> List[ChangedFile]:
        return [f for f in self.files if f.is_api_route]

    @property
    def suspected_routes(self) -> List[str]:
        routes: set[str] = set()
        for f in self.files:
            routes.update(f.detected_routes)
        return sorted(routes) if routes else ["/"]


# ── Parser ──────────────────────────────────────────────────────────────────


class DiffParser:
    """Parses raw git diff strings or .diff / .patch files into a ``ParsedDiff``."""

    UI_EXTENSIONS = frozenset({".jsx", ".tsx", ".vue", ".svelte", ".html", ".js", ".ts"})
    STYLE_EXTENSIONS = frozenset({".css", ".scss", ".sass", ".less", ".styl"})
    TEST_INDICATORS = frozenset({"test", "spec", "__tests__", "e2e", "cypress"})
    CONFIG_EXTENSIONS = frozenset({".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"})
    API_INDICATORS = frozenset({"api/", "route.ts", "route.js", "+server", "controller", "endpoints", "router"})

    _DIFF_HEADER = re.compile(r"^diff --git a/(.*?) b/(.*)$")
    _HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

    def parse_file(self, diff_file_path: Path | str) -> ParsedDiff:
        """Read a .diff or .patch file and return structured output."""
        path = Path(diff_file_path)
        if not path.exists():
            raise FileNotFoundError(f"Diff file not found: {diff_file_path}")
        return self.parse_string(path.read_text(encoding="utf-8", errors="replace"))

    def parse_string(self, diff_text: str) -> ParsedDiff:
        """Parse raw diff text into a ``ParsedDiff``."""
        lines = diff_text.splitlines()
        files: List[ChangedFile] = []
        current_file: Optional[ChangedFile] = None
        current_hunk: Optional[ChangedHunk] = None

        for line in lines:
            # ── New file header ────────────────────────────────────
            m = self._DIFF_HEADER.match(line)
            if m:
                if current_file:
                    if current_hunk:
                        current_file.hunks.append(current_hunk)
                        current_hunk = None
                    self._enrich(current_file)
                    files.append(current_file)
                old_p, new_p = m.group(1), m.group(2)
                current_file = ChangedFile(
                    path=new_p,
                    old_path=old_p if old_p != new_p else None,
                    change_type="renamed" if old_p != new_p else "modified",
                )
                continue

            if not current_file:
                continue

            # Detect new / deleted files
            if line.startswith("new file mode"):
                current_file.change_type = "added"
                continue
            if line.startswith("deleted file mode"):
                current_file.change_type = "deleted"
                continue

            # ── Hunk header ────────────────────────────────────────
            h = self._HUNK_HEADER.match(line)
            if h:
                if current_hunk:
                    current_file.hunks.append(current_hunk)
                current_hunk = ChangedHunk(
                    source_start=int(h.group(1)),
                    source_length=int(h.group(2) or 1),
                    target_start=int(h.group(3)),
                    target_length=int(h.group(4) or 1),
                )
                continue

            # ── Diff content lines ─────────────────────────────────
            if current_hunk:
                if line.startswith("+") and not line.startswith("+++"):
                    current_hunk.added_lines.append(line[1:])
                    current_file.added_count += 1
                elif line.startswith("-") and not line.startswith("---"):
                    current_hunk.removed_lines.append(line[1:])
                    current_file.removed_count += 1

        # Flush last file
        if current_file:
            if current_hunk:
                current_file.hunks.append(current_hunk)
            self._enrich(current_file)
            files.append(current_file)

        return ParsedDiff(
            files=files,
            total_added=sum(f.added_count for f in files),
            total_removed=sum(f.removed_count for f in files),
            raw_diff=diff_text,
        )

    # ── Private helpers ─────────────────────────────────────────────────

    def _enrich(self, f: ChangedFile) -> None:
        """Annotate a ChangedFile with inferred metadata."""
        path_lower = f.path.lower()
        ext = os.path.splitext(path_lower)[1]
        basename = os.path.basename(path_lower)

        # File type classification
        f.is_ui_component = ext in self.UI_EXTENSIONS
        f.is_style_file = ext in self.STYLE_EXTENSIONS
        f.is_api_route = any(ind in path_lower for ind in self.API_INDICATORS)
        f.is_test_file = any(ind in path_lower for ind in self.TEST_INDICATORS)
        f.is_config_file = ext in self.CONFIG_EXTENSIONS or basename in (
            "package.json", "tsconfig.json", "vite.config.ts", "next.config.js",
        )

        # Route inference from file-based routing conventions
        self._infer_routes(f, path_lower, ext)

    @staticmethod
    def _infer_routes(f: ChangedFile, path_lower: str, ext: str) -> None:
        """Infer web routes from common framework file-based routing."""
        # Next.js pages/ directory
        if "pages/" in path_lower:
            route = "/" + path_lower.split("pages/")[-1].replace(ext, "").replace("index", "")
            f.detected_routes.append(re.sub(r"/+", "/", route).rstrip("/") or "/")

        # Next.js app/ directory (page.tsx, layout.tsx)
        elif "app/" in path_lower and any(seg in path_lower for seg in ("page.", "layout.")):
            route = "/" + path_lower.split("app/")[-1].split("/page")[0].split("/layout")[0]
            f.detected_routes.append(re.sub(r"/+", "/", route).rstrip("/") or "/")

        # SvelteKit routes/
        elif "routes/" in path_lower and "+page" in path_lower:
            route = "/" + path_lower.split("routes/")[-1].split("/+page")[0]
            f.detected_routes.append(re.sub(r"/+", "/", route).rstrip("/") or "/")

        # Component files → likely affects root + component-named route
        elif "components/" in path_lower:
            comp_name = os.path.splitext(os.path.basename(path_lower))[0].lower()
            f.detected_routes.extend([f"/{comp_name}", "/"])

        # Fallback: if it's a UI file with no route detected, assume root
        elif not f.detected_routes and f.is_ui_component:
            f.detected_routes.append("/")
