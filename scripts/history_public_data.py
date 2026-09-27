"""Download pinned public history data, recording checksums and source metadata.

Run with --download; no dataset code is imported or executed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

DATASETS = {
    "commons": "trace-commons/agent-traces",
    "dataclaw-claude": "peteromallet/dataclaw-peteromallet",
    "dataclaw-codex": "peteromallet/my-personal-codex-data",
}


def request(url):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "thearc-history-test/0.1"}), timeout=120
    )


def json_request(url):
    with request(url) as response:
        return json.load(response)


def inventory():
    jobs = []
    for label, dataset in DATASETS.items():
        info = json_request(f"https://huggingface.co/api/datasets/{dataset}")
        revision = info["sha"]
        url = f"https://huggingface.co/api/datasets/{dataset}/tree/{revision}?recursive=true&limit=1000"
        files = []
        while url:
            with request(url) as response:
                files.extend(json.load(response))
                links = response.headers.get("Link", "")
                url = next(
                    (part.split("<", 1)[1].split(">", 1)[0] for part in links.split(",") if 'rel="next"' in part), None
                )
        for item in files:
            name = item["path"]
            if item["type"] != "file":
                continue
            if label == "commons" and not (name.startswith("sessions/claude_code/") and name.endswith(".jsonl")):
                if name != "README.md":
                    continue
            elif label != "commons" and not name.endswith((".jsonl", ".json", ".md")):
                continue
            jobs.append(
                {
                    "label": label,
                    "path": f"{label}/{name}",
                    "revision": revision,
                    "url": f"https://huggingface.co/datasets/{dataset}/resolve/{revision}/"
                    f"{urllib.parse.quote(name)}?download=true",
                    "size": item["size"],
                    "expected_sha256": item.get("lfs", {}).get("oid"),
                    "license": info.get("cardData", {}).get("license"),
                }
            )
    pi = json_request("https://api.github.com/repos/earendil-works/pi/commits/main")["sha"]
    for name in ["before-compaction.jsonl", "large-session.jsonl"]:
        jobs.append(
            {
                "label": "pi",
                "path": f"pi/{name}",
                "revision": pi,
                "url": f"https://raw.githubusercontent.com/earendil-works/pi/{pi}/"
                f"packages/coding-agent/test/fixtures/{name}",
                "license": "see pi/LICENSE",
            }
        )
    jobs.append(
        {
            "label": "pi",
            "path": "pi/LICENSE",
            "revision": pi,
            "url": f"https://raw.githubusercontent.com/earendil-works/pi/{pi}/LICENSE",
        }
    )
    release = json_request("https://api.github.com/repos/uw-syfi/TraceLab/releases/tags/v0.0.1")
    for asset in release["assets"]:
        jobs.append(
            {
                "label": "tracelab",
                "path": f"tracelab/{asset['name']}",
                "url": asset["browser_download_url"],
                "revision": "v0.0.1",
                "size": asset["size"],
                "expected_sha256": asset.get("digest", "").removeprefix("sha256:"),
                "license": "CC-BY-4.0",
            }
        )
    jobs.append(
        {
            "label": "tracelab",
            "path": "tracelab/LICENSE-DATASET.md",
            "revision": "v0.0.1",
            "url": "https://raw.githubusercontent.com/uw-syfi/TraceLab/v0.0.1/LICENSE-DATASET.md",
        }
    )
    return jobs


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(root, job):
    destination = root / job["path"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        digest = file_hash(destination)
        if (not job.get("size") or destination.stat().st_size == job["size"]) and (
            not job.get("expected_sha256") or digest == job["expected_sha256"]
        ):
            return dict(job, sha256=digest, downloaded_bytes=destination.stat().st_size, status="cached")
    temporary = destination.with_name(destination.name + ".part")
    error = None
    for attempt in range(3):
        try:
            with request(job["url"]) as response, temporary.open("wb") as target:
                for block in iter(lambda: response.read(1024 * 1024), b""):
                    target.write(block)
            digest = file_hash(temporary)
            if job.get("size") is not None and temporary.stat().st_size != job["size"]:
                raise ValueError("Downloaded size does not match source inventory")
            if job.get("expected_sha256") and digest != job["expected_sha256"]:
                raise ValueError("Downloaded checksum does not match published checksum")
            temporary.replace(destination)
            return dict(job, sha256=digest, downloaded_bytes=destination.stat().st_size, status="downloaded")
        except (OSError, ValueError) as exc:
            error = str(exc)
            time.sleep(attempt + 1)
    return dict(job, status="failed", error=error)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(".history-bench/downloads"))
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    manifest_path = args.root / "manifest.json"
    if manifest_path.exists():
        jobs = json.loads(manifest_path.read_text())["files"]
    else:
        jobs = inventory()
    manifest = {"files": jobs}
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"files": len(jobs), "bytes_listed": sum(job.get("size", 0) for job in jobs)}), flush=True)
    if not args.download:
        return
    results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(download, args.root, job) for job in jobs]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(
                json.dumps(
                    {
                        "path": result["path"],
                        "status": result["status"],
                        "bytes": result.get("downloaded_bytes"),
                        "error": result.get("error"),
                    }
                ),
                flush=True,
            )
            manifest_path.write_text(
                json.dumps(
                    {"files": results + [job for job in jobs if job["path"] not in {r["path"] for r in results}]},
                    indent=2,
                )
            )
    manifest_path.write_text(json.dumps({"files": sorted(results, key=lambda item: item["path"])}, indent=2))
    if any(result["status"] == "failed" for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
