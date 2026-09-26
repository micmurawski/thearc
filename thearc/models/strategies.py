from abc import ABC, abstractmethod

from thearc.config import AGENT_ANTIGRAVITY, AGENT_CLAUDE, AGENT_CODEX, AGENT_PI, normalize_agent_name
from thearc.models.markdown import MarkdownDocument
from thearc.models.schemas import CommandDefinition, SkillDefinition, SkillMetadata


class AgentFormatStrategy(ABC):
    """Abstract Strategy interface for formatting skills and commands per agent."""

    @property
    @abstractmethod
    def agent_name(self) -> str:
        pass

    @abstractmethod
    def to_yaml(self, metadata: SkillMetadata, include_ranks: bool = True) -> str:
        """Serialize SkillMetadata into agent-specific YAML/Header string."""

    @abstractmethod
    def skill_to_markdown(self, skill: SkillDefinition, include_ranks: bool = True) -> str:
        """Serialize SkillDefinition into agent-specific Markdown string."""

    @abstractmethod
    def command_to_markdown(self, command: CommandDefinition) -> str:
        """Serialize CommandDefinition into agent-specific Markdown string."""


class AntigravityStrategy(AgentFormatStrategy):
    """Serialization strategy for Antigravity Agent."""

    @property
    def agent_name(self) -> str:
        return AGENT_ANTIGRAVITY

    def to_yaml(self, metadata: SkillMetadata, include_ranks: bool = True) -> str:
        yaml_body = metadata.to_yaml(include_ranks=include_ranks)
        return f"---\n{yaml_body}\n---"

    def skill_to_markdown(self, skill: SkillDefinition, include_ranks: bool = True) -> str:
        header = self.to_yaml(skill.metadata, include_ranks=include_ranks)
        body = skill.instructions if include_ranks else MarkdownDocument.parse(
            skill.instructions).to_markdown(include_ranks=False).strip()
        return f"{header}\n\n{body}".strip() + "\n"

    def command_to_markdown(self, command: CommandDefinition) -> str:
        header = f"---\ndescription: {command.description}\n---"
        return f"{header}\n\n{command.body}".strip() + "\n"


class ClaudeStrategy(AgentFormatStrategy):
    """Serialization strategy for Claude Code / Claude Agent."""

    @property
    def agent_name(self) -> str:
        return AGENT_CLAUDE

    def to_yaml(self, metadata: SkillMetadata, include_ranks: bool = True) -> str:
        yaml_body = metadata.to_yaml(include_ranks=include_ranks)
        return f"---\n{yaml_body}\n---"

    def skill_to_markdown(self, skill: SkillDefinition, include_ranks: bool = True) -> str:
        suffix = skill.metadata.ranks.suffix() if include_ranks and skill.metadata.ranks is not None else ""
        header = f"# Skill: {skill.metadata.name}{suffix}\n> {skill.metadata.description}"
        body = skill.instructions if include_ranks else MarkdownDocument.parse(
            skill.instructions).to_markdown(include_ranks=False).strip()
        return f"{header}\n\n{body}".strip() + "\n"

    def command_to_markdown(self, command: CommandDefinition) -> str:
        header = f"# Command: /{command.name}\n> {command.description}"
        return f"{header}\n\n{command.body}".strip() + "\n"


class CodexStrategy(AgentFormatStrategy):
    """Serialization strategy for OpenAI Codex / Copilot Agent."""

    @property
    def agent_name(self) -> str:
        return AGENT_CODEX

    def to_yaml(self, metadata: SkillMetadata, include_ranks: bool = True) -> str:
        header = f"<!-- Skill: {metadata.name} | Description: {metadata.description} -->"
        if include_ranks and metadata.ranks is not None:
            header = "---\nranks:\n" + "\n".join(
                f"  {key}: {value}" for key, value in metadata.ranks.model_dump().items()
            ) + f"\n---\n\n{header}"
        return header

    def skill_to_markdown(self, skill: SkillDefinition, include_ranks: bool = True) -> str:
        header = self.to_yaml(skill.metadata, include_ranks=include_ranks)
        body = skill.instructions if include_ranks else MarkdownDocument.parse(
            skill.instructions).to_markdown(include_ranks=False).strip()
        return f"{header}\n\n{body}".strip() + "\n"

    def command_to_markdown(self, command: CommandDefinition) -> str:
        header = f"<!-- Prompt: /{command.name} | Description: {command.description} -->"
        return f"{header}\n\n{command.body}".strip() + "\n"


class PiStrategy(AgentFormatStrategy):
    """Serialization strategy for Pi Agent."""

    @property
    def agent_name(self) -> str:
        return AGENT_PI

    def to_yaml(self, metadata: SkillMetadata, include_ranks: bool = True) -> str:
        yaml_body = metadata.to_yaml(include_ranks=include_ranks)
        return f"---\n{yaml_body}\n---"

    def skill_to_markdown(self, skill: SkillDefinition, include_ranks: bool = True) -> str:
        header = self.to_yaml(skill.metadata, include_ranks=include_ranks)
        body = skill.instructions if include_ranks else MarkdownDocument.parse(
            skill.instructions).to_markdown(include_ranks=False).strip()
        return f"{header}\n\n{body}".strip() + "\n"

    def command_to_markdown(self, command: CommandDefinition) -> str:
        header = f"---\ndescription: {command.description}\n---"
        return f"{header}\n\n{command.body}".strip() + "\n"


def get_strategy(agent_name: str) -> AgentFormatStrategy:
    """Retrieve format strategy for agent."""
    norm_name = normalize_agent_name(agent_name)
    if norm_name == AGENT_ANTIGRAVITY:
        return AntigravityStrategy()
    elif norm_name == AGENT_CLAUDE:
        return ClaudeStrategy()
    elif norm_name == AGENT_CODEX:
        return CodexStrategy()
    elif norm_name == AGENT_PI:
        return PiStrategy()
    else:
        raise ValueError(f"Unknown agent: {agent_name}")
