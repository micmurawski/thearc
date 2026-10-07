"""Caller-defined output schemas and explicit semantic validation."""

from __future__ import annotations

import json
import re

from jsonschema import Draft202012Validator, validators
from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import GenerateJsonSchema
from referencing import Registry

from thearc.learning.curation.models import Curation, ReflectionItem, ReflectionRating
from thearc.models import MetaAgent, ResourceTarget


class OutputValidationError(ValueError):
    def __init__(self, message, *, response=None):
        self.response = response
        super().__init__(message)


class ResourceSchema(GenerateJsonSchema):
    def model_schema(self, schema):
        result = super().model_schema(schema)
        if issubclass(schema["cls"], ResourceTarget):
            result["x-thearc-resource"] = True
        if issubclass(schema["cls"], Curation):
            result["properties"]["target"]["x-thearc-change-target"] = True
        return result


class OutputBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @classmethod
    def model_json_schema(cls, **kwargs):
        kwargs.setdefault("schema_generator", ResourceSchema)
        return super().model_json_schema(**kwargs)

    @classmethod
    def to_json_schema(cls):
        return json.dumps(cls.model_json_schema(), ensure_ascii=False, indent=2)

    def validate_against(self, agent: MetaAgent):
        validate_references(self.model_dump(mode="json", by_alias=True), type(self).model_json_schema(), agent)
        return self


class ReflectionBaseModel(OutputBaseModel):
    """Extend with any response fields; ResourceTarget fields carry reference semantics."""


class ResourceTag(OutputBaseModel):
    """A rating of a resource ID from the evaluated agent's ResourceView."""

    id: str = Field(min_length=1)
    tag: ReflectionRating


class TaggedReflection(ReflectionBaseModel):
    """One evidence-backed reflection. submit_reflections accepts a list of these."""

    reasoning: str = Field(min_length=1, description="Concise evidence-based explanation of the finding.")
    error_identification: str | None
    root_cause_analysis: str | None
    correct_approach: str | None
    key_insight: str = Field(min_length=1)
    event_ids: list[str] = Field(min_length=1)
    bullet_tags: list[ResourceTag]


class ResourceReference(OutputBaseModel):
    """A rating of the agent lines at ``ref``; ranks go to the innermost section containing them."""

    ref: str = Field(pattern=r"^[^#\s]+#L[1-9][0-9]*(-L[1-9][0-9]*)?$",
                     description="Agent file lines, e.g. skills/debug/SKILL.md#L14-L25 or hooks.json#L3-L9.")
    tag: ReflectionRating


class ReferencedReflection(ReflectionBaseModel):
    """One reflection on the supplied history, citing agent files by line. submit_reflections accepts a list.

    The whole supplied history is the evidence for each rating; no event IDs are cited.
    """

    reasoning: str = Field(min_length=1, description="Concise evidence-based explanation of the finding.")
    error_identification: str | None
    root_cause_analysis: str | None
    correct_approach: str | None
    key_insight: str = Field(min_length=1)
    resource_refs: list[ResourceReference]


class CurationBaseModel(OutputBaseModel):
    """Custom reports need an explicit operation adapter before application."""


def schema_for(spec):
    if spec is None:
        return None
    if isinstance(spec, type) and not issubclass(spec, BaseModel):
        raise TypeError("output_schema classes must inherit Pydantic BaseModel")
    schema = spec.model_json_schema(schema_generator=ResourceSchema) if isinstance(spec, type) else spec
    if not isinstance(schema, (dict, bool)):
        raise TypeError("output_schema must be a Pydantic model class or JSON Schema")
    # Detach caller state before validation/execution.
    schema = json.loads(json.dumps(schema))
    validator = validators.validator_for(schema, default=Draft202012Validator)
    validator.check_schema(schema)

    def local_only(node):
        if isinstance(node, dict):
            for key in ("$ref", "$dynamicRef"):
                if key in node and not node[key].startswith("#"):
                    raise ValueError("Only local JSON Schema references are supported")
            for value in node.values():
                local_only(value)
        elif isinstance(node, list):
            for value in node:
                local_only(value)
    local_only(schema)
    return schema


def _validator(schema):
    return validators.validator_for(schema)(schema, registry=Registry())


def native_output_schema(schema):
    """Use native strict output only for a conservatively supported schema subset.

    Optional/defaulted properties remain optional in the host schema; do not rewrite
    them into required fields just to meet the provider's strict-output dialect.
    """
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return None
    allowed = {"type", "properties", "required", "additionalProperties", "$defs", "$ref",
               "title", "description", "enum", "anyOf", "items"}

    def convert(node):
        if not isinstance(node, dict):
            raise TypeError("Unsupported native schema node")
        node = {key: value for key, value in node.items() if not key.startswith("x-thearc-")}
        if set(node) - allowed:
            raise ValueError("Schema needs host validation")
        if node.get("type") == "object" and (
            node.get("additionalProperties") is not False
            or set(node.get("required", [])) != set(node.get("properties", {}))
        ):
            raise ValueError("Native output requires all object fields and closed properties")
        result = dict(node)
        for key in ("properties", "$defs"):
            if key in node:
                result[key] = {name: convert(value) for name, value in node[key].items()}
        if "items" in node:
            result["items"] = convert(node["items"])
        if "anyOf" in node:
            result["anyOf"] = [convert(value) for value in node["anyOf"]]
        return result
    try:
        return convert(schema)
    except (TypeError, ValueError):
        return None


def parse_output(text, spec, schema, *, agent=None, validate_resources=False):
    if schema is None:
        return text
    try:
        data = json.loads(text)
        _validator(schema).validate(data)
        output = spec.model_validate(data) if isinstance(spec, type) else data
        if validate_resources:
            if agent is None:
                raise ValueError("Resource validation requires a MetaAgent")
            if isinstance(output, OutputBaseModel):
                output.validate_against(agent)
            else:
                validate_references(data, schema, agent)
        return output
    except Exception as exc:
        raise OutputValidationError(f"Invalid reflection output: {exc}", response=text) from exc


def validate_references(data, schema, agent):
    """Walk schema annotations, including local refs, union branches and containers."""
    if not isinstance(agent, MetaAgent):
        raise TypeError("Resource validation requires a MetaAgent")

    def walk(value, node, path, seen):
        if not isinstance(node, dict):
            return
        if node.get("x-thearc-change-target"):
            return  # Existence depends on ADD/EDIT/REMOVE; the operation adapter validates it.
        key = (id(value), id(node))
        if key in seen:
            return
        seen = seen | {key}
        if "$ref" in node:
            ref = node["$ref"]
            resolved = schema
            if ref != "#":
                if not ref.startswith("#/"):
                    raise ValueError("Resource schemas require local JSON pointer references")
                for token in ref[2:].split("/"):
                    resolved = resolved[token.replace("~1", "/").replace("~0", "~")]
            walk(value, resolved, path, seen)
        if node.get("x-thearc-resource"):
            try:
                agent.read_target(ResourceTarget.model_validate(value))
            except (KeyError, ValueError) as exc:
                raise OutputValidationError(f"{path or '$'}: resource or section does not exist: {value}") from exc
        if isinstance(value, dict):
            evaluated = set()
            for name, child in node.get("properties", {}).items():
                if name in value:
                    evaluated.add(name)
                    walk(value[name], child, f"{path}.{name}".lstrip("."), seen)
            for pattern, child in node.get("patternProperties", {}).items():
                for name, item in value.items():
                    if re.search(pattern, name):
                        evaluated.add(name)
                        walk(item, child, f"{path}[{name!r}]", seen)
            additional = node.get("additionalProperties")
            if isinstance(additional, dict):
                for name in value.keys() - evaluated:
                    walk(value[name], additional, f"{path}[{name!r}]", seen)
            for name, child in node.get("dependentSchemas", {}).items():
                if name in value:
                    walk(value, child, path, seen)
        if isinstance(value, list):
            prefix = node.get("prefixItems", [])
            for i, item in enumerate(value):
                walk(item, prefix[i] if i < len(prefix) else node.get("items", {}), f"{path}[{i}]", seen)
                contains = node.get("contains")
                if isinstance(contains, dict) and _validator(schema).evolve(schema=contains).is_valid(item):
                    walk(item, contains, f"{path}[{i}]", seen)
        if "if" in node:
            branch = "then" if _validator(schema).evolve(schema=node["if"]).is_valid(value) else "else"
            walk(value, node.get(branch, {}), path, seen)
        for operator in ("allOf", "anyOf", "oneOf"):
            for branch in node.get(operator, []):
                if operator == "allOf" or _validator(schema).evolve(schema=branch).is_valid(value):
                    walk(value, branch, path, seen)
    walk(data, schema, "", set())


class ResourceAssessment(ReflectionBaseModel):
    resource: ResourceTarget
    rating: ReflectionRating
    reason: str = Field(min_length=1)
    event_ids: list[str] = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)


class ResourceAssessmentReport(ReflectionBaseModel):
    summary: str
    findings: list[ResourceAssessment] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    def assessment_items(self, history):
        """Resolve only caller-supplied immutable evidence, never IDs guessed from text."""
        events = history.selected_events if hasattr(history, "selected_events") else history
        sessions = {}
        for event in events:
            if event.id in sessions:
                raise ValueError("Duplicate evidence event identity")
            sessions[event.id] = event.session_id
        result = []
        for finding in self.findings:
            if any(event_id not in sessions for event_id in finding.event_ids):
                raise ValueError("Assessment cites events outside the supplied history")
            result.append(ReflectionItem(target=finding.resource, rating=finding.rating, reason=finding.reason,
                                         limitations=finding.limitations,
                                         evidence=[{"event_id": eid, "session_id": sessions[eid]}
                                                   for eid in finding.event_ids]))
        return result


class CurationReport(CurationBaseModel):
    summary: str
    changes: list[Curation] = Field(default_factory=list)

    def validate_against(self, agent):
        candidate = agent.model_copy(deep=True)
        for i, change in enumerate(self.changes):
            try:
                candidate.apply_change(change.target, change.operation, change.value)
            except (KeyError, ValueError) as exc:
                raise OutputValidationError(f"changes[{i}].target: {exc}") from exc
        return self
