from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from thearc.models.markdown import MarkdownDocument
from thearc.models.ranks import Ranks


class SkillMetadata(BaseModel):
    """Pydantic model representing skill metadata frontmatter."""
    model_config = ConfigDict(validate_assignment=True)
    ranks: Ranks | None = None
    name: str
    description: str
    version: str = "1.0.0"
    author: str | None = None
    tags: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)

    def to_yaml(self, include_ranks: bool = True) -> str:
        """Serialize metadata to YAML string."""
        import yaml
        data = self.model_dump(exclude_none=True, exclude={"extra"})
        if self.extra:
            data.update(self.extra)
        if not include_ranks:
            data.pop("ranks", None)
        return yaml.dump(data, sort_keys=False).strip()

class SkillDefinition(BaseModel):
    """Pydantic model representing a complete agent skill."""
    metadata: SkillMetadata
    instructions: str
    document: MarkdownDocument | None = None

    @classmethod
    def from_markdown(cls, name: str, markdown_text: str, default_description: str = "") -> SkillDefinition:
        doc = MarkdownDocument.parse(markdown_text)
        meta_dict = doc.frontmatter or {}

        metadata = SkillMetadata(
            name=meta_dict.get("name", name),
            description=meta_dict.get("description", default_description or f"Skill for {name}"),
            version=meta_dict.get("version", "1.0.0"),
            author=meta_dict.get("author"),
            ranks=meta_dict.get("ranks"),
            tags=meta_dict.get("tags", []),
            extra={k: v for k, v in meta_dict.items()
                   if k not in {"name", "description", "version", "author", "tags", "ranks"}}
        )

        instructions = doc.root_content
        if doc.sections:
            sec_md = "\n\n".join([s.to_markdown() for s in doc.sections])
            instructions = f"{instructions}\n\n{sec_md}".strip()

        return cls(metadata=metadata, instructions=instructions, document=doc)

    def to_markdown(self, include_ranks: bool = True) -> str:
        """Render current instructions and metadata, optionally omitting ranks."""
        body = MarkdownDocument.parse(self.instructions).to_markdown(include_ranks=include_ranks).strip()
        return f"---\n{self.metadata.to_yaml(include_ranks=include_ranks)}\n---\n\n{body}\n"

    def write(self, path: str | Path, include_ranks: bool = True) -> None:
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_markdown(include_ranks=include_ranks), encoding="utf-8")


class CommandDefinition(BaseModel):
    """Pydantic model representing a slash command or prompt definition."""
    name: str
    description: str
    arguments: str | None = None
    body: str

    @classmethod
    def from_markdown(cls, name: str, markdown_text: str) -> CommandDefinition:
        doc = MarkdownDocument.parse(markdown_text)
        meta_dict = doc.frontmatter or {}

        description = meta_dict.get("description", f"Slash command /{name}")
        arguments = meta_dict.get("arguments")

        body = doc.root_content
        if doc.sections:
            sec_md = "\n\n".join([s.to_markdown() for s in doc.sections])
            body = f"{body}\n\n{sec_md}".strip()

        return cls(name=name, description=description, arguments=arguments, body=body)
