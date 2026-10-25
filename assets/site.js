(function () {
  "use strict";

  var root = document.documentElement;
  var body = document.body;
  var tabs = Array.prototype.slice.call(
    document.querySelectorAll('[role="tab"][data-lang]')
  );
  var panels = Array.prototype.slice.call(
    document.querySelectorAll('[role="tabpanel"][data-panel-lang]')
  );
  var storageKey = "x-ai-news-lang";

  function readStoredLanguage() {
    try {
      var value = window.localStorage.getItem(storageKey);
      return value === "zh" || value === "en" ? value : null;
    } catch (error) {
      return null;
    }
  }

  function storeLanguage(language) {
    try {
      window.localStorage.setItem(storageKey, language);
    } catch (error) {
      /* Storage can be blocked; reading must still work. */
    }
  }

  function updateTitle(language) {
    var title = language === "en" ? body.getAttribute("data-title-en") : body.getAttribute("data-title-zh");
    if (title) {
      document.title = title;
    }
  }

  function activate(language, options) {
    var settings = options || {};
    var matchedTab = null;

    tabs.forEach(function (tab) {
      var selected = tab.getAttribute("data-lang") === language;
      tab.setAttribute("aria-selected", selected ? "true" : "false");
      tab.setAttribute("tabindex", selected ? "0" : "-1");
      tab.classList.toggle("is-active", selected);
      if (selected) {
        matchedTab = tab;
      }
    });

    panels.forEach(function (panel) {
      var selected = panel.getAttribute("data-panel-lang") === language;
      panel.hidden = !selected;
      panel.classList.toggle("is-active", selected);
    });

    root.setAttribute("lang", language === "en" ? "en" : "zh-Hans");
    root.setAttribute("data-active-lang", language);
    updateTitle(language);

    if (settings.persist) {
      storeLanguage(language);
    }
    if (settings.focus && matchedTab) {
      matchedTab.focus();
    }
  }

  function moveFocus(event, targetIndex) {
    if (!tabs.length) {
      return;
    }
    var bounded = (targetIndex + tabs.length) % tabs.length;
    var next = tabs[bounded];
    activate(next.getAttribute("data-lang"), { focus: true, persist: true });
    event.preventDefault();
  }

  function onTabKeydown(event) {
    var currentIndex = tabs.indexOf(event.currentTarget);
    if (currentIndex < 0) {
      return;
    }
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      moveFocus(event, currentIndex + 1);
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      moveFocus(event, currentIndex - 1);
    } else if (event.key === "Home") {
      moveFocus(event, 0);
    } else if (event.key === "End") {
      moveFocus(event, tabs.length - 1);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      activate(tabs[currentIndex].getAttribute("data-lang"), {
        focus: true,
        persist: true,
      });
    }
  }

  if (tabs.length && panels.length) {
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        activate(tab.getAttribute("data-lang"), { persist: true });
      });
      tab.addEventListener("keydown", onTabKeydown);
    });

    try {
      var initial = readStoredLanguage() || "zh";
      root.classList.add("js");
      activate(initial, { persist: false });
    } catch (error) {
      /* Keep both pre-rendered language panels readable. */
      root.classList.remove("js");
      panels.forEach(function (panel) {
        panel.hidden = false;
        panel.classList.remove("is-active");
      });
    }
  }

  var note = document.getElementById("stale-note");
  if (note) {
    var generatedAt = body.getAttribute("data-generated-at");
    var staleHours = parseFloat(body.getAttribute("data-stale-after-hours") || "2");
    var zhSpan = note.querySelector("[data-stale-text-zh]");
    var enSpan = note.querySelector("[data-stale-text-en]");
    var futureToleranceMs = 5 * 60 * 1000;

    var checkStale = function () {
      var generatedTime = Date.parse(generatedAt || "");
      if (isNaN(generatedTime) || !isFinite(staleHours) || staleHours <= 0) {
        return;
      }

      var deltaMs = Date.now() - generatedTime;
      var zhText = "";
      var enText = "";

      if (deltaMs < -futureToleranceMs) {
        zhText = "数据时间异常（快照时间晚于当前时间）";
        enText = "Data timestamp anomaly (snapshot time is in the future)";
      } else if (deltaMs > staleHours * 60 * 60 * 1000) {
        zhText = "数据可能已过期（超过 " + staleHours + " 小时未更新）";
        enText = "Data may be stale (not updated for over " + staleHours + " hours)";
      }

      if (zhSpan) {
        zhSpan.textContent = zhText;
      }
      if (enSpan) {
        enSpan.textContent = enText;
      }
      note.hidden = !(zhText || enText);
    };

    checkStale();
    window.setInterval(checkStale, 60000);
  }
})();
