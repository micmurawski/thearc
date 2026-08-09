import pytest
from pathlib import Path
from thearc.core.s3_uploader import parse_attributes, build_s3_key, upload_context_to_s3

def test_parse_attributes_str():
    res = parse_attributes("workspace=example,name=mycontext,env=prod")
    assert res["workspace"] == "example"
    assert res["name"] == "mycontext"
    assert res["env"] == "prod"

def test_parse_attributes_spaces():
    res = parse_attributes("workspace=demo name=test")
    assert res["workspace"] == "demo"
    assert res["name"] == "test"

def test_parse_attributes_dict():
    res = parse_attributes({"workspace": "ws1", "name": "n1"})
    assert res == {"workspace": "ws1", "name": "n1"}

def test_build_s3_key():
    p = Path("/tmp/sample_transcript.jsonl")
    attrs = {"workspace": "acme", "name": "chat1"}
    key = build_s3_key(p, attrs, prefix="uploads")
    assert key.startswith("uploads/acme/chat1/")
    assert key.endswith("_sample_transcript.jsonl")

def test_upload_dry_run(tmp_path):
    f = tmp_path / "dummy.jsonl"
    f.write_text("{\"role\": \"user\"}")

    res = upload_context_to_s3(
        file_path=f,
        attributes={"workspace": "ws", "name": "nm"},
        bucket="my-bucket",
        dry_run=True
    )
    assert res["dry_run"] is True
    assert res["bucket"] == "my-bucket"
    assert "s3://my-bucket/ws/nm/" in res["s3_uri"]
