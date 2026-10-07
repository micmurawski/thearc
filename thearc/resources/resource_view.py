"""Addressable, paged views of a frozen MetaAgent; ranks never affect IDs."""

from __future__ import annotations

import json

from thearc.learning.curation.assessments import AssessmentLedger, _content
from thearc.learning.curation.tools import seed_rank_history
from thearc.learning.evidence.snapshot import content_hash
from thearc.learning.privacy import redact
from thearc.models import MarkdownSection, MetaAgent, ResourceTarget


class ResourceView:
    """Paged, redacted access to a frozen agent's addressable resources.

    IDs, the address list and labelled Markdown come from the agent model
    (``ResourceTarget.id``, ``MetaAgent.iter_targets``, ``MetaAgent.to_labeled_markdown``);
    this view adds redaction, paging and assessment-ledger ranks.
    """

    def __init__(self, agent: MetaAgent, *, ledger: AssessmentLedger | None = None):
        self._agent = agent.without_ranks()
        self.revision = content_hash(self._agent.model_dump(mode="json"))
        self._ledger = seed_rank_history(agent, ledger or AssessmentLedger())
        self._targets = {target.id: target for target, _ in self._agent.iter_targets()}

    @staticmethod
    def id_for(target: ResourceTarget) -> str:
        """An address ID, meaningful together with the evaluated snapshot revision."""
        return target.id

    def resolve(self, resource_id: str) -> ResourceTarget:
        try:
            return self._targets[resource_id]
        except KeyError:
            raise ValueError(f"Unknown resource ID: {resource_id}. Use list_resources to find its ID.") from None

    @property
    def mapping(self):
        return {key: target.model_dump(mode="json") for key, target in self._targets.items()}

    def catalog(self, *, include_ranks=False):
        """Every addressable ID with its target, for inspection or export."""
        return [self._entry(resource_id, include_ranks) for resource_id in self._targets]

    def _current_ranks(self, target):
        return self._ledger.ranks(target, revision=content_hash(_content(self._agent, target)))

    def catalog_markdown(self, *, include_ranks=False, include_content=False, title=None):
        """An outline of every ID, or with include_content the agent's Markdown with labelled headings."""
        return redact(self._agent.to_labeled_markdown(include_ranks=include_ranks, outline=not include_content,
                                                      ranks=self._current_ranks, title=title))

    def _entry(self, resource_id, include_ranks):
        target = self.resolve(resource_id)
        entry = {"id": resource_id, "target": target.model_dump(mode="json")}
        if include_ranks:
            revision = content_hash(_content(self._agent, target))
            entry["current_ranks"] = self._ledger.ranks(target, revision=revision).model_dump()
            entry["lifetime_ranks"] = self._ledger.ranks(target).model_dump()
        return entry

    def list(self, *, query="", offset=0, limit=20, include_ranks=False):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("offset must be nonnegative; limit must be between 1 and 50")
        ids = [key for key, target in self._targets.items()
               if query.casefold() in f"{target.kind} {target.name} {target.section or ''}".casefold()]
        end = offset + limit
        return {"resources": [self._entry(key, include_ranks) for key in ids[offset:end]],
                "total": len(ids), "next_offset": end if end < len(ids) else None}

    def read(self, resource_id, *, offset=0, max_chars=8000, include_ranks=False):
        if type(max_chars) is not int or max_chars < 1:
            raise ValueError("max_chars must be a positive integer")
        target = self.resolve(resource_id)
        value = redact(_content(self._agent, target))
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
        if type(offset) is not int or offset < 0 or offset > len(text):
            raise ValueError("offset is outside the resource")
        end = offset + max_chars
        return {**self._entry(resource_id, include_ranks), "content": text[offset:end],
                "total_chars": len(text), "next_offset": end if end < len(text) else None}

    def to_markdown(self, resource_id, *, offset=0, max_chars=8000, include_ranks=False):
        # Page the rendered Markdown, so offsets always refer to this format.
        value = redact(_content(self._agent, self.resolve(resource_id)))
        if self.resolve(resource_id).section is not None:
            content = MarkdownSection.model_validate(value).to_markdown(include_ranks=False)
        elif isinstance(value, str):
            content = value
        elif "instructions" in value or "content" in value:
            content = value.get("instructions", value.get("content", ""))
        else:
            from .formats import fenced

            content = fenced(json.dumps(value, ensure_ascii=False, indent=2), "json")
        if (type(offset) is not int or not 0 <= offset <= len(content)
                or type(max_chars) is not int or max_chars < 1):
            raise ValueError("Invalid Markdown page")
        page = self._entry(resource_id, include_ranks)
        target = page["target"]
        title = f"## [{resource_id}] {target['kind']}: {target['name']}"
        if target["section"]:
            title += f" / {target['section']}"
        ranks = ""
        if include_ranks:
            ranks = f"\n\nCurrent ranks: {page['current_ranks']}\nLifetime ranks: {page['lifetime_ranks']}"
        end = offset + max_chars
        more = f"\n\nContinue at offset {end}." if end < len(content) else ""
        return f"{title}{ranks}\n\n{content[offset:end]}{more}"
