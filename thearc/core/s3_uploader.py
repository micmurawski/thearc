import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
import logging

logger = logging.getLogger("thearc")

def parse_attributes(attr_input: Any) -> Dict[str, str]:
    """
    Parse attribute map input into a dictionary of key-value strings.

    Supports formats:
    - "workspace=example,name=example"
    - "workspace=example name=example"
    - tuple of ("workspace=example", "name=example")
    - dict
    """
    attributes: Dict[str, str] = {}
    if not attr_input:
        return attributes

    if isinstance(attr_input, dict):
        return {str(k): str(v) for k, v in attr_input.items()}

    if isinstance(attr_input, (list, tuple)):
        raw_str = " ".join(attr_input)
    else:
        raw_str = str(attr_input)

    # Split by comma or whitespace, respecting quoted strings if any
    pairs = re.split(r'[, \t]+', raw_str.strip())
    for pair in pairs:
        if "=" in pair:
            key, val = pair.split("=", 1)
            key = key.strip()
            val = val.strip().strip("'\"")
            if key:
                attributes[key] = val
        elif pair.strip():
            # Loose value without key
            attributes[f"attr_{len(attributes)}"] = pair.strip()

    return attributes

def build_s3_key(file_path: Path, attributes: Dict[str, str], prefix: Optional[str] = None) -> str:
    """
    Construct S3 object key based on workspace, name, timestamp, and filename.
    """
    workspace = attributes.get("workspace", "default")
    name = attributes.get("name", "context")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = file_path.name

    prefix_part = f"{prefix.strip('/')}/" if prefix else ""
    key = f"{prefix_part}{workspace}/{name}/{timestamp}_{filename}"
    return key

def upload_context_to_s3(
    file_path: Path,
    attributes: Dict[str, str],
    bucket: Optional[str] = None,
    prefix: Optional[str] = None,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Upload a transcript/history file to S3 with key metadata.

    :param file_path: Path to transcript file
    :param attributes: Map of metadata attributes (workspace, name, etc.)
    :param bucket: Target S3 bucket name
    :param prefix: S3 key prefix folder
    :param dry_run: If True, simulate upload without AWS network calls
    :return: Result dict with s3_uri, bucket, key, attributes, size_bytes
    """
    file_path = Path(file_path).resolve()
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    target_bucket = bucket or attributes.get("bucket") or os.environ.get("THEARC_S3_BUCKET") or os.environ.get("AWS_S3_BUCKET") or "thearc-contexts"
    s3_key = build_s3_key(file_path, attributes, prefix=prefix)
    s3_uri = f"s3://{target_bucket}/{s3_key}"
    file_size = file_path.stat().st_size

    result = {
        "s3_uri": s3_uri,
        "bucket": target_bucket,
        "key": s3_key,
        "attributes": attributes,
        "file_path": str(file_path),
        "file_size_bytes": file_size,
        "dry_run": dry_run,
        "uploaded": False
    }

    if dry_run:
        result["message"] = f"[DRY RUN] Would upload {file_path} to {s3_uri}"
        return result

    try:
        import boto3
        from botocore.exceptions import NoCredentialsError, ClientError

        s3_client = boto3.client("s3")

        # Include attributes as S3 object metadata
        metadata = {str(k): str(v) for k, v in attributes.items()}

        s3_client.upload_file(
            Filename=str(file_path),
            Bucket=target_bucket,
            Key=s3_key,
            ExtraArgs={"Metadata": metadata}
        )

        result["uploaded"] = True
        result["message"] = f"Successfully uploaded {file_path.name} ({file_size} bytes) to {s3_uri}"
        return result

    except ImportError:
        result["message"] = f"[WARN] boto3 library not found. Simulated upload to {s3_uri}"
        return result
    except (NoCredentialsError, ClientError, Exception) as err:
        logger.warning(f"S3 upload error: {err}")
        result["error"] = str(err)
        result["message"] = f"[WARN] AWS S3 upload could not complete ({err}). Prepared S3 URI: {s3_uri}"
        return result
