(function () {
  "use strict";
  var csrf = document.querySelector('meta[name="csrf-token"]').content;
  var toastEl = document.getElementById("toast");

  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.classList.add("show");
    clearTimeout(toast.t);
    toast.t = setTimeout(function () { toastEl.classList.remove("show"); }, 2500);
  }

  function post(url, data) {
    return fetch(url, {
      method: "POST",
      headers: { "X-CSRFToken": csrf, "X-Requested-With": "XMLHttpRequest" },
      body: data || new FormData(),
      credentials: "same-origin"
    }).then(function (r) { return r.json().then(function (j) { j.status = r.status; return j; }); });
  }

  /* ---------- live search / filter ---------- */
  var form = document.getElementById("filters");
  var grid = document.getElementById("note-grid");

  function load(url, pushUrl) {
    grid.classList.add("loading");
    fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" }, credentials: "same-origin" })
      .then(function (r) { return r.text(); })
      .then(function (html) {
        grid.innerHTML = html;
        grid.classList.remove("loading");
        if (pushUrl) history.replaceState(null, "", pushUrl);
      })
      .catch(function () { grid.classList.remove("loading"); toast("Could not load notes. Check your connection."); });
  }

  function currentParams() {
    var p = new URLSearchParams(new FormData(form));
    Array.from(p.keys()).forEach(function (k) { if (!p.get(k)) p.delete(k); });
    return p.toString();
  }

  if (form && grid) {
    var timer;
    var refresh = function () {
      var qs = currentParams();
      load("?" + qs + (qs ? "&" : "") + "partial=1", "?" + qs);
    };
    form.addEventListener("input", function (e) {
      clearTimeout(timer);
      timer = setTimeout(refresh, e.target.tagName === "SELECT" ? 0 : 250);
    });
    form.addEventListener("submit", function (e) { e.preventDefault(); refresh(); });
    document.getElementById("clear-filters").addEventListener("click", function (e) {
      e.preventDefault(); form.reset();
      Array.from(form.elements).forEach(function (el) { if (el.name && el.name !== "sort") el.value = ""; });
      refresh();
    });
  }

  document.addEventListener("click", function (e) {
    /* pagination links inside the grid */
    var pg = e.target.closest(".js-page");
    if (pg && grid) {
      e.preventDefault();
      load(pg.getAttribute("href") + "&partial=1", pg.getAttribute("href"));
      window.scrollTo({ top: 0 });
      return;
    }
    /* bookmark buttons (cards and detail page) */
    var bm = e.target.closest(".js-bookmark");
    if (bm) {
      post(bm.dataset.url).then(function (j) {
        if (j.status === 401) { window.location = "/accounts/login/?next=" + encodeURIComponent(location.pathname); return; }
        if (!j.ok) { toast(j.error || "Something went wrong."); return; }
        bm.classList.toggle("on", j.bookmarked);
        bm.setAttribute("aria-pressed", j.bookmarked);
        if (bm.hasAttribute("data-label")) bm.textContent = j.bookmarked ? "Saved" : "Save for later";
        toast(j.bookmarked ? "Saved to your list" : "Removed from your list");
      });
    }
  });

  /* ---------- AJAX star rating ---------- */
  var widget = document.getElementById("rating-widget");
  if (widget) {
    var stars = Array.from(widget.querySelectorAll(".star"));
    var paint = function (n, cls) { stars.forEach(function (s) { s.classList.toggle(cls, +s.dataset.stars <= n); }); };
    stars.forEach(function (s) {
      s.addEventListener("mouseenter", function () { paint(+s.dataset.stars, "hover"); });
      s.addEventListener("mouseleave", function () { paint(0, "hover"); });
      s.addEventListener("click", function () {
        var fd = new FormData(); fd.append("stars", s.dataset.stars);
        post(widget.dataset.url, fd).then(function (j) {
          if (!j.ok) { toast(j.error); return; }
          paint(j.stars, "on");
          document.getElementById("avg-rating").textContent = j.avg.toFixed(1);
          document.getElementById("rating-count").textContent = j.count;
          document.getElementById("rate-label").textContent = "Your rating: " + j.stars + "\u2605 (click to change)";
          toast("Rating saved");
        });
      });
    });
  }

  /* ---------- AJAX comments ---------- */
  var cform = document.getElementById("comment-form");
  if (cform) {
    cform.addEventListener("submit", function (e) {
      e.preventDefault();
      var box = cform.querySelector("textarea");
      post(cform.action, new FormData(cform)).then(function (j) {
        if (!j.ok) { toast(j.error); return; }
        var none = document.getElementById("no-comments");
        if (none) none.remove();
        document.getElementById("comment-list").insertAdjacentHTML("beforeend", j.html);
        document.getElementById("comment-count").textContent = j.count;
        box.value = "";
        toast("Comment posted");
      });
    });
  }

  /* ---------- AJAX report ---------- */
  var rform = document.getElementById("report-form");
  if (rform) {
    rform.addEventListener("submit", function (e) {
      e.preventDefault();
      var err = document.getElementById("report-error");
      err.textContent = "";
      post(rform.action, new FormData(rform)).then(function (j) {
        if (j.status === 401) { window.location = "/accounts/login/?next=" + encodeURIComponent(location.pathname); return; }
        if (!j.ok) { err.textContent = j.error || "Could not send the report."; return; }
        var modalEl = document.getElementById("reportModal");
        var inst = window.bootstrap && bootstrap.Modal.getInstance(modalEl);
        if (inst) inst.hide();
        var btn = document.getElementById("report-btn");
        if (btn) { btn.disabled = true; btn.textContent = "Reported"; btn.removeAttribute("data-bs-toggle"); }
        rform.reset();
        toast(j.message);
      });
    });
  }
})();
