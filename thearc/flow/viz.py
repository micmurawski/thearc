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

_NW, _NH = 150, 64
_SO = 6
_CW, _RH = 270, 120
_PAD = 44
_COLORS = {
    "pending": ("#2d333b", "#444c56", "#adbac7"),
    "running": ("#0d2d6b", "#1f6feb", "#cae8ff"),
    "success": ("#0d3320", "#238636", "#aff5b4"),
    "failed": ("#4b1113", "#da3633", "#ffa198"),
    "skipped": ("#161b22", "#30363d", "#484f58"),
}


def _darken(h):
    h = h.lstrip("#")
    return "#" + "".join(f"{max(0, int(h[i : i + 2], 16) - 20):02x}" for i in range(0, 6, 2))


def _bezier(x1, y1, x2, y2):
    off = max(30, abs(x2 - x1) * 0.5)
    return f"M {x1},{y1} C {x1 + off},{y1} {x2 - off},{y2} {x2},{y2}"


def _disp(nid, state, done):
    st = state.get(nid, {}).get("status", "pending")
    return "skipped" if (done and st == "pending") else st


def _layout(graph):
    ids = [n["id"] for n in graph["nodes"]]
    if not ids:
        return {}, set()

    out = {}
    for e in graph["edges"]:
        out.setdefault(e["from"], []).append(e["to"])

    # dfs to classify black edges
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


def _render_svg(graph, state, done, pos, back):
    nmap = {n["id"]: n for n in graph["nodes"]}
    if not pos:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="40" ></svg>'
    mc = max(c for c, _ in pos.values())
    mr = max(r for _, r in pos.values())
    n_back = sum(1 for e in graph["edges"] if (e["from"], e["to"]) in back)
    W = _PAD + (mc + 1) * _CW + _PAD
    H = _PAD + (mr + 1) * _RH + _PAD + (n_back * 55 if n_back else 10)
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
        return _PAD + c * _CW, _PAD + r * _RH

    def rc(nid):  # right center of front card
        x, y = xy(nid)
        return x + _NW, y + _NH // 2

    def lc(nid):  # left center of back card
        x, y = xy(nid)
        return x, y + _NH // 2

    def bc(nid):  # bottom center of card
        x, y = xy(nid)
        return x + _NW // 2, y + _NH

    p = [
        (
            f'<svg id="flow-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
            f'data-storage-key="{storage_key}" '
            f'style="width:100%;height:100%;'
            f'background:#161b22;border-radius:8px;display:block;cursor:grab;">'
        ),
        "<defs>",
        # Standard arrowhead
        ('<marker id="arr" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">'),
        '<polygon points="0 0,10 3.5,0 7" fill="#8b939e"/></marker>',
        # smaller arrowhead for parrallel triple lines
        ('<marker id="arr-triple" markerWidth="8" markerHeight="6" refX="7" refY="2.5" orient="auto">'),
        '<polygon points="0 0,8 3,0 6" fill="#8b939e"/></marker>',
        # back-edge arrowhead (dimmer)
        (
            '<marker id="arr-bk" markerWidth="10" markerHeight="7" '
            'refX="9" refY="3.5" orient="auto">'
            '<polygon points="0 0,10 3.5,0 7" fill="#6e7681"/></marker>'
        ),
        "</defs>",
        '<g id="vp">',
    ]
    # edges / draw first, behind nodes
    bdrop = 0
    for e in graph["edges"]:
        src, tgt, act = e["from"], e["to"], e.get("action", "default")
        if src not in pos or tgt not in pos:
            continue
        is_back = (src, tgt) in back
        if is_back:
            bx, by = bc(src)
            tx, ty = bc(tgt)
            drop = _PAD + (mr + 1) * _RH + 22 + bdrop + 40
            d = f"M {bx},{by} C {bx},{drop} {tx},{drop} {tx},{ty}"
            p.append(
                f'<path d="{d}" fill="none" stroke="#6e7681" stroke-width="1.5" '
                f'stroke-dasharray="5,3" marker-end="url(#arr-bk)" '
                f'data-edge="1" data-back="1" data-drop="{drop}" '
                f'data-src="{src}" data-src-anchor="b" '
                f'data-tgt="{tgt}" data-tgt-anchor="b"/>'
            )
            if act not in ("default", "start"):
                p.append(
                    f'<text x="{(bx + tx) / 2:.0f}>" y="{drop + 14}" '
                    f'text-anchor="middle" fill="#6e7681" '
                    f'font-size="11" font-family="monospace" '
                    f'data-edge="1" data-back="1" data-drop="{drop}" data-label-dy="14" '
                    f'data-src="{src}" data-src-anchor="b" '
                    f'data-tgt="{tgt}" data-tgt-anchor="b" '
                    f"{_html.escape(act)}</text>"
                )
            bdrop += 1
        else:
            rx, ry = rc(src)
            lx, ly = lc(tgt)
            is_par = nmap.get(src, {}).get("is_parallel", False)

            if is_par:
                for dy in (-10, 0, 10):
                    d = _bezier(rx, ry + dy, lx, ly + dy)
                    p.append(
                        f'<path d="{d}" fill="none" '
                        f'stroke="#8b949e" stroke-width="1.2" '
                        f'marker-end="url(#arr-triple)" '
                        f'data-edge="1" data-curve="1" '
                        f'data-src="{src}" data-src-anchor="r" data-src-dy="{dy}" '
                        f'data-tgt="{tgt}" data-tgt-anchor="l" data-tgt-dy="{dy}"/>'
                    )
                if act not in ("default", "start"):
                    mx, my = (rx + lx) / 2, min(ry, ly) - 6
                    p.append(
                        f'<text x="{mx:.0f}" y="{my:.0f}" text-anchor="middle" '
                        f'fill="#8b949e" font-size="11" font-family="monospace" '
                        f'data-edge="1" data-label-dy="6" '
                        f'data-src="{src}" data-src-anchor="r" '
                        f'data-tgt="{tgt}" data-tgt-anchor="l" '
                        f"{_html.escape(act)}</text>"
                    )
            else:
                d = _bezier(rx, ry, lx, ly)
                p.append(
                    f'<path d="{d}" fill="none" stroke="#8b949e" stroke-width="1.5" '
                    f'marker-end="url(#arr)" data-edge="1" data-curve="1" '
                    f'data-src="{src}" data-src-anchor="r" data-tgt="{tgt}" data-tgt-anchor="l"/>'
                )
                if act not in ("default", "start"):
                    mx, my = (rx + lx) / 2, min(ry, ly) - 6
                    p.append(
                        f'<text x="{mx:.0f}" y="{my:.0f}" text-anchor="middle" fill="#8b949e" '
                        f'font-size="11" font-family="monospace" data-edge="1" data-label-dy="6" '
                        f'data-src="{src}" data-src-anchor="r" data-tgt="{tgt}" data-tgt-anchor="l">'
                        f"{_html.escape(act)}</text>"
                    )
    # Nodes render after every edge so cards stay above connectors.
    for n in graph["nodes"]:
        nid = n["id"]
        x, y = xy(nid)
        st = _disp(nid, state, done)
        fill, stroke, tc = _COLORS.get(st, _COLORS["pending"])
        sw = "2.5" if st == "running" else "1.5"
        is_batch = n["is_batch"]
        meta = n.get("metadata")
        meta_attr = ""
        if meta:
            meta_json = _html.escape(_json.dumps(meta, indent=2), quote=True)
            meta_attr = f' data-metadata="{meta_json}"'
        p.append(
            f'<g class="node" data-node-id="{nid}" data-x="{x}" data-y="{y}"{meta_attr} transform="translate({x},{y})">'
        )

        if is_batch:
            for ox, oy, front in ((2 * _SO, 2 * _SO, False), (_SO, _SO, False), (0, 0, True)):
                cf = fill if front else _darken(fill)
                cs = stroke if front else _darken(stroke)
                p.append(
                    f'<rect x="{ox}" y="{oy}" width="{_NW}" height="{_NH}" rx="8" '
                    f'fill="{cf}" stroke="{cs}" stroke-width="{sw if front else "1.5"}"/>'
                )
        else:
            p.append(
                f'<rect x="0" y="0" width="{_NW}" height="{_NH}" rx="8" '
                f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'
            )

        cx, cy = _NW // 2, _NH // 2
        items = state.get(nid, {}).get("items", [])
        sub = ""
        if items:
            ok = sum(1 for item in items if item["status"] == "success")
            bad = sum(1 for item in items if item["status"] == "failed")
            running = sum(1 for item in items if item["status"] == "running")
            sub = f"{ok}/{len(items)}"
            if running:
                sub += f" \u21bb{running}"
            if bad:
                sub += f" \u2717{bad}"

        label = _html.escape(n["label"])
        label_y = cy - 9 if sub else cy
        p.append(
            f'<text x="{cx}" y="{label_y}" text-anchor="middle" dominant-baseline="middle" '
            f'font-family="-apple-system,BlinkMacSystemFont,monospace" font-size="13" '
            f'font-weight="bold" fill="{tc}">{label}</text>'
        )
        if sub:
            p.append(
                f'<text x="{cx}" y="{cy + 12}" text-anchor="middle" font-family="monospace" '
                f'font-size="10" fill="{tc}" opacity="0.8">{sub}</text>'
            )
        p.append("</g>")
    p.append("</g>")
    p.append("</svg>")
    return "\n".join(p)


_LOG_LEVELS = ("debug", "info", "warning", "error")


def _render_logs(logs):
    if not logs:
        return '<div class="log-empty">No log entries yet.</div>'
    lines = []
    for entry in logs:
        level = entry["level"] if entry["level"] in _LOG_LEVELS else "info"
        ts = time.strftime("%H:%M:%S", time.localtime(entry["ts"]))
        node = _html.escape(entry["node"] or "")
        msg = _html.escape(entry["message"])
        node_html = f'<span class="log-node">[{node}]</span>' if node else ""
        lines.append(
            f'<div class="log-entry {level}"><span class="log-ts">{ts}</span>'
            f'{node_html}<span class="log-msg">{msg}</span></div>'
        )
    return "".join(lines)


def _render_html(graph, state, refresh_interval, done, logs=None):
    logs = logs or []
    pos, back = _layout(graph)
    svg = _render_svg(graph, state, done, pos, back)

    counts: dict = {
        "pending": 0,
        "running": 0,
        "success": 0,
        "failed": 0,
        "skipped": 0,
    }

    for n in graph["nodes"]:
        counts[_disp(n["id"], state, done)] += 1

    badges = "".join(f'<span class="badge {st}">{cnt} {st}</span>' for st, cnt in counts.items() if cnt > 0)

    done_text = '<span class="done">Complete</span>' if done else ""
    meta_refresh = "" if done else f'<meta http-equiv="refresh" content="{refresh_interval}">'

    rows = []
    for n in graph["nodes"]:
        nid = n["id"]
        s: dict = state.get(nid, {})
        st = _disp(nid, state, done)
        err = s.get("error") or ""
        items: list[dict] = s.get("items", [])
        st2, et2 = s.get("start_ts"), s.get("end_ts")
        elapsed = f"{(et2 or time.time()) - st2:.1f}s" if st2 else "-"

        item_html = ""
        if items:
            item_html = (
                '<div class="irow">'
                + "".join(
                    f'<span class="ib {i["status"]}" title="{_html.escape(i.get("error") or "")}">#{i["idx"]}</span>'
                    for i in items
                )
                + "</div>"
            )
        err_html = f'<div class="err">{_html.escape(err[:120])}</div>' if err else ""
        rows.append(
            f'<tr class="{st}">'
            f"<td>{_html.escape(n['label'])}</td>"
            f'<td><span class="pill {st}">{st}</span></td>'
            f"<td>{elapsed}</td>"
            f"<td>{item_html}{err_html}</td>"
            "</tr>"
        )
    log_html = _render_logs(logs)
    return (
        "<!DOCTYPE html>\n<html>\n<head>\n"
        '<meta charset="utf-8">\n'
        "<title>AI Pipeline Viz</title>\n"
        f"{meta_refresh}\n"
        f"<style>\n{_CSS}\n</style>\n"
        "</head>\n<body>\n"
        '<header class="page-header"><div><p class="eyebrow">Orchestration</p>'
        "<h1>AI Pipeline Visualizer</h1></div>"
        f'<div class="run-summary">{badges}{done_text}</div></header>\n'
        '<div class="graph-box">'
        '<div class="zoom-bar">'
        '<button class="zoom-btn" onclick="window._flowViz&&window._flowViz.zoomIn()" title="Zoom In">+</button>'
        '<button class="zoom-btn" onclick="window._flowViz&&window._flowViz.zoomOut()" title="Zoom Out">\u2212</button>'
        '<button class="zoom-btn" onclick="window._flowViz&&window._flowViz.reset()" '
        'title="Reset zoom" style="font-size:10px">\u229e</button>'
        f"</div>{svg}</div>\n"
        '<div class="tabs">'
        '<button class="tab-btn" data-tab="status" '
        "onclick=\"window._flowViz&&window._flowViz.showTab('status')\">Status</button>"
        '<button  class="tab-btn" data-tab="logs" '
        f"onclick=\"window._flowViz&&window._flowViz.showTab('logs')\">Logs ({len(logs)})</button>"
        "</div>\n"
        '<div id="tab-status" class="tab-panel">'
        "<table>\n"
        " <thead><tr>"
        "<th>Node</th><th>Status</th><th>Elapsed</th><th>Details</th>"
        "</tr></thead>\n"
        f" <tbody>{''.join(rows)}</tbody>\n"
        "</table>\n"
        "</div>\n"
        f'<div id="tab-logs" class="tab-panel"><div class="log-box">{log_html}</div></div>\n'
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
    def __init__(self, flow: Flow, output="flow_status.html", refresh_interval=2):
        self._flow = flow
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
        html = _render_html(self._graph, self._state, self._refresh_interval, done, self._logs)
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
