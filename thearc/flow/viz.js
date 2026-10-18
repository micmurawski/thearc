(function () {
    "use strict";

    // ---- theme: same toggle and remembered choice as the report page ----
    (function () {
        var root = document.documentElement, button = document.getElementById("theme");
        if (!button) return;
        var system = function () {
            return window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
        };
        var current = function () { return root.getAttribute("data-theme") || system(); };
        var paint = function () {
            var dark = current() === "dark";
            button.textContent = dark ? "\u25d0 light" : "\u25d1 dark";
            button.setAttribute("aria-pressed", String(!dark));
            button.title = "Switch to the " + (dark ? "light" : "dark") + " theme";
        };
        try { var saved = localStorage.getItem("thearc-report-theme"); if (saved) root.setAttribute("data-theme", saved); } catch (_error) {}
        button.addEventListener("click", function () {
            var next = current() === "dark" ? "light" : "dark";
            root.setAttribute("data-theme", next);
            try { localStorage.setItem("thearc-report-theme", next); } catch (_error) {}
            paint();
        });
        paint();
    }());

    // ---- tabs ----
    var tabKey = "thearc-flow-viz-active-tab";
    function showTab(name) {
        document.querySelectorAll(".tab-panel").forEach(function (panel) {
            panel.hidden = panel.id !== "tab-" + name;
        });
        document.querySelectorAll("nav.tabs [data-tab]").forEach(function (button) {
            button.setAttribute("aria-selected", String(button.dataset.tab === name));
        });
        try { localStorage.setItem(tabKey, name); } catch (_error) {}
    }
    document.querySelectorAll("nav.tabs [data-tab]").forEach(function (button) {
        button.addEventListener("click", function () { showTab(button.dataset.tab); });
    });
    var savedTab = "status";
    try { savedTab = localStorage.getItem(tabKey) || savedTab; } catch (_error) {}
    showTab(document.getElementById("tab-" + savedTab) ? savedTab : "status");
    window._flowViz = { showTab: showTab };

    var svg = document.getElementById("flow-svg");
    var viewport = document.getElementById("vp");
    if (!svg || !viewport) return;

    var transform = { x: 0, y: 0, scale: 1 };
    var pan = null;
    var nodeDrag = null;
    var lastTouches = [];
    var nodeWidth = Number(svg.dataset.nodeW) || 176;
    var nodeHeight = Number(svg.dataset.nodeH) || 64;
    var positionsKey = "thearc-flow-viz-node-positions:" + (svg.dataset.storageKey || "default");
    // The live page reloads every few seconds; keep the pan and zoom across those reloads.
    var viewKey = "thearc-flow-viz-view:" + (svg.dataset.storageKey || "default");
    var background = document.getElementById("vp-bg");
    var nodeGroups = Array.from(svg.querySelectorAll(".node"));
    var edgeElements = Array.from(svg.querySelectorAll("[data-edge]"));
    var nodesById = {};

    nodeGroups.forEach(function (group) {
        nodesById[group.dataset.nodeId] = group;
    });

    function clamp(value, minimum, maximum) {
        return Math.min(Math.max(value, minimum), maximum);
    }

    function readJSON(key, fallback) {
        try {
            return JSON.parse(localStorage.getItem(key)) || fallback;
        } catch (_error) {
            return fallback;
        }
    }

    function applyViewport() {
        var value = "translate(" + transform.x + " " + transform.y + ") scale(" + transform.scale + ")";
        viewport.setAttribute("transform", value);
        if (background) background.setAttribute("transform", value);
        try { sessionStorage.setItem(viewKey, JSON.stringify(transform)); } catch (_error) {}
    }

    function nodePosition(group) {
        return {
            x: Number(group.dataset.x) || 0,
            y: Number(group.dataset.y) || 0
        };
    }

    function setNodePosition(group, x, y) {
        group.dataset.x = x;
        group.dataset.y = y;
        group.setAttribute("transform", "translate(" + x + " " + y + ")");
    }

    function anchorPoint(nodeId, anchor) {
        var group = nodesById[nodeId];
        if (!group) return null;
        var position = nodePosition(group);
        if (anchor === "r") return { x: position.x + nodeWidth, y: position.y + nodeHeight / 2 };
        if (anchor === "l") return { x: position.x, y: position.y + nodeHeight / 2 };
        if (anchor === "t") return { x: position.x + nodeWidth / 2, y: position.y };
        return { x: position.x + nodeWidth / 2, y: position.y + nodeHeight };
    }

    function updateEdge(element) {
        var source = anchorPoint(element.dataset.src, element.dataset.srcAnchor);
        var target = anchorPoint(element.dataset.tgt, element.dataset.tgtAnchor);
        if (!source || !target) return;

        if (element.dataset.back === "1") {
            var drop = Number(element.dataset.drop);
            if (element.tagName === "path") {
                element.setAttribute(
                    "d",
                    "M " + source.x + "," + source.y + " C " + source.x + "," + drop +
                    " " + target.x + "," + drop + " " + target.x + "," + target.y
                );
            } else {
                element.setAttribute("x", (source.x + target.x) / 2);
                // Sit on the curve's midpoint, as the server-side render does.
                element.setAttribute("y", 0.125 * (source.y + target.y) + 0.75 * drop - Number(element.dataset.labelDy || 0));
            }
            return;
        }

        var sourceY = source.y + Number(element.dataset.srcDy || 0);
        var targetY = target.y + Number(element.dataset.tgtDy || 0);
        if (element.tagName === "path") {
            var offset = Math.max(30, Math.abs(target.x - source.x) * 0.5);
            element.setAttribute(
                "d",
                "M " + source.x + "," + sourceY + " C " + (source.x + offset) + "," + sourceY +
                " " + (target.x - offset) + "," + targetY + " " + target.x + "," + targetY
            );
        } else if (element.tagName === "text") {
            element.setAttribute("x", (source.x + target.x) / 2);
            element.setAttribute("y", (sourceY + targetY) / 2 - Number(element.dataset.labelDy || 6));
        }
    }

    function updateEdgesFor(nodeId) {
        edgeElements.forEach(function (element) {
            if (element.dataset.src === nodeId || element.dataset.tgt === nodeId) updateEdge(element);
        });
    }

    var initialPositions = {};
    nodeGroups.forEach(function (group) {
        initialPositions[group.dataset.nodeId] = nodePosition(group);
    });
    var savedPositions = readJSON(positionsKey, {});
    // Size the box to the graph, and keep wide graphs readable: never start below 80% of natural size
    // (the view opens on the start of the flow; pan for the rest), and never blow small graphs up past 100%.
    var box = svg.parentElement;
    var viewBox = svg.viewBox.baseVal;
    var fit = box.clientWidth / viewBox.width;
    var natural = clamp(fit, 0.8, 1);
    box.style.height = clamp(viewBox.height * natural + 2, 220, Math.max(320, window.innerHeight * 0.7)) + "px";
    var initialScale = natural / Math.min(fit, box.clientHeight / viewBox.height);
    function initialView() {
        transform = { x: 0, y: 0, scale: 1 };
        if (initialScale > 1.01) {
            transform = { x: 0, y: viewBox.height / 2 * (1 - initialScale), scale: initialScale };
        }
    }
    initialView();
    try {
        var savedView = JSON.parse(sessionStorage.getItem(viewKey));
        if (savedView && isFinite(savedView.scale)) transform = savedView;
    } catch (_error) {}
    applyViewport();
    nodeGroups.forEach(function (group) {
        var saved = savedPositions[group.dataset.nodeId];
        if (saved) setNodePosition(group, saved.x, saved.y);
    });
    edgeElements.forEach(updateEdge);

    var tooltip = document.createElement("div");
    tooltip.className = "viz-tooltip";
    document.body.appendChild(tooltip);

    function moveTooltip(event) {
        tooltip.style.left = event.clientX + 14 + "px";
        tooltip.style.top = event.clientY + 14 + "px";
    }

    nodeGroups.forEach(function (group) {
        group.addEventListener("mouseenter", function (event) {
            if (!group.dataset.metadata) return;
            try {
                tooltip.textContent = JSON.stringify(JSON.parse(group.dataset.metadata), null, 2);
            } catch (_error) {
                tooltip.textContent = group.dataset.metadata;
            }
            tooltip.style.display = "block";
            moveTooltip(event);
        });
        group.addEventListener("mousemove", moveTooltip);
        group.addEventListener("mouseleave", function () {
            tooltip.style.display = "none";
        });
        group.addEventListener("mousedown", function (event) {
            if (event.button !== 0) return;
            event.preventDefault();
            event.stopPropagation();
            var position = nodePosition(group);
            nodeDrag = {
                group: group,
                clientX: event.clientX,
                clientY: event.clientY,
                x: position.x,
                y: position.y
            };
            group.classList.add("dragging");
        });
    });

    function clientDeltaToSvg(dx, dy) {
        var bounds = svg.getBoundingClientRect();
        var viewBox = svg.viewBox.baseVal;
        return {
            x: dx * viewBox.width / bounds.width / transform.scale,
            y: dy * viewBox.height / bounds.height / transform.scale
        };
    }

    svg.addEventListener("mousedown", function (event) {
        if (event.button !== 0 || event.target.closest(".node")) return;
        pan = { clientX: event.clientX, clientY: event.clientY, x: transform.x, y: transform.y };
        svg.classList.add("panning");
    });

    window.addEventListener("mousemove", function (event) {
        if (nodeDrag) {
            var nodeDelta = clientDeltaToSvg(event.clientX - nodeDrag.clientX, event.clientY - nodeDrag.clientY);
            setNodePosition(nodeDrag.group, nodeDrag.x + nodeDelta.x, nodeDrag.y + nodeDelta.y);
            updateEdgesFor(nodeDrag.group.dataset.nodeId);
        } else if (pan) {
            var panDelta = clientDeltaToSvg(event.clientX - pan.clientX, event.clientY - pan.clientY);
            transform.x = pan.x + panDelta.x * transform.scale;
            transform.y = pan.y + panDelta.y * transform.scale;
            applyViewport();
        }
    });

    window.addEventListener("mouseup", function () {
        if (nodeDrag) {
            nodeDrag.group.classList.remove("dragging");
            savedPositions[nodeDrag.group.dataset.nodeId] = nodePosition(nodeDrag.group);
            try { localStorage.setItem(positionsKey, JSON.stringify(savedPositions)); } catch (_error) {}
            nodeDrag = null;
        }
        pan = null;
        svg.classList.remove("panning");
    });

    function toViewBox(clientX, clientY) {
        var bounds = svg.getBoundingClientRect();
        var viewBox = svg.viewBox.baseVal;
        return {
            x: (clientX - bounds.left) * viewBox.width / bounds.width,
            y: (clientY - bounds.top) * viewBox.height / bounds.height
        };
    }

    function zoomAt(point, factor) {
        var oldScale = transform.scale;
        transform.scale = clamp(oldScale * factor, 0.35, 4);
        var ratio = transform.scale / oldScale;
        transform.x = point.x - (point.x - transform.x) * ratio;
        transform.y = point.y - (point.y - transform.y) * ratio;
        applyViewport();
    }

    svg.addEventListener("wheel", function (event) {
        event.preventDefault();
        zoomAt(toViewBox(event.clientX, event.clientY), event.deltaY < 0 ? 1.12 : 1 / 1.12);
    }, { passive: false });

    svg.addEventListener("touchstart", function (event) {
        lastTouches = Array.from(event.touches).map(function (touch) {
            return { x: touch.clientX, y: touch.clientY };
        });
    }, { passive: true });

    svg.addEventListener("touchmove", function (event) {
        if (event.touches.length !== 1 || lastTouches.length !== 1) return;
        event.preventDefault();
        var touch = event.touches[0];
        var delta = clientDeltaToSvg(touch.clientX - lastTouches[0].x, touch.clientY - lastTouches[0].y);
        transform.x += delta.x * transform.scale;
        transform.y += delta.y * transform.scale;
        applyViewport();
        lastTouches = [{ x: touch.clientX, y: touch.clientY }];
    }, { passive: false });

    window._flowViz = {
        zoomIn: function () {
            zoomAt({ x: svg.viewBox.baseVal.width / 2, y: svg.viewBox.baseVal.height / 2 }, 1.25);
        },
        zoomOut: function () {
            zoomAt({ x: svg.viewBox.baseVal.width / 2, y: svg.viewBox.baseVal.height / 2 }, 0.8);
        },
        reset: function () {
            initialView();
            applyViewport();
            nodeGroups.forEach(function (group) {
                var initial = initialPositions[group.dataset.nodeId];
                setNodePosition(group, initial.x, initial.y);
            });
            edgeElements.forEach(updateEdge);
            savedPositions = {};
            try { localStorage.removeItem(positionsKey); } catch (_error) {}
        },
        showTab: showTab
    };
}());
