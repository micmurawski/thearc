import hashlib
import html as _html
import json as _json
import pathlib
import time
from collections import deque

from . import AsyncNode, AsyncParallelBatchNode, BatchNode, Flow

_JS = (pathlib.Path(__file__).parent / "viz.js").read_text(encoding="utf-8")
_CSS = (pathlib.Path(__file__).parent / "viz.css").read_text(encoding="utf-8")


def _node_id(node):
    return getattr(node, "_flow_viz_id", f"{type(node).__name__}-{id(node)}")


def _extract_graph(start_node):
    nodes = []
    edges = []
    visited = set()

    def walk(node, parent_id=None, action=None):
        if not hasattr(node, "_flow_viz_id"):
            node._flow_viz_id = f"node-{len(nodes)}-{type(node).__name__}"
        nid = _node_id(node)
        if parent_id is not None:
            edges.append({"from": parent_id, "to": nid, "action": action or "default"})
        if nid in visited:
            return
        visited.add(nid)
        nodes.append(
            {
                "id": nid,
                "label": type(node).__name__,
                "is_batch": isinstance(node, BatchNode),
                "is_parallel": isinstance(node, AsyncParallelBatchNode),
                "is_async": isinstance(node, AsyncNode),
                "is_flow": isinstance(node, Flow),
                "metadata": getattr(node, "metadata", None),
            }
        )
        if isinstance(node, Flow) and node.start_node:
            walk(node.start_node, nid, "start")
        for act, nxt in node.successors.items():
            walk(nxt, nid, act)

    if start_node:
        walk(start_node)
    return {"nodes": nodes, "edges": edges}


# HTML
# Styled like resources/report.html: viz.css holds the theme tokens and the SVG only carries classes, so the graph
# follows the light/dark toggle too.

_NW, _NH = 176, 64
_SO = 5  # offset of the stacked cards behind a batch node
_SH = 4  # hard drop shadow under a card
_CW, _RH = 264, 112
_PAD = 44
_LABEL_CHARS = 21
_DASH, _DOT = "\u2013", " \u00b7 "
_STATUSES = ("pending", "running", "success", "failed", "skipped")
_FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap">\n'
)


def _bezier(x1, y1, x2, y2):
    off = max(30, abs(x2 - x1) * 0.5)
    return f"M {x1},{y1} C {x1 + off},{y1} {x2 - off},{y2} {x2},{y2}"


def _disp(nid, state, done):
    st = state.get(nid, {}).get("status", "pending")
    return "skipped" if (done and st == "pending") else st


def _kind(n):
    if n["is_flow"]:
        return "flow"
    if n["is_parallel"]:
        return "parallel batch"
    if n["is_batch"]:
        return "async batch" if n["is_async"] else "batch"
    return "async" if n["is_async"] else "node"


def _items_summary(items):
    if not items:
        return ""
    ok = sum(1 for item in items if item["status"] == "success")
    bad = sum(1 for item in items if item["status"] == "failed")
    running = sum(1 for item in items if item["status"] == "running")
    sub = f"{ok}/{len(items)}"
    if running:
        sub += f" ↻{running}"
    if bad:
        sub += f" ✗{bad}"
    return sub


def _elapsed(s):
    st, et = s.get("start_ts"), s.get("end_ts")
    return f"{(et or time.time()) - st:.1f}s" if st else ""


def _layout(graph):
    ids = [n["id"] for n in graph["nodes"]]
    if not ids:
        return {}, set()

    out = {}
    for e in graph["edges"]:
        out.setdefault(e["from"], []).append(e["to"])

    # dfs to classify back edges
    WHITE, GRAY, BLACK = range(3)
    color = {n: WHITE for n in ids}
    back = set()

    def dfs(u):
        color[u] = GRAY
        for v in out.get(u, []):
            if v not in color:
                continue
            if color[v] == GRAY:
                back.add((u, v))
            elif color[v] == WHITE:
                dfs(v)
        color[u] = BLACK

    for n in ids:
        if color[n] == WHITE:
            dfs(n)

    fwd_targets = {e["to"] for e in graph["edges"] if (e["from"], e["to"]) not in back}
    roots = [n for n in ids if n not in fwd_targets] or [ids[0]]
    col = {n: 0 for n in roots}
    seen, q = set(roots), deque(roots)
    while q:
        u = q.popleft()
        for v in out.get(u, []):
            if (u, v) in back:
                continue
            col[v] = max(col.get(v, 0), col[u] + 1)
            if v not in seen:
                seen.add(v)
                q.append(v)

    mc = max(col.values()) if col else 0
    for n in ids:
        if n not in col:
            col[n] = mc + 1

    by_col: dict = {}
    for n in ids:
        by_col.setdefault(col[n], []).append(n)
    row: dict = {}
    for bucket in by_col.values():
        for i, n in enumerate(bucket):
            row[n] = i

    return {n: (col[n], row[n]) for n in ids}, back


def _edge_label(x, y, act, src, tgt, src_anchor, tgt_anchor, attrs, back=False):
    cls = "edge-label back" if back else "edge-label"
    return (
        f'<text class="{cls}" x="{x:.0f}" y="{y:.0f}" text-anchor="middle" data-edge="1" {attrs}'
        f'data-src="{src}" data-src-anchor="{src_anchor}" data-tgt="{tgt}" data-tgt-anchor="{tgt_anchor}">'
        f"{_html.escape(act)}</text>"
    )


def _render_svg(graph, state, done, pos, back):
    nmap = {n["id"]: n for n in graph["nodes"]}
    if not pos:
        return '<svg id="flow-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 40"></svg>'
    mc = max(c for c, _ in pos.values())
    mr = max(r for _, r in pos.values())
    # A back edge between two top-row nodes loops above the graph; any other loops below it, clear of the cards.
    back_edges = [(e["from"], e["to"]) for e in graph["edges"] if (e["from"], e["to"]) in back]
    over = {edge for edge in back_edges if pos[edge[0]][1] == 0 and pos[edge[1]][1] == 0}
    n_over, n_under = len(over), len(back_edges) - len(over)
    top = _PAD + (n_over * 40 + 20 if n_over else 0)
    W = _PAD + mc * _CW + _NW + _PAD + 2 * _SO
    H = top + mr * _RH + _NH + _PAD + (n_under * 40 + 30 if n_under else 0)
    graph_signature = _json.dumps(
        {
            "nodes": [(node["id"], node["label"]) for node in graph["nodes"]],
            "edges": [(edge["from"], edge["to"], edge.get("action")) for edge in graph["edges"]],
        },
        sort_keys=True,
    )
    storage_key = hashlib.sha256(graph_signature.encode()).hexdigest()[:16]

    def xy(nid):
        c, r = pos[nid]
        return _PAD + c * _CW, top + r * _RH

    def rc(nid):  # right center of front card
        x, y = xy(nid)
        return x + _NW, y + _NH // 2

    def lc(nid):  # left center of front card
        x, y = xy(nid)
        return x, y + _NH // 2

    def bc(nid):  # bottom center of card
        x, y = xy(nid)
        return x + _NW // 2, y + _NH

    def tc(nid):  # top center of card
        x, y = xy(nid)
        return x + _NW // 2, y

    p = [
        (
            f'<svg id="flow-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
            f'preserveAspectRatio="xMidYMid meet" data-storage-key="{storage_key}" '
            f'data-node-w="{_NW}" data-node-h="{_NH}" role="img" aria-label="Flow graph">'
        ),
        "<defs>",
        (
            '<pattern id="dots" width="16" height="16" patternUnits="userSpaceOnUse">'
            '<rect class="grid-dot" x="0" y="0" width="1.5" height="1.5"/></pattern>'
        ),
        (
            '<marker id="arr" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">'
            '<polygon class="arrow" points="0 0,10 3.5,0 7"/></marker>'
        ),
        (
            '<marker id="arr-triple" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">'
            '<polygon class="arrow" points="0 0,8 3,0 6"/></marker>'
        ),
        (
            '<marker id="arr-bk" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">'
            '<polygon class="arrow-bk" points="0 0,10 3.5,0 7"/></marker>'
        ),
        "</defs>",
        '<rect x="-5000" y="-5000" width="10000" height="10000" fill="url(#dots)" id="vp-bg"/>',
        '<g id="vp">',
    ]
    # edges first, so cards stay above connectors
    n_drawn_over = n_drawn_under = 0
    for e in graph["edges"]:
        src, tgt, act = e["from"], e["to"], e.get("action", "default")
        if src not in pos or tgt not in pos:
            continue
        labelled = act not in ("default", "start")
        if (src, tgt) in back:
            if (src, tgt) in over:
                anchor, (bx, by), (tx, ty) = "t", tc(src), tc(tgt)
                drop = top - 30 - n_drawn_over * 40
                n_drawn_over += 1
            else:
                anchor, (bx, by), (tx, ty) = "b", bc(src), bc(tgt)
                drop = top + mr * _RH + _NH + 30 + n_drawn_under * 40
                n_drawn_under += 1
            d = f"M {bx},{by} C {bx},{drop} {tx},{drop} {tx},{ty}"
            back_attrs = f'data-back="1" data-drop="{drop}" data-label-dy="4" '
            p.append(
                f'<path class="edge back" d="{d}" marker-end="url(#arr-bk)" data-edge="1" {back_attrs}'
                f'data-src="{src}" data-src-anchor="{anchor}" data-tgt="{tgt}" data-tgt-anchor="{anchor}"/>'
            )
            if labelled:
                apex = 0.125 * (by + ty) + 0.75 * drop  # the curve's midpoint
                p.append(_edge_label((bx + tx) / 2, apex - 4, act, src, tgt, anchor, anchor, back_attrs, back=True))
            continue
        rx, ry = rc(src)
        lx, ly = lc(tgt)
        is_par = nmap.get(src, {}).get("is_parallel", False)
        for dy in (-10, 0, 10) if is_par else (0,):
            p.append(
                f'<path class="edge" d="{_bezier(rx, ry + dy, lx, ly + dy)}" '
                f'marker-end="url({"#arr-triple" if is_par else "#arr"})" data-edge="1" '
                f'data-src="{src}" data-src-anchor="r" data-src-dy="{dy}" '
                f'data-tgt="{tgt}" data-tgt-anchor="l" data-tgt-dy="{dy}"/>'
            )
        if labelled:
            dy = 16 if is_par else 6
            p.append(_edge_label((rx + lx) / 2, (ry + ly) / 2 - dy, act, src, tgt, "r", "l", f'data-label-dy="{dy}" '))

    for n in graph["nodes"]:
        nid = n["id"]
        x, y = xy(nid)
        st = _disp(nid, state, done)
        s = state.get(nid, {})
        meta = n.get("metadata")
        meta_attr = ""
        if meta:
            meta_json = _html.escape(_json.dumps(meta, indent=2), quote=True)
            meta_attr = f' data-metadata="{meta_json}"'
        p.append(
            f'<g class="node {st}" data-node-id="{nid}" data-x="{x}" data-y="{y}"{meta_attr} '
            f'transform="translate({x},{y})">'
        )
        full = n["label"]
        title = f"{full} — {st}" + (f": {s['error']}" if s.get("error") else "")
        p.append(f"<title>{_html.escape(title)}</title>")
        depth = 2 * _SO if n["is_batch"] else 0
        p.append(f'<rect class="n-shadow" x="{_SH}" y="{_SH}" width="{_NW + depth}" height="{_NH + depth}"/>')
        if n["is_batch"]:
            for o in (2 * _SO, _SO):
                p.append(f'<rect class="n-stack" x="{o}" y="{o}" width="{_NW}" height="{_NH}"/>')
        p.append(f'<rect class="n-card" x="0" y="0" width="{_NW}" height="{_NH}"/>')
        p.append(f'<rect class="n-bar" x="0" y="0" width="4" height="{_NH}"/>')

        label = full if len(full) <= _LABEL_CHARS else full[: _LABEL_CHARS - 1] + "…"
        sub_parts = [st]
        if items := _items_summary(s.get("items", [])):
            sub_parts.append(items)
        if elapsed := _elapsed(s):
            sub_parts.append(elapsed)
        p.append(f'<text class="n-kind" x="14" y="18">{_html.escape(_kind(n))}</text>')
        p.append(f'<text class="n-label" x="14" y="36">{_html.escape(label)}</text>')
        p.append(f'<text class="n-sub" x="14" y="53">{_html.escape(" · ".join(sub_parts))}</text>')
        p.append("</g>")
    p.append("</g>")
    p.append("</svg>")
    return "\n".join(p)


_LOG_LEVELS = {"debug": "debug", "info": "info", "warning": "warning", "error": "error",
               "critical": "error", "exception": "error"}  # fmt: skip


def _render_logs(logs):
    if not logs:
        return '<div class="log-empty">No log entries yet.</div>'
    lines = []
    for entry in logs:
        level = _LOG_LEVELS.get(entry["level"], "info")
        ts = time.strftime("%H:%M:%S", time.localtime(entry["ts"]))
        node = _html.escape(entry["node"] or "")
        msg = _html.escape(entry["message"])
        node_html = f'<span class="log-node">[{node}]</span>' if node else ""
        lines.append(
            f'<div class="log-entry {level}"><span class="log-ts">{ts}</span>'
            f'<span class="log-lvl">{_html.escape(entry["level"])}</span>'
            f'{node_html}<span class="log-msg">{msg}</span></div>'
        )
    return "".join(lines)


def _render_stats(counts, total):
    cells = []
    for st in ("success", "running", "failed", "pending", "skipped"):
        if not counts[st] and not (st == "pending" and not total):
            continue
        cells.append(f'<div class="stat {st}"><div class="eyebrow">{st}</div><b>{counts[st]}</b></div>')
    bar = "".join(
        f'<i class="{st}" style="width:{100 * counts[st] / total:.2f}%"></i>'
        for st in ("success", "failed", "running", "skipped")
        if total and counts[st]
    )
    stats = f'<div class="stats" title="{total} nodes">{"".join(cells)}</div>'
    return f'<div>{stats}<div class="scalebar">{bar}</div></div>' if bar else stats


def _render_html(graph, state, refresh_interval, done, logs=None, title="Flow run"):
    logs = logs or []
    pos, back = _layout(graph)
    svg = _render_svg(graph, state, done, pos, back)

    counts = dict.fromkeys(_STATUSES, 0)
    for n in graph["nodes"]:
        counts[_disp(n["id"], state, done)] += 1

    meta_refresh = "" if done else f'<meta http-equiv="refresh" content="{refresh_interval}">'
    run_state = (
        '<span class="done">complete</span>'
        if done
        else f'<span class="live">live · refresh {refresh_interval}s</span>'
    )
    updated = time.strftime("%Y-%m-%d %H:%M:%S")
    n_errors = sum(1 for e in logs if _LOG_LEVELS.get(e["level"]) == "error")

    rows = []
    for n in graph["nodes"]:
        nid = n["id"]
        s: dict = state.get(nid, {})
        st = _disp(nid, state, done)
        err = s.get("error") or ""
        items: list[dict] = s.get("items", [])

        item_html = ""
        if items:
            item_html = (
                '<div class="irow">'
                + "".join(
                    f'<span class="ib {i["status"]}" title="{_html.escape(i.get("error") or i["status"])}">'
                    f"#{i['idx']}</span>"
                    for i in items
                )
                + "</div>"
            )
        err_html = f'<div class="err">{_html.escape(err[:300])}</div>' if err else ""
        rows.append(
            f'<tr class="{st}">'
            f'<td class="name">{_html.escape(n["label"])}<span class="kind">{_kind(n)}</span></td>'
            f'<td><span class="chip {st}">{st}</span></td>'
            f'<td class="num">{_elapsed(s) or "–"}</td>'
            f'<td class="num">{_items_summary(items) or "–"}</td>'
            f"<td>{item_html}{err_html}</td>"
            "</tr>"
        )
    legend = "".join(f'<span><i class="swatch {st}"></i>{st}</span>' for st in _STATUSES)
    log_html = _render_logs(logs)
    esc_title = _html.escape(title)
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{esc_title}</title>\n"
        f"{meta_refresh}\n"
        f"{_FONTS}"
        f"<style>\n{_CSS}\n</style>\n"
        "</head>\n<body>\n"
        '<div class="page">\n'
        '<header class="top"><div>'
        '<div class="eyebrow">Flow run</div>'
        f"<h1>{esc_title}</h1>"
        f'<div class="meta">{run_state}<span>{len(graph["nodes"])} nodes</span>'
        f"<span>{len(graph['edges'])} edges</span><span>updated {updated}</span></div>"
        "</div>"
        '<div class="topright">'
        '<button id="theme" type="button" class="theme-toggle" aria-pressed="false">◐ light</button>'
        f"{_render_stats(counts, len(graph['nodes']))}"
        "</div></header>\n"
        '<section class="panel"><div class="head"><h2>Graph</h2>'
        f'<div class="legend">{legend}</div></div>'
        '<div class="body"><div class="graph-box">'
        '<div class="zoom-bar">'
        '<button class="zoom-btn" type="button" onclick="window._flowViz&&window._flowViz.zoomIn()" '
        'title="Zoom in">+</button>'
        '<button class="zoom-btn" type="button" onclick="window._flowViz&&window._flowViz.zoomOut()" '
        'title="Zoom out">−</button>'
        '<button class="zoom-btn" type="button" onclick="window._flowViz&&window._flowViz.reset()" '
        'title="Reset view and node positions">⊞</button>'
        f"</div>{svg}</div>"
        '<div class="muted" style="font-size:0.74rem">drag to pan · scroll to zoom · drag a node to move it'
        " · hover a node for its metadata</div></div></section>\n"
        '<nav class="tabs" role="tablist">'
        '<button role="tab" type="button" data-tab="status" aria-selected="true">Status</button>'
        f'<button role="tab" type="button" data-tab="logs" aria-selected="false">Logs ({len(logs)}'
        f"{f' · {n_errors} error' + ('s' if n_errors > 1 else '') if n_errors else ''})</button>"
        "</nav>\n"
        '<section id="tab-status" class="panel tab-panel"><div class="head"><h2>Node status</h2>'
        '<span class="muted">One row per node, in graph order</span></div>'
        '<div class="body"><div class="scroll"><table class="status">'
        "<thead><tr><th>Node</th><th>Status</th><th>Elapsed</th><th>Items</th><th>Details</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div></div></section>\n"
        '<section id="tab-logs" class="panel tab-panel" hidden><div class="head"><h2>Logs</h2>'
        '<span class="muted">shared["log"].info(...) from any node</span></div>'
        f'<div class="body"><div class="log-box">{log_html}</div></div></section>\n'
        f"<footer>thearc.flow · {esc_title} · {updated}</footer>\n"
        "</div>\n"
        f"<script>{_JS}</script>\n"
        "</body>\n</html>"
    )


# flow logger


class FlowLogger:
    def __init__(self, tracker):
        self._tracker = tracker

    def _emit(self, level, message, node=None):
        tracker = self._tracker
        tracker._logs.append(
            {
                "ts": time.time(),
                "level": level,
                "message": message,
                "node": node or tracker._current_node_label,
            }
        )

    def debug(self, message, node=None):
        self._emit("debug", message, node)

    def info(self, message, node=None):
        self._emit("info", message, node)

    def warning(self, message, node=None):
        self._emit("warning", message, node)

    def error(self, message, node=None):
        self._emit("error", message, node)

    def critical(self, message, node=None):
        self._emit("critical", message, node)

    def exception(self, message, node=None):
        self._emit("exception", message, node)


# flow tracker
class FlowTracker:
    def __init__(self, flow: Flow, output="flow_status.html", refresh_interval=2, title=None):
        self._flow = flow
        self._title = title or type(flow).__name__
        self._output = output
        self._refresh_interval = refresh_interval
        self._flow_done = False
        self._graph = _extract_graph(flow.start_node)
        self._logs = []
        self._current_node_label = None
        self.logger = FlowLogger(self)
        self._state = {
            n["id"]: {
                "label": n["label"],
                "status": "pending",
                "start_ts": None,
                "end_ts": None,
                "error": None,
                "items": [],
            }
            for n in self._graph["nodes"]
        }
        for n in self._graph["nodes"]:
            self._instrument(n["id"], n["is_batch"], n["is_async"])
        self._write_html()

    def _set_status(self, node_id, status, error=None):
        s = self._state.get(node_id)
        if s is None:
            return
        s["status"] = status
        s["error"] = error
        if status == "failed":
            self._logs.append({"ts": time.time(), "level": "error", "message": error or "failed", "node": s["label"]})
        if status == "running":
            s["start_ts"] = time.time()
        elif status in ("success", "failed", "skipped"):
            s["end_ts"] = time.time()

    def _set_item(self, node_id, idx, status, error=None):
        s = self._state.get(node_id)
        if s is None:
            return
        items = s["items"]
        for item in items:
            if item["idx"] == idx:
                item["status"] = status
                item["error"] = error
                return
        items.append({"idx": idx, "status": status, "error": error})

    def _write_html(self):
        done = self._flow_done or all(s["status"] in ("success", "failed") for s in self._state.values())
        html = _render_html(self._graph, self._state, self._refresh_interval, done, self._logs, self._title)
        with open(self._output, "w", encoding="utf-8") as f:
            f.write(html)

    def _instrument(self, node_id, is_batch, is_async):
        node = self._find_node(node_id)
        if node is None:
            return

        tracker = self
        original_cls = node.__class__

        if is_async:

            class TrackerNode(original_cls):
                async def _run_async(self, shared):
                    tracker._set_status(node_id, "running")
                    tracker._current_node_label = tracker._state[node_id]["label"]
                    tracker._write_html()
                    try:
                        r = await super()._run_async(shared)
                        tracker._set_status(node_id, "success")
                        tracker._write_html()
                        return r
                    except Exception as e:
                        tracker._set_status(node_id, "failed", str(e))
                        tracker._write_html()
                        raise
        elif is_batch:

            class TrackerNode(original_cls):
                def _exec(self, items):
                    items = list(items or [])
                    self._tracked_item_index = 0
                    tracker._state[node_id]["items"] = [
                        {"idx": idx, "status": "pending", "error": None} for idx in range(len(items))
                    ]
                    tracker._write_html()
                    return super()._exec(items)

                def _exec_item(self, item):
                    idx = self._tracked_item_index
                    self._tracked_item_index += 1
                    tracker._set_item(node_id, idx, "running")
                    tracker._write_html()
                    try:
                        result = super()._exec_item(item)
                        tracker._set_item(node_id, idx, "success")
                        return result
                    except Exception as exc:
                        tracker._set_item(node_id, idx, "failed", str(exc))
                        raise
                    finally:
                        tracker._write_html()

                def _run(self, shared):
                    tracker._set_status(node_id, "running")
                    tracker._current_node_label = tracker._state[node_id]["label"]
                    tracker._write_html()
                    try:
                        r = super()._run(shared)
                        tracker._set_status(node_id, "success")
                        tracker._write_html()
                        return r
                    except Exception as e:
                        tracker._set_status(node_id, "failed", str(e))
                        tracker._write_html()
                        raise
        else:

            class TrackerNode(original_cls):
                def _run(self, shared):
                    tracker._set_status(node_id, "running")
                    tracker._current_node_label = tracker._state[node_id]["label"]
                    tracker._write_html()
                    try:
                        r = super()._run(shared)
                        tracker._set_status(node_id, "success")
                        tracker._write_html()
                        return r
                    except Exception as e:
                        tracker._set_status(node_id, "failed", str(e))
                        tracker._write_html()
                        raise

        TrackerNode.__name__ = f"TrackerNode_{original_cls.__name__}"
        TrackerNode.__qualname__ = TrackerNode.__name__
        node.__class__ = TrackerNode

    def _find_node(self, node_id):
        found = [None]
        visited = set()

        def walk(node):
            nid = _node_id(node)
            if nid == node_id:
                found[0] = node
                return
            if nid in visited:
                return
            visited.add(nid)
            if isinstance(node, Flow) and node.start_node:
                walk(node.start_node)
            for nxt in node.successors.values():
                walk(nxt)

        if self._flow.start_node:
            walk(self._flow.start_node)
        return found[0]

    def run(self, shared):
        if isinstance(shared, dict):
            shared.setdefault("log", self.logger)
        try:
            return self._flow.run(shared)
        finally:
            self._flow_done = True
            self._write_html()

    async def run_async(self, shared):
        if not hasattr(self._flow, "run_async"):
            raise RuntimeError("Flow does not support run_async; use an AsyncFlow.")
        if isinstance(shared, dict):
            shared.setdefault("log", self.logger)
        try:
            return await self._flow.run_async(shared)
        finally:
            self._flow_done = True
            self._write_html()
