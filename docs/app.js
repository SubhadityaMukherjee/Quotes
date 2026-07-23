/* Quotes viewer — client-side pagination + instant search. No dependencies. */
(function () {
  "use strict";

  var PAGE = 100;
  var ALL = window.__QUOTES__ || [];
  var filtered = ALL;
  var page = 1;
  var query = "";

  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  };
  var fmt = function (n) { return n.toLocaleString("en-US"); };
  var clampPage = function () {
    var total = totalPages();
    if (page > total) page = total;
    if (page < 1) page = 1;
  };
  var totalPages = function () { return Math.max(1, Math.ceil(filtered.length / PAGE)); };

  function applyFilter(q) {
    query = q.trim().toLowerCase();
    filtered = query ? ALL.filter(function (t) { return t.toLowerCase().indexOf(query) !== -1; }) : ALL;
    page = 1;
    clampPage();
    render();
    syncHash();
  }

  function highlight(text) {
    if (!query) return esc(text);
    var i = text.toLowerCase().indexOf(query);
    if (i === -1) return esc(text);
    return esc(text.slice(0, i)) + "<mark>" + esc(text.slice(i, i + query.length)) + "</mark>" +
           esc(text.slice(i + query.length));
  }

  function render() {
    var statusEl = $("status");
    if (query) {
      statusEl.textContent = fmt(filtered.length) + " of " + fmt(ALL.length) + ' quotes matching "' + query + '"';
    } else {
      statusEl.textContent = fmt(ALL.length) + " quotes";
    }

    var start = (page - 1) * PAGE;
    var end = Math.min(start + PAGE, filtered.length);
    var html = "";
    if (filtered.length === 0) {
      html = '<p class="empty-state">No quotes match "' + esc(query) + '".</p>';
    } else {
      for (var i = start; i < end; i++) {
        // Index in the full collection, so it stays stable across searches.
        var globalIndex = ALL.indexOf(filtered[i]) + 1;
        html += '<article class="quote"><span class="num">' + globalIndex + '</span><p>' +
                highlight(filtered[i]) + "</p></article>";
      }
    }
    $("quotes").innerHTML = html;
    renderPager();
  }

  function renderPager() {
    var show = filtered.length > 0;
    $("pager").hidden = !show;
    $("pager-bottom").hidden = !show;
    if (!show) return;
    var total = totalPages();
    $("total-pages").textContent = total;
    $("total-pages-b").textContent = total;
    $("cur-page").textContent = page;
    $("jump").value = page;
    $("jump").max = total;

    var atFirst = page <= 1, atLast = page >= total;
    setBtn("first", atFirst); setBtn("prev", atFirst);
    setBtn("next", atLast);  setBtn("last", atLast);
    setBtn("first-b", atFirst); setBtn("prev-b", atFirst);
    setBtn("next-b", atLast);  setBtn("last-b", atLast);
  }
  function setBtn(id, disabled) { var b = $(id); if (b) b.disabled = disabled; }

  function go(p) { page = p; clampPage(); render(); syncHash(); window.scrollTo(0, 0); }

  function syncHash() {
    var parts = [];
    if (query) parts.push("q=" + encodeURIComponent(query));
    if (page > 1 || query) parts.push("p=" + page);
    location.hash = parts.length ? parts.join("&") : "";
  }
  function readHash() {
    var h = location.hash.replace(/^#/, "");
    var map = {};
    h.split("&").forEach(function (kv) { var p = kv.split("="); map[p[0]] = decodeURIComponent(p[1] || ""); });
    return map;
  }

  /* Theme ----------------------------------------------------------------- */
  function initTheme() {
    var saved = localStorage.getItem("quotes-theme");
    if (saved) document.documentElement.setAttribute("data-theme", saved);
    $("theme").addEventListener("click", function () {
      var cur = document.documentElement.getAttribute("data-theme");
      var next = cur === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("quotes-theme", next);
    });
  }

  /* Wire up --------------------------------------------------------------- */
  function init() {
    $("search").placeholder = "Search " + fmt(ALL.length) + " quotes…";
    initTheme();

    var debounce;
    $("search").addEventListener("input", function (e) {
      clearTimeout(debounce);
      var val = e.target.value;
      debounce = setTimeout(function () { applyFilter(val); }, 120);
    });

    $("prev").addEventListener("click", function () { go(page - 1); });
    $("next").addEventListener("click", function () { go(page + 1); });
    $("first").addEventListener("click", function () { go(1); });
    $("last").addEventListener("click", function () { go(totalPages()); });
    $("prev-b").addEventListener("click", function () { go(page - 1); });
    $("next-b").addEventListener("click", function () { go(page + 1); });
    $("first-b").addEventListener("click", function () { go(1); });
    $("last-b").addEventListener("click", function () { go(totalPages()); });

    $("jump").addEventListener("change", function (e) {
      var n = parseInt(e.target.value, 10);
      if (!isNaN(n)) go(n);
    });

    document.addEventListener("keydown", function (e) {
      var typing = /^(input|textarea)$/i.test(document.activeElement.tagName);
      if (e.key === "/" && !typing) { e.preventDefault(); $("search").focus(); }
      else if (e.key === "ArrowLeft" && !typing) { go(page - 1); }
      else if (e.key === "ArrowRight" && !typing) { go(page + 1); }
    });

    /* Restore state from URL hash. */
    var st = readHash();
    if (st.q) { $("search").value = st.q; applyFilter(st.q); if (st.p) { page = parseInt(st.p, 10) || 1; clampPage(); render(); } }
    else if (st.p) { page = parseInt(st.p, 10) || 1; clampPage(); render(); }
    else { render(); }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
