import json

import pytest
from pydantic import Field

from thearc import ResourceTarget
from thearc.resources import CurationReport, OutputValidationError, ReflectionBaseModel
from thearc.resources.schemas import native_output_schema, parse_output, schema_for


class Finding(ReflectionBaseModel):
    resource: ResourceTarget | None = Field(description="Evaluated resource")


class Report(ReflectionBaseModel):
    findings: list[Finding]
    related_resources: dict[str, ResourceTarget] = Field(default_factory=dict)


@pytest.mark.parametrize("raw", [False, True])
def test_schema_descriptions_and_nested_reference_validation(agent, raw):
    schema = json.loads(Report.to_json_schema())
    assert schema["$defs"]["ResourceTarget"]["x-thearc-resource"]
    assert schema["$defs"]["Finding"]["properties"]["resource"]["description"] == "Evaluated resource"
    spec = schema if raw else Report
    valid = {"findings": [{"resource": {"kind": "skill", "name": "debug", "section": "Retry"}},
                          {"resource": None}]}
    parse_output(json.dumps(valid), spec, schema, agent=agent, validate_resources=True)
    valid["findings"][0]["resource"]["name"] = "absent"
    with pytest.raises(OutputValidationError, match=r"findings\[0\].resource"):
        parse_output(json.dumps(valid), spec, schema, agent=agent, validate_resources=True)
    parse_output(json.dumps(valid), spec, schema, agent=agent, validate_resources=False)


def test_raw_schema_and_invalid_output_errors():
    with pytest.raises(ValueError, match="local"):
        schema_for({"$ref": "https://example.com/schema"})
    with pytest.raises(OutputValidationError) as raised:
        parse_output('{"findings": 3}', Report, schema_for(Report))
    assert raised.value.response == '{"findings": 3}'
    assert native_output_schema(schema_for(Report)) is None  # Optional fields must remain optional.


def test_add_then_edit_validation_and_input_preserved(agent):
    before = agent.model_dump()
    report = CurationReport(summary="Add guidance", changes=[
        {"id": "add", "target": {"kind": "skill", "name": "new"}, "operation": "ADD",
         "value": {"instructions": "Start"}, "reflection_ids": ["r"]},
        {"id": "edit", "target": {"kind": "skill", "name": "new"}, "operation": "EDIT",
         "value": {"instructions": "Better"}, "reflection_ids": ["r"]},
    ])
    report.validate_against(agent)
    assert agent.model_dump() == before
    report.changes.reverse()
    with pytest.raises(OutputValidationError, match=r"changes\[0\].target"):
        report.validate_against(agent)
