import json
from pathlib import Path

from thearc import SessionLog, SessionSearchEngine


def create_dummy_jsonl(tmp_path: Path, filename: str, lines_data: list) -> Path:
    file_path = tmp_path / filename
    with open(file_path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(item) + "\n" for item in lines_data)
    return file_path


def test_session_log_parsing(tmp_path):
    log_data = [
        {"type": "USER_INPUT", "content": "How do I fix Docker build permissions?"},
        {"type": "PLANNER_RESPONSE", "content": "Checking docker group permissions.", 
         "tool_calls": [{"name": "run_command", "toolAction": "Running docker info"}]},
        {"type": "PLANNER_RESPONSE", "content": "Add user to docker group.", "toolAction": "Modifying group"},
    ]
    log_file = create_dummy_jsonl(tmp_path, "session_001.jsonl", log_data)

    session = SessionLog.from_jsonl(log_file)
    assert session.session_id == "session_001"
    assert len(session.entries) == 3
    assert len(session.user_messages) == 1
    assert "Docker build permissions" in session.user_messages[0]
    assert len(session.tool_calls) >= 1
    assert "run_command" in session.tool_calls or "Running docker info" in session.tool_calls


def test_session_search_engine(tmp_path):
    # Session 1: Docker / Container
    create_dummy_jsonl(tmp_path, "docker_session.jsonl", [
        {"type": "USER_INPUT", "content": "Debug docker container build failure"},
        {"type": "PLANNER_RESPONSE", 
         "content": "Inspecting Dockerfile and docker daemon logs", "tool_calls": [{"name": "run_command"}]},
    ])

    # Session 2: Kubernetes / K8s
    create_dummy_jsonl(tmp_path, "k8s_session.jsonl", [
        {"type": "USER_INPUT", "content": "Kubernetes pod crashing crashloopbackoff"},
        {"type": "PLANNER_RESPONSE", 
         "content": "Checking kubectl logs and describe pod", "tool_calls": [{"name": "kubectl_get"}]},
    ])

    # Session 3: Docker Compose / Container
    create_dummy_jsonl(tmp_path, "compose_session.jsonl", [
        {"type": "USER_INPUT", "content": "Docker compose up container exited with code 1"},
        {"type": "PLANNER_RESPONSE", 
         "content": "Reading docker-compose.yml configuration", "tool_calls": [{"name": "run_command"}]},
    ])

    engine = SessionSearchEngine()
    loaded_count = engine.load_directory(tmp_path)
    assert loaded_count == 3

    # Test Search Query
    results = engine.search(query="docker container build", top_k=5)
    assert len(results) >= 2
    session_ids = [r.session.session_id for r in results]
    assert "docker_session" in session_ids or "compose_session" in session_ids

    # Test Find Similar Sessions
    target_session = engine.sessions["docker_session"]
    similar = engine.find_similar(target=target_session, top_k=2)
    assert len(similar) >= 1
    assert similar[0].session.session_id in ("compose_session", "k8s_session")

    # Test Pair Similarities
    pairs = engine.find_similar_pairs(min_similarity=0.1, top_k=5)
    assert len(pairs) >= 1

    # Test Clustering
    clusters = engine.cluster_sessions(n_clusters=2)
    assert len(clusters) <= 2
    assert sum(c.size for c in clusters) == 3
