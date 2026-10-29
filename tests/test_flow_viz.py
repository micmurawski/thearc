"""The flow visualizer's log tabs: one per log file, showing its tail as the page is written."""

import re
import time

from thearc.flow import Flow, Node, viz
from thearc.flow.viz import FlowTracker, log_files


class Write(Node):
    def __init__(self, path):
        super().__init__()
        self.path = path

    def post(self, shared, prep_res, exec_res):
        with open(self.path, "a") as stream:
            stream.write("INFO starting <run>\nWARNING slow disk\nERROR lost connection\nDEBUG retry 1\n")


def tabs(page):
    return re.findall(r'<button role="tab"[^>]*>([^<]*)</button>', page)


def test_each_log_file_is_a_tab_with_its_lines(tmp_path):
    log = tmp_path / "stdout.log"
    output = tmp_path / "flow.html"
    logs = {"stdout": log, "agent": tmp_path / "none.log"}
    FlowTracker(Flow(start=Write(log)), output=str(output), logs=logs).run({})
    page = output.read_text()
    assert tabs(page) == ["Status", "stdout (4 · 1 error)", "agent (0)"]
    assert "starting &lt;run&gt;" in page  # Escaped.
    levels = re.findall(r'<div class="log-line ([a-z]*)">', page)
    assert levels == ["", "warning", "error", "debug"]
    assert "Not created yet." in page and str(log) in page
    assert 'id="thearc-theme"' in page and 'shared["log"]' not in page


def test_paths_are_named_after_their_files(tmp_path):
    files = log_files(["/tmp/logs/stdout.log", "/var/app/stdout.log", "/logs/agent.log"])
    assert list(files) == ["logs/stdout.log", "app/stdout.log", "agent.log"]
    assert list(log_files("/tmp/logs/stdout.log")) == ["stdout.log"]


def test_a_long_log_shows_its_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(viz, "LOG_TAIL_LINES", 3)
    log = tmp_path / "big.log"
    log.write_text("".join(f"line {n}\n" for n in range(10)))
    output = tmp_path / "flow.html"
    FlowTracker(Flow(start=Node()), output=str(output), logs=[log]).run({})
    page = output.read_text()
    assert re.findall(r'<span class="log-msg">([^<]*)</span>', page) == ["line 7", "line 8", "line 9"]
    assert "the last 3 lines" in page


def test_the_page_shows_log_lines_while_a_node_still_runs(tmp_path):
    log, output = tmp_path / "stdout.log", tmp_path / "flow.html"
    seen = {}

    class Slow(Node):
        def exec(self, prep_res):
            log.write_text("halfway there\n")
            time.sleep(0.3)  # The tracker rewrites the page meanwhile.
            seen["page"] = output.read_text()

    FlowTracker(Flow(start=Slow()), output=str(output), refresh_interval=0.05, logs={"stdout": log}).run({})
    assert "halfway there" in seen["page"] and 'class="chip running"' in seen["page"]
