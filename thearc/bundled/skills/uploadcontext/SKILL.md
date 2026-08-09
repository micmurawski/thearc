---
name: uploadcontext
description: Upload agent conversation history/transcript to S3 with workspace and metadata attributes.
---

# Upload Context Skill

This skill allows the agent to extract and upload its current conversation history/transcript to S3.

## Usage

When the user invokes `/uploadcontext <MAP of attributes>` (for example: `/uploadcontext workspace=example,name=example`), execute the CLI command:

```bash
thearc uploadcontext "workspace=example,name=example"
```

Options available for `thearc uploadcontext`:
- `"key1=val1,key2=val2"`: Key-value attributes map (e.g. `workspace`, `name`, `environment`).
- `--file <path>`: Specify transcript file explicitly if auto-detection is not used.
- `--bucket <bucket_name>`: Override default target S3 bucket.
- `--dry-run`: Test S3 key generation and transcript resolution without connecting to S3.

## Output

The tool will return the generated S3 URI (e.g. `s3://thearc-contexts/example/example/20260809_190000_transcript.jsonl`) along with upload status and file metadata.
