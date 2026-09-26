from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

try:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.json as paj
    HAS_PYARROW = True
except ImportError:
    pa = None
    paj = None
    pc = None
    HAS_PYARROW = False

STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can", "can't", "cannot", "could",
    "did", "do", "does", "doing", "don't", "down", "during", "each", "few", "for",
    "from", "further", "had", "has", "have", "having", "he", "her", "here", "hers",
    "herself", "him", "himself", "his", "how", "i", "if", "in", "into", "is", "it",
    "its", "itself", "just", "me", "more", "most", "my", "myself", "no", "nor",
    "not", "of", "off", "on", "once", "only", "or", "other", "our", "ours",
    "ourselves", "out", "over", "own", "same", "she", "should", "so", "some",
    "such", "than", "that", "the", "their", "theirs", "them", "themselves", "then",
    "there", "these", "they", "this", "those", "through", "to", "too", "under",
    "until", "up", "very", "was", "we", "were", "what", "when", "where", "which",
    "while", "who", "whom", "why", "with", "would", "you", "your", "yours",
    "yourself", "yourselves"
}

def _tokenize(text: str) -> list[str]:
    """Tokenize raw text into normalized words, excluding common stopwords."""
    words = re.findall(r'\b[a-zA-Z0-9_\-\./]{2,}\b', text.lower())
    return [w for w in words if w not in STOPWORDS and not w.isdigit()]


def _term_frequencies(tokens: list[str]) -> dict[str, float]:
    """Calculate term frequencies for a list of tokens."""
    if not tokens:
        return {}
    counts: dict[str, int] = {}
    for t in tokens:
        counts[t] = counts.get(t, 0) + 1
    total = float(len(tokens))
    return {t: count / total for t, count in counts.items()}


def _compute_doc_freqs(sessions: list[SessionLog]) -> dict[str, int]:
    """Compute document frequency (DF) for tokens across a session collection."""
    df: dict[str, int] = {}
    for session in sessions:
        tokens = set(_tokenize(session.full_searchable_text()))
        for t in tokens:
            df[t] = df.get(t, 0) + 1
    return df


def _tfidf_vector(tokens: list[str], doc_freqs: dict[str, int], num_docs: int) -> dict[str, float]:
    """Compute TF-IDF feature vector for a token sequence."""
    tf = _term_frequencies(tokens)
    vector: dict[str, float] = {}
    for token, tf_val in tf.items():
        df_val = doc_freqs.get(token, 1)
        idf = math.log((1.0 + num_docs) / (1.0 + df_val)) + 1.0
        vector[token] = tf_val * idf
    return vector


def _cosine_similarity(vec1: dict[str, float], vec2: dict[str, float]) -> float:
    """Calculate cosine similarity between two sparse feature vectors."""
    common = set(vec1.keys()) & set(vec2.keys())
    if not common:
        return 0.0

    dot_product = sum(vec1[k] * vec2[k] for k in common)
    norm1 = math.sqrt(sum(v * v for v in vec1.values()))
    norm2 = math.sqrt(sum(v * v for v in vec2.values()))

    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0
    return dot_product / (norm1 * norm2)


def _jaccard_similarity(set1: set, set2: set) -> float:
    """Calculate Jaccard similarity between two sets."""
    if not set1 and not set2:
        return 1.0
    if not set1 or not set2:
        return 0.0
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return float(intersection) / float(union)


class SessionLog(BaseModel):
    """
    Object representation of a JSONL agent session transcript log file.
    Parses steps, user messages, assistant responses, tool calls, and raw text.
    """
    session_id: str
    file_path: Path | None = None
    entries: list[dict[str, Any]] = Field(default_factory=list)
    user_messages: list[str] = Field(default_factory=list)
    agent_messages: list[str] = Field(default_factory=list)
    tool_calls: list[str] = Field(default_factory=list)
    raw_text: str = ""

    @classmethod
    def from_jsonl(cls, path: str | Path) -> SessionLog:
        """Load and parse a JSONL session log file from disk."""
        p = Path(path).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"Session JSONL log file not found: {path}")

        entries = []
        user_msgs = []
        agent_msgs = []
        tools = []
        raw_lines = []

        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if not line_str:
                    continue
                raw_lines.append(line_str)
                data = json.loads(line_str)
                entries.append(data)

                # Extract text content
                msg_type = str(data.get("type") or data.get("role") or data.get("source") or "").lower()
                content = str(data.get("content") or data.get("text") or data.get("message") or "")

                if "user" in msg_type or "input" in msg_type:
                    if content:
                        user_msgs.append(content)
                elif "assistant" in msg_type or "planner" in msg_type or "model" in msg_type:
                    if content:
                        agent_msgs.append(content)
                else:
                    if content:
                        agent_msgs.append(content)

                # Extract tool calls
                if "tool_calls" in data and isinstance(data["tool_calls"], list):
                    for tc in data["tool_calls"]:
                        if isinstance(tc, dict):
                            tc_name = (
                                tc.get("name")
                                or tc.get("function", {}).get("name")
                                or tc.get("toolAction")
                                or "tool"
                            )
                            tools.append(str(tc_name))
                        elif isinstance(tc, str):
                            tools.append(tc)
                elif "tool_name" in data:
                    tools.append(str(data["tool_name"]))
                elif "toolAction" in data:
                    tools.append(str(data["toolAction"]))
         

        session_id = p.stem
        raw_text = "\n".join(raw_lines)

        return cls(
            session_id=session_id,
            file_path=p,
            entries=entries,
            user_messages=user_msgs,
            agent_messages=agent_msgs,
            tool_calls=tools,
            raw_text=raw_text,
        )

    def full_searchable_text(self) -> str:
        """Return aggregated text content across prompts, responses, and tools."""
        parts = []
        parts.extend(self.user_messages)
        parts.extend(self.agent_messages)
        parts.extend(self.tool_calls)
        return " ".join(parts)


class SessionSearchResult(BaseModel):
    """Result item returned by SessionSearchEngine search or similarity queries."""
    session: SessionLog
    score: float
    match_reasons: list[str] = Field(default_factory=list)


class SessionCluster(BaseModel):
    """Cluster of similar session logs."""
    cluster_id: int
    label: str
    size: int
    sessions: list[SessionLog] = Field(default_factory=list)


class SessionSearchEngine(BaseModel):
    """
    Search and similarity engine for loading, indexing, querying,
    and analyzing similarities across session logs in JSONL format.
    """
    sessions: dict[str, SessionLog] = Field(default_factory=dict)

    def load_directory(
        self,
        dir_path: str | Path,
        pattern: str = "*.jsonl",
        recursive: bool = True
    ) -> int:
        """
        Load all JSONL session files from directory or subdirectories into index.
        Returns count of loaded session log files.
        """
        p = Path(dir_path).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"Directory not found: {dir_path}")

        glob_fn = p.rglob if recursive else p.glob
        count = 0
        for log_file in glob_fn(pattern):
            if log_file.is_file():
                s_log = SessionLog.from_jsonl(log_file)
                self.sessions[s_log.session_id] = s_log
                count += 1
        return count

    def add_session(self, session: SessionLog | Path | str) -> SessionLog:
        """Add a single SessionLog object or JSONL file path to the engine index."""
        if isinstance(session, (str, Path)):
            session = SessionLog.from_jsonl(session)
        self.sessions[session.session_id] = session
        return session

    def search(
        self,
        query: str,
        top_k: int = 10,
        min_score: float = 0.0
    ) -> list[SessionSearchResult]:
        """
        Search sessions by query text relevance using TF-IDF token scoring.
        """
        if not self.sessions or not query.strip():
            return []

        tokens = _tokenize(query)
        if not tokens:
            return []

        doc_freqs = _compute_doc_freqs(list(self.sessions.values()))
        num_docs = len(self.sessions)

        query_vec = _tfidf_vector(tokens, doc_freqs, num_docs)

        results = []
        for s_log in self.sessions.values():
            doc_tokens = _tokenize(s_log.full_searchable_text())
            doc_vec = _tfidf_vector(doc_tokens, doc_freqs, num_docs)
            score = _cosine_similarity(query_vec, doc_vec)

            if score >= min_score and score > 0:
                reasons = []
                matched_tokens = set(tokens) & set(doc_tokens)
                if matched_tokens:
                    reasons.append(f"Matched keywords: {', '.join(sorted(matched_tokens)[:5])}")
                results.append(SessionSearchResult(
                    session=s_log,
                    score=round(score, 4),
                    match_reasons=reasons
                ))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    def find_similar(
        self,
        target: SessionLog | Path | str,
        top_k: int = 5,
        min_score: float = 0.0
    ) -> list[SessionSearchResult]:
        """
        Find top_k session logs most similar to a target session log.
        Uses hybrid TF-IDF text similarity + tool call signature Jaccard similarity.
        """
        if isinstance(target, (str, Path)):
            target = SessionLog.from_jsonl(target)

        target_tokens = _tokenize(target.full_searchable_text())
        target_tools = set(target.tool_calls)

        all_sessions = list(self.sessions.values())
        doc_freqs = _compute_doc_freqs(all_sessions + [target])
        num_docs = len(all_sessions) + 1

        target_vec = _tfidf_vector(target_tokens, doc_freqs, num_docs)

        results = []
        for s_log in self.sessions.values():
            if s_log.session_id == target.session_id:
                continue

            cand_tokens = _tokenize(s_log.full_searchable_text())
            cand_vec = _tfidf_vector(cand_tokens, doc_freqs, num_docs)

            text_sim = _cosine_similarity(target_vec, cand_vec)
            cand_tools = set(s_log.tool_calls)
            tool_sim = _jaccard_similarity(target_tools, cand_tools)

            # Combined hybrid similarity score (70% text content, 30% tool usage)
            combined_score = (0.7 * text_sim) + (0.3 * tool_sim)

            if combined_score >= min_score and combined_score > 0:
                reasons = []
                shared_tools = target_tools & cand_tools
                if shared_tools:
                    reasons.append(f"Shared tools: {', '.join(sorted(shared_tools))}")
                common_words = set(target_tokens) & set(cand_tokens)
                if common_words:
                    reasons.append(f"Common topics: {', '.join(sorted(common_words)[:5])}")

                results.append(SessionSearchResult(
                    session=s_log,
                    score=round(combined_score, 4),
                    match_reasons=reasons
                ))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    def find_similar_pairs(
        self,
        min_similarity: float = 0.3,
        top_k: int = 10
    ) -> list[tuple[SessionLog, SessionLog, float]]:
        """
        Scan all indexed session logs and return top pairs of similar sessions.
        Returns list of (session_a, session_b, similarity_score).
        """
        session_list = list(self.sessions.values())
        if len(session_list) < 2:
            return []

        doc_freqs = _compute_doc_freqs(session_list)
        num_docs = len(session_list)
        vectors = {
            s.session_id: _tfidf_vector(_tokenize(s.full_searchable_text()), doc_freqs, num_docs)
            for s in session_list
        }
        tools_map = {s.session_id: set(s.tool_calls) for s in session_list}

        pairs = []
        for i in range(len(session_list)):
            for j in range(i + 1, len(session_list)):
                sa = session_list[i]
                sb = session_list[j]

                text_sim = _cosine_similarity(vectors[sa.session_id], vectors[sb.session_id])
                tool_sim = _jaccard_similarity(tools_map[sa.session_id], tools_map[sb.session_id])
                sim = (0.7 * text_sim) + (0.3 * tool_sim)

                if sim >= min_similarity:
                    pairs.append((sa, sb, round(sim, 4)))

        pairs.sort(key=lambda p: p[2], reverse=True)
        return pairs[:top_k]

    def cluster_sessions(self, n_clusters: int = 5) -> list[SessionCluster]:
        """
        Cluster indexed session logs into groups of similar sessions using K-Medoids similarity grouping.
        """
        session_list = list(self.sessions.values())
        if not session_list:
            return []

        n_clusters = min(n_clusters, len(session_list))
        doc_freqs = _compute_doc_freqs(session_list)
        num_docs = len(session_list)
        vectors = {
            s.session_id: _tfidf_vector(_tokenize(s.full_searchable_text()), doc_freqs, num_docs)
            for s in session_list
        }

        clusters: list[list[SessionLog]] = []
        centroids: list[SessionLog] = []

        for s in session_list:
            best_idx = -1
            best_sim = -1.0

            for idx, centroid in enumerate(centroids):
                sim = _cosine_similarity(vectors[s.session_id], vectors[centroid.session_id])
                if sim > best_sim:
                    best_sim = sim
                    best_idx = idx

            if best_sim > 0.2 and best_idx != -1:
                clusters[best_idx].append(s)
            elif len(centroids) < n_clusters:
                centroids.append(s)
                clusters.append([s])
            else:
                if best_idx != -1:
                    clusters[best_idx].append(s)
                else:
                    clusters[0].append(s)

        res = []
        for idx, (centroid, members) in enumerate(zip(centroids, clusters)):
            # Generate cluster label from top tokens
            cluster_tokens: list[str] = []
            for m in members:
                cluster_tokens.extend(_tokenize(m.full_searchable_text()))
            t_counts = _term_frequencies(cluster_tokens)
            top_words = sorted(t_counts.keys(), key=lambda w: t_counts[w], reverse=True)[:3]
            label = ", ".join(top_words) if top_words else f"Cluster-{idx+1}"

            res.append(SessionCluster(
                cluster_id=idx + 1,
                label=label,
                size=len(members),
                sessions=members
            ))
        return res

    def to_arrow_table(self) -> Any:
        """
        Convert indexed session logs into a PyArrow Table for zero-copy high-speed
        columnar analytics, vectorized filter queries, and Parquet exporting.
        Returns a pyarrow.Table object (requires pyarrow to be installed).
        """
        if not HAS_PYARROW:
            raise RuntimeError("pyarrow is not installed. Install it via 'pip install pyarrow' to use Arrow tables.")

        rows = []
        for s in self.sessions.values():
            rows.append({
                "session_id": s.session_id,
                "file_path": str(s.file_path) if s.file_path else "",
                "user_messages_count": len(s.user_messages),
                "agent_messages_count": len(s.agent_messages),
                "tool_calls_count": len(s.tool_calls),
                "tool_calls": ", ".join(s.tool_calls),
                "user_content": "\n".join(s.user_messages),
                "full_text": s.full_searchable_text(),
            })
        return pa.Table.from_pylist(rows)

    def export_parquet(self, output_path: str | Path) -> Path:
        """Export indexed session logs to a compressed Apache Parquet file for fast querying."""
        if not HAS_PYARROW:
            raise RuntimeError(
                "pyarrow is not installed. Install it via 'pip install pyarrow' to export Parquet files."
            )
        import pyarrow.parquet as pq

        p = Path(output_path).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        table = self.to_arrow_table()
        pq.write_table(table, p)
        return p
