/* Otari Switch site: small, dependency-free helpers. */
(function () {
  "use strict";

  var root = document.documentElement;
  root.classList.add("js");
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function ready(fn) {
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }

  ready(function () {
    initToast();
    initCopy();
    initTabs();
    initMenu();
    initShots();
    initVideo();
    initReveal();
  });

  /* Toast ------------------------------------------------------------- */
  var toastEl, toastTimer;
  function initToast() { toastEl = document.querySelector(".toast"); }
  function say(msg) {
    if (!toastEl) return;
    toastEl.textContent = msg;
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove("show"); }, 1800);
  }

  /* Copy buttons ------------------------------------------------------ */
  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text).then(function () { return true; }, function () { return legacyCopy(text); });
    }
    return Promise.resolve(legacyCopy(text));
  }
  function legacyCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.className = "copy-scratch";
    ta.style.position = "fixed";
    ta.style.top = "0";
    ta.style.left = "0";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    ta.remove();
    return ok;
  }
  function initCopy() {
    document.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-copy]");
      if (!btn) return;
      var wrap = btn.closest("[data-copy-wrap]");
      if (!wrap) return;
      // data-copy-text holds an exact one-line command when the display is split over lines.
      var text = wrap.getAttribute("data-copy-text");
      if (text === null) {
        var src = wrap.querySelector("[data-copy-src]");
        if (!src) return;
        text = src.textContent;
      }
      copyText(text).then(function (ok) {
        var label = btn.querySelector(".copy-label");
        btn.classList.toggle("copied", ok);
        if (label) label.textContent = ok ? "Copied" : "Copy";
        say(ok ? "Copied to clipboard" : "Could not copy. Select the text instead.");
        setTimeout(function () {
          btn.classList.remove("copied");
          if (label) label.textContent = "Copy";
        }, 1800);
      });
    });
  }

  /* Tabs (pill segmented control) ------------------------------------ */
  function initTabs() {
    document.querySelectorAll("[data-tabs]").forEach(function (box) {
      var tabs = Array.prototype.slice.call(box.querySelectorAll('[role="tab"]'));
      function select(tab, focus) {
        tabs.forEach(function (t) {
          var on = t === tab;
          t.setAttribute("aria-selected", on ? "true" : "false");
          t.tabIndex = on ? 0 : -1;
          var pane = document.getElementById(t.getAttribute("aria-controls"));
          if (pane) pane.hidden = !on;
        });
        if (focus) tab.focus();
      }
      tabs.forEach(function (t, i) {
        t.addEventListener("click", function () { select(t, false); });
        t.addEventListener("keydown", function (e) {
          var j = null;
          if (e.key === "ArrowRight") j = (i + 1) % tabs.length;
          else if (e.key === "ArrowLeft") j = (i - 1 + tabs.length) % tabs.length;
          else if (e.key === "Home") j = 0;
          else if (e.key === "End") j = tabs.length - 1;
          if (j !== null) { e.preventDefault(); select(tabs[j], true); }
        });
      });
    });
  }

  /* Mobile menu ------------------------------------------------------- */
  function initMenu() {
    var nav = document.querySelector(".nav");
    var btn = document.querySelector(".menu-btn");
    if (!nav || !btn) return;
    function set(open) {
      if (open) nav.setAttribute("data-open", "");
      else nav.removeAttribute("data-open");
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      btn.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    }
    btn.addEventListener("click", function () { set(!nav.hasAttribute("data-open")); });
    nav.addEventListener("click", function (e) { if (e.target.closest(".nav-links a")) set(false); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && nav.hasAttribute("data-open")) { set(false); btn.focus(); }
    });
    document.addEventListener("click", function (e) {
      if (nav.hasAttribute("data-open") && !nav.contains(e.target)) set(false);
    });
  }

  /* Screenshots: show a frame only when its image really exists -------
     No placeholders: a figure whose image is missing is removed, and a
     group with no figures left is hidden (with its nav link). */
  function initShots() {
    var shots = Array.prototype.slice.call(document.querySelectorAll("[data-shot]"));
    if (!shots.length) return;
    var pending = shots.length;
    var canProbe = location.protocol === "http:" || location.protocol === "https:";

    function settle() {
      pending -= 1;
      if (pending === 0) tidyGroups();
    }
    function markOk(fig) {
      fig.classList.add("is-ok");
      var group = fig.closest("[data-shot-group]");
      while (group) {
        group.classList.add("has-shots");
        group = group.parentElement && group.parentElement.closest("[data-shot-group]");
      }
    }
    function drop(fig) {
      if (fig.isConnected) fig.remove();
      tidyGroups();
    }
    function watchImage(fig, img) {
      img.addEventListener("error", function () { drop(fig); }, { once: true });
    }
    function viaEvents(fig, img) {
      img.loading = "eager";
      if (img.complete && img.naturalWidth > 0) { markOk(fig); settle(); return; }
      if (img.complete && img.currentSrc) { drop(fig); settle(); return; }
      img.addEventListener("load", function () { markOk(fig); settle(); }, { once: true });
      img.addEventListener("error", function () { drop(fig); settle(); }, { once: true });
    }

    shots.forEach(function (fig) {
      var img = fig.querySelector("img");
      if (!img) { drop(fig); settle(); return; }
      if (!canProbe || !window.fetch) { viaEvents(fig, img); return; }
      // A HEAD request is cheap and lets lazy images stay lazy.
      fetch(img.getAttribute("src"), { method: "HEAD", cache: "no-cache" }).then(function (res) {
        var type = res.headers.get("content-type") || "";
        if (res.ok && type.indexOf("image/") === 0) {
          markOk(fig);
          watchImage(fig, img);
        } else {
          drop(fig);
        }
        settle();
      }, function () {
        viaEvents(fig, img);
      });
    });
  }
  function tidyGroups() {
    document.querySelectorAll("[data-shot-group]").forEach(function (g) {
      var any = g.querySelector("[data-shot]");
      if (!any) {
        g.classList.remove("has-shots");
        g.hidden = true;
        var key = g.getAttribute("data-nav-key");
        if (key) {
          document.querySelectorAll('[data-nav-for="' + key + '"]').forEach(function (a) {
            var li = a.closest("li");
            (li || a).remove();
          });
        }
      }
    });
  }

  /* Demo video -------------------------------------------------------- */
  function initVideo() {
    var frame = document.querySelector("[data-video-frame]");
    if (!frame) return;
    var video = frame.querySelector("video");
    var chapters = Array.prototype.slice.call(document.querySelectorAll(".chapter"));
    if (!video) return;

    function fail() {
      if (frame.classList.contains("no-video")) return;
      frame.classList.add("no-video");
      video.removeAttribute("controls");
      video.setAttribute("aria-hidden", "true");
      video.tabIndex = -1;
      chapters.forEach(function (c) { c.disabled = true; });
    }
    if (video.error || video.networkState === 3 /* NETWORK_NO_SOURCE */) fail();
    video.addEventListener("error", fail);

    chapters.forEach(function (c) {
      c.addEventListener("click", function () {
        var t = parseFloat(c.getAttribute("data-t")) || 0;
        try { video.currentTime = t; } catch (e) { /* not seekable yet */ }
        var p = video.play();
        if (p && p.catch) p.catch(function () {});
      });
    });
    video.addEventListener("timeupdate", function () {
      var current = null;
      chapters.forEach(function (c) {
        if (video.currentTime >= (parseFloat(c.getAttribute("data-t")) || 0)) current = c;
      });
      chapters.forEach(function (c) {
        var on = c === current;
        c.classList.toggle("is-active", on);
        if (on) c.setAttribute("aria-current", "true");
        else c.removeAttribute("aria-current");
      });
    });
  }

  /* Gentle reveal on scroll (skipped with reduced motion) ------------- */
  function initReveal() {
    if (reduceMotion || !("IntersectionObserver" in window)) return;
    var sel = ".sec-head, .steps > li, .v-card, .term, .gallery > .shot, .phones-panel, .why-panel, .otari-panel, .use-panel, .section .dock, .faq-list > details";
    var els = Array.prototype.slice.call(document.querySelectorAll(sel));
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) {
          en.target.classList.add("in");
          io.unobserve(en.target);
        }
      });
    }, { rootMargin: "0px 0px -6% 0px", threshold: 0.06 });
    els.forEach(function (el) {
      var r = el.getBoundingClientRect();
      if (r.top < window.innerHeight && r.bottom > 0) return; // already on screen: no flash
      var sibs = el.parentElement ? Array.prototype.indexOf.call(el.parentElement.children, el) : 0;
      el.style.transitionDelay = Math.min(sibs % 4, 3) * 70 + "ms";
      el.classList.add("reveal");
      io.observe(el);
    });
  }
})();
