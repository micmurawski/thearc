"""The look thearc's HTML pages share (the evolution report, the flow visualizer): one stylesheet, one theme toggle.

A page puts ``THEME_CSS`` before its own rules, ``FONTS`` in its head, and ``THEME_JS`` in a ``<script>`` of its own,
next to a ``<button id="theme" class="theme-toggle">``.
"""

from importlib.resources import files

THEME_CSS = files("thearc.ui").joinpath("theme.css").read_text(encoding="utf-8")
THEME_JS = files("thearc.ui").joinpath("theme.js").read_text(encoding="utf-8")
FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap">\n'
)

__all__ = ["FONTS", "THEME_CSS", "THEME_JS"]
