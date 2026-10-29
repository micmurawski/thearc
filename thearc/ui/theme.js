// The theme toggle of thearc's pages: follows the system until the viewer picks one; the pick is remembered in this
// browser and shared by every thearc page. Needs a <button id="theme" class="theme-toggle">.
(function () {
  "use strict";
  var root = document.documentElement, button = document.getElementById("theme");
  if (!root || !button) return;
  var system = function () {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  };
  var current = function () { return root.getAttribute("data-theme") || system(); };
  var paint = function () {
    var dark = current() === "dark";
    button.textContent = dark ? "◐ light" : "◑ dark";
    button.setAttribute("aria-pressed", String(!dark));
    button.title = "Switch to the " + (dark ? "light" : "dark") + " theme";
  };
  try { var saved = localStorage.getItem("thearc-report-theme"); if (saved) root.setAttribute("data-theme", saved); } catch (_error) { /* storage may be unavailable */ }
  button.addEventListener("click", function () {
    var next = current() === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem("thearc-report-theme", next); } catch (_error) { /* storage may be unavailable */ }
    paint();
  });
  paint();
}());
