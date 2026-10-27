"""Parser for Git unified diffs with component, route, and framework detection."""

import os
import re
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class ChangedHunk(BaseModel):
    source_start: int
    source_length: int
    target_start: int
    target_length: int
    added_lines: List[str] = Field(default_factory=list)
    removed_lines: List[str] = Field(default_factory=list)


class ChangedFile(BaseModel):
    path: str
    old_path: Optional[str] = None
    change_type: str = "modified"  # added, modified, deleted, renamed
    added_count: int = 0
    removed_count: int = 0
    hunks: List[ChangedHunk] = Field(default_factory=list)
    detected_routes: List[str] = Field(default_factory=list)
    is_ui_component: bool = False
    is_api_route: bool = False
    is_style_file: bool = False


class ParsedDiff(BaseModel):
    files: List[ChangedFile] = Field(default_factory=list)
    total_added: int = 0
    total_removed: int = 0
    raw_diff: str = ""

    @property
    def file_paths(self) -> List[str]:
        return [f.path for f in self.files]

    @property
    def suspected_routes(self) -> List[str]:
        routes = set()
        for f in self.files:
            routes.update(f.detected_routes)
        return sorted(list(routes)) if routes else ["/"]


class DiffParser:
    """Parses raw git diff strings or patch files into structured representations."""

    UI_EXTENSIONS = {".jsx", ".tsx", ".vue", ".svelte", ".html", ".js", ".ts"}
    STYLE_EXTENSIONS = {".css", ".scss", ".sass", ".less", ".tailwind.css"}
    API_INDICATORS = {"api/", "route.ts", "route.js", "controller", "endpoints", "router"}

    def parse_file(self, diff_file_path: Path | str) -> ParsedDiff:
        path = Path(diff_file_path)
        if not path.exists():
            raise FileNotFoundError(f"Diff file not found: {diff_file_path}")
        content = path.read_text(encoding="utf-8", errors="replace")
        return self.parse_string(content)

    def parse_string(self, diff_text: str) -> ParsedDiff:
        lines = diff_text.splitlines()
        files: List[ChangedFile] = []
        current_file: Optional[ChangedFile] = None
        current_hunk: Optional[ChangedHunk] = None

        diff_header_pattern = re.compile(r"^diff --git a/(.*?) b/(.*)$")
        hunk_header_pattern = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

        for line in lines:
            diff_match = diff_header_pattern.match(line)
            if diff_match:
                if current_file:
                    if current_hunk:
                        current_file.hunks.append(current_hunk)
                        current_hunk = None
                    self._enrich_file_metadata(current_file)
                    files.append(current_file)

                old_p, new_p = diff_match.group(1), diff_match.group(2)
                current_file = ChangedFile(
                    path=new_p,
                    old_path=old_p if old_p != new_p else None,
                )
                continue

            if not current_file:
                continue

            hunk_match = hunk_header_pattern.match(line)
            if hunk_match:
                if current_hunk:
                    current_file.hunks.append(current_hunk)
                s_start = int(hunk_match.group(1))
                s_len = int(hunk_match.group(2) or 1)
                t_start = int(hunk_match.group(3))
                t_len = int(hunk_match.group(4) or 1)
                current_hunk = ChangedHunk(
                    source_start=s_start,
                    source_length=s_len,
                    target_start=t_start,
                    target_length=t_len,
                )
                continue

            if current_hunk:
                if line.startswith("+") and not line.startswith("+++"):
                    current_hunk.added_lines.append(line[1:])
                    current_file.added_count += 1
                elif line.startswith("-") and not line.startswith("---"):
                    current_hunk.removed_lines.append(line[1:])
                    current_file.removed_count += 1

        if current_file:
            if current_hunk:
                current_file.hunks.append(current_hunk)
            self._enrich_file_metadata(current_file)
            files.append(current_file)

        total_added = sum(f.added_count for f in files)
        total_removed = sum(f.removed_count for f in files)

        return ParsedDiff(
            files=files,
            total_added=total_added,
            total_removed=total_removed,
            raw_diff=diff_text,
        )

    def _enrich_file_metadata(self, f: ChangedFile) -> None:
        path_str = f.path.lower()
        ext = os.path.splitext(path_str)[1]

        f.is_ui_component = ext in self.UI_EXTENSIONS
        f.is_style_file = ext in self.STYLE_EXTENSIONS
        f.is_api_route = any(ind in path_str for ind in self.API_INDICATORS)

        # Infer potential routes from common file-based routing conventions:
        # e.g., pages/checkout.tsx -> /checkout
        # app/dashboard/page.tsx -> /dashboard
        # routes/login.jsx -> /login
        # components/Cart.tsx -> /cart
        if "pages/" in path_str:
            route = "/" + path_str.split("pages/")[-1].replace(ext, "").replace("index", "")
            f.detected_routes.append(re.sub(r"/+", "/", route))
        elif "app/" in path_str and ("page." in path_str or "layout." in path_str):
            route = "/" + path_str.split("app/")[-1].split("/page")[0].split("/layout")[0]
            f.detected_routes.append(re.sub(r"/+", "/", route))
        elif "components/" in path_str:
            comp_name = os.path.splitext(os.path.basename(path_str))[0].lower()
            f.detected_routes.append(f"/{comp_name}")
            f.detected_routes.append("/")

