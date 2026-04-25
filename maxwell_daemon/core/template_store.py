"""Task Template Store for Maxwell Daemon.

Provides parameterised, repeatable task workflows (Issue #492).
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ParameterType(str, Enum):
    STRING = "string"
    REPO = "repo"
    FILE = "file"
    ENUM = "enum"
    DATE = "date"


class TemplateParameter(BaseModel):
    name: str
    type: ParameterType = ParameterType.STRING
    description: str | None = None
    required: bool = True
    default: Any | None = None
    options: list[str] | None = None  # For ENUM type


class TaskTemplate(BaseModel):
    id: str
    name: str
    description: str
    prompt_template: str
    parameters: list[TemplateParameter] = Field(default_factory=list)
    default_backend: str | None = None
    default_policy: str | None = None
    tags: list[str] = Field(default_factory=list)

    def render(self, kwargs: dict[str, Any]) -> str:
        """Render the prompt_template using Jinja2."""
        # Optional jinja2 import to avoid hard dependency if not needed everywhere
        try:
            from jinja2 import Template
        except ImportError:
            # Fallback to simple format if jinja2 is missing
            return self.prompt_template.format(**kwargs)

        t = Template(self.prompt_template)
        return t.render(**kwargs)


class TemplateStore:
    """Store for predefined and user-customised task templates."""

    def __init__(self, templates_path: Path | None = None) -> None:
        self._path = templates_path
        self._templates: dict[str, TaskTemplate] = {}
        self._load_builtins()
        if self._path and self._path.exists():
            self._load_from_disk()

    def _load_builtins(self) -> None:
        """Load opinionated built-in templates."""
        builtins = [
            TaskTemplate(
                id="audit-repo-todos",
                name="Audit Repo TODOs",
                description="Scan a repository for TODO comments and summarize them.",
                prompt_template="Scan the repository {{ repo }} for TODOs and FIXMEs. Group them by file and suggest a priority order.",
                parameters=[
                    TemplateParameter(name="repo", type=ParameterType.REPO, required=True),
                ],
                tags=["audit", "cleanup"],
            ),
            TaskTemplate(
                id="write-release-notes",
                name="Write Release Notes",
                description="Generate release notes from recent git commits.",
                prompt_template="Write release notes for {{ repo }}. Focus on user-facing features, bug fixes, and breaking changes.",
                parameters=[
                    TemplateParameter(name="repo", type=ParameterType.REPO, required=True),
                ],
                tags=["release", "docs"],
            ),
        ]
        for t in builtins:
            self._templates[t.id] = t

    def _load_from_disk(self) -> None:
        """Load user templates from disk."""
        if not self._path or not self._path.exists():
            return

        for child in self._path.glob("*.json"):
            if not child.is_file():
                continue
            try:
                data = json.loads(child.read_text("utf-8"))
                template = TaskTemplate.model_validate(data)
                self._templates[template.id] = template
            except Exception:
                pass  # Ignore invalid files for now

    def list_templates(self) -> list[TaskTemplate]:
        return list(self._templates.values())

    def get_template(self, template_id: str) -> TaskTemplate | None:
        return self._templates.get(template_id)
