/* TradingAgents-MOEX extension UI (spec §9). Loaded after app_v2.js;
   injects MOEX market/board into the analyze payload and renders the
   MOEX + T-Invest Sandbox tabs. */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  function toast(msg, type) {
    if (window.showToast) { window.showToast(msg, type || "info"); }
    else { console.log("[moex]", msg); }
  }
  function j(res) { return res.json(); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function post(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    }).then(j);
  }
  function get(url) { return fetch(url).then(j); }
  function showErr(data) {
    toast(data && data.error ? data.error : "Ошибка запроса", "error");
  }

  // --- Inject market/board into every analyze request (§4.1) ----------
  var origFetch = window.fetch;
  window.fetch = function (url, opts) {
    if (url === "/api/analyze" && opts && opts.body) {
      try {
        var body = JSON.parse(opts.body);
        var mkt = ($("market-select") || {}).value;
        if (mkt) body.market = mkt;
        if (mkt === "MOEX") {
          var board = ($("board-input") || {}).value;
          body.board = (board || "").trim().toUpperCase();
          if (body.ticker) body.ticker = String(body.ticker).trim().toUpperCase();
        }
        opts = Object.assign({}, opts, { body: JSON.stringify(body) });
      } catch (e) {}
    }
    return origFetch.call(this, url, opts);
  };

  function onMarketChange() {
    var mkt = ($("market-select") || {}).value;
    var hint = $("market-hint");
    var boardGroup = $("board-group");
    var tInput = $("ticker-input");
    if (mkt === "MOEX") {
      if (hint) hint.textContent = "МОЕХ: SECID вроде SBER, GAZP, SU26238RMFS6 (см. вкладку MOEX).";
      if (boardGroup) boardGroup.style.display = "";
      if (tInput) tInput.placeholder = "SBER  GAZP  SU26238RMFS6";
    } else {
      if (hint) hint.textContent = "Original behaviour — US/JP/CN/HK data sources.";
      if (boardGroup) boardGroup.style.display = "none";
      if (tInput) tInput.placeholder = "NVDA  AAPL  7203.T  CNC.TO  0700.HK";
    }
  }

  // ================= MOEX tab =================
  function searchInstruments() {
    var q = ($("moex-search") || {}).value || "";
    var box = $("moex-results");
    if (!box || !q.trim()) return;
    box.innerHTML = "<div class='form-hint'>Ищем…</div>";
    get("/api/moex/instruments?q=" + encodeURIComponent(q.trim())).then(function (d) {
      if (!d.ok) { box.innerHTML = "<div class='form-hint'>" + esc(d.error) + "</div>"; return; }
      if (!d.results.length) { box.innerHTML = "<div class='form-hint'>Ничего не найдено</div>"; return; }
      box.innerHTML = d.results.map(function (r) {
        var t = r.ticker || r.secid;
        var name = r.name || r.short_name || r.full_name || "";
        return "<div class='moex-res' data-ticker='" + esc(t) + "' style='cursor:pointer;padding:4px 6px;border-bottom:1px solid var(--border,#333);'>" +
          "<b>" + esc(t) + "</b> <span style='opacity:.7'>" + esc(name) + "</span>" +
          " <span style='opacity:.5;font-size:11px'>" + esc(r.board_id || r.board || "") + " " + esc(r.currency || "") + "</span></div>";
      }).join("");
      Array.prototype.forEach.call(box.querySelectorAll(".moex-res"), function (el) {
        el.onclick = function () {
          var t = el.getAttribute("data-ticker");
          if ($("ticker-input")) $("ticker-input").value = t;
          if ($("market-select")) { $("market-select").value = "MOEX"; onMarketChange(); }
          if ($("moex-quote")) renderQuote(t);
          toast("Инструмент выбран: " + t, "success");
        };
      });
    }).catch(showErr);
  }

  function renderQuote(t) {
    var box = $("moex-quote");
    if (!box) return;
    box.innerHTML = "<div class='form-hint'>Котировка…</div>";
    get("/api/moex/quote?ticker=" + encodeURIComponent(t)).then(function (d) {
      if (!d.ok) { box.innerHTML = "<div class='form-hint'>" + esc(d.error) + "</div>"; return; }
      var q = d.quote;
      var chg = "";
      if (q.last != null && q.prev_close != null && Number(q.prev_close) > 0) {
        var pct = ((Number(q.last) - Number(q.prev_close)) / Number(q.prev_close)) * 100;
        chg = " (" + (pct >= 0 ? "+" : "") + pct.toFixed(2) + "%)";
      }
      box.innerHTML = "<b>" + esc(q.secid || t) + "</b>: " + esc(q.last != null ? q.last : "—") + " " + esc(q.currency || "") + chg +
        " <span style='opacity:.6;font-size:11px'>" + esc(q.trading_status || "") + " · " + esc(q.quote_time || q.updated_at || "") + "</span>";
    }).catch(showErr);
  }

  function loadNewsSources() {
    var box = $("moex-news-sources");
    if (!box) return;
    get("/api/moex/news/registry").then(function (d) {
      if (!d.ok) return showErr(d);
      box.innerHTML = d.sources.map(function (s) {
        var why = s.available ? (s.requires_token ? "нужен TINVEST_TOKEN" : "") : (s.reason || "недоступен");
        return "<label style='display:flex;gap:8px;align-items:center;padding:2px 0;'>" +
          "<input type='checkbox' data-src='" + esc(s.id) + "'" + (s.enabled ? " checked" : "") + (s.available || s.enabled ? "" : " disabled") + ">" +
          "<span style='flex:1'>" + esc(s.name) + " <span style='opacity:.5;font-size:11px'>[" + esc(s.category) + "/" + esc(s.kind) + "]</span></span>" +
          "<span style='font-size:11px;opacity:.7'>" + esc(why) + "</span></label>";
      }).join("");
      Array.prototype.forEach.call(box.querySelectorAll("input[data-src]"), function (cb) {
        cb.onchange = function () {
          var src = {};
          src[cb.getAttribute("data-src")] = cb.checked;
          post("/api/moex/news/settings", { sources: src }).then(function (r) {
            toast(r.ok ? "Источник обновлён" : (r.error || "Ошибка"), r.ok ? "success" : "error");
          });
        };
      });
      var sync = $("moex-news-sync");
      if (sync && d.sync_seconds) sync.value = d.sync_seconds;
    }).catch(showErr);
  }

  function refreshNews() {
    var info = $("moex-news-info");
    var prev = $("moex-news-preview");
    if (info) info.textContent = "Собираем ленты…";
    post("/api/moex/news/refresh", { days: 3 }).then(function (d) {
      if (!d.ok) { if (info) info.textContent = d.error || "Ошибка"; return showErr(d); }
      if (info) info.textContent = "Обновлено: " + d.fetched_at + ", уникальных: " + d.unique_items;
      var failed = d.statuses.filter(function (s) { return s.status !== "ok" && s.status !== "disabled" && s.status !== "skipped"; });
      if (prev) {
        prev.innerHTML = d.statuses.map(function (s) {
          var mark = s.status === "ok" ? "✓" : (s.status === "disabled" || s.status === "skipped" ? "·" : "✗");
          return "<div>" + mark + " " + esc(s.name || s.source_id) + (s.status === "ok" ? " — " + (s.items || 0) + " материалов" : " — " + esc(s.detail || s.status)) + "</div>";
        }).join("");
      }
      if (failed.length) toast("Часть источников недоступна: " + failed.length, "info");
      else toast("Источники обновлены", "success");
    }).catch(showErr);
  }

  function previewNews() {
    var t = (($("moex-preview-ticker") || {}).value || ($("ticker-input") || {}).value || "").trim();
    var prev = $("moex-news-article");
    if (!t || !prev) { toast("Укажите тикер", "error"); return; }
    prev.textContent = "Загружаем…";
    get("/api/moex/news/preview?ticker=" + encodeURIComponent(t) + "&days=5").then(function (d) {
      if (!d.ok) { prev.textContent = d.error || "Ошибка"; return showErr(d); }
      prev.textContent = d.report;
    }).catch(showErr);
  }

  var RISK_FIELDS = ["max_order_amount", "max_position_pct", "max_issuer_pct",
    "max_open_orders", "max_risk_per_trade_pct", "daily_loss_limit"];
  function loadRisk() {
    if (!$("risk-form")) return;
    get("/api/moex/risk").then(function (d) {
      if (!d.ok) return showErr(d);
      RISK_FIELDS.forEach(function (f) {
        var el = $("risk-" + f.replace(/_/g, "-"));
        if (el) el.value = d.limits[f] == null ? "" : d.limits[f];
      });
      var ab = $("risk-allow-new-buys"), as = $("risk-allow-sell");
      if (ab) ab.checked = !!d.limits.allow_new_buys;
      if (as) as.checked = !!d.limits.allow_selling_positions;
      var rc = $("risk-require-confirmation");
      if (rc) rc.checked = d.limits.require_confirmation !== false;
      var ac = $("risk-sandbox-auto-confirm");
      if (ac) ac.checked = !!d.sandbox_auto_confirm;
    }).catch(showErr);
  }
  function saveRisk() {
    var body = {};
    RISK_FIELDS.forEach(function (f) {
      var el = $("risk-" + f.replace(/_/g, "-"));
      if (el) body[f] = el.value === "" ? null : Number(el.value);
    });
    var ab = $("risk-allow-new-buys"), as = $("risk-allow-sell");
    if (ab) body.allow_new_buys = ab.checked;
    if (as) body.allow_selling_positions = as.checked;
    var rc = $("risk-require-confirmation");
    if (rc) body.require_confirmation = rc.checked;
    var ac = $("risk-sandbox-auto-confirm");
    if (ac) body.sandbox_auto_confirm = ac.checked;
    post("/api/moex/risk", body).then(function (d) {
      toast(d.ok ? "Лимиты сохранены" : (d.error || "Ошибка"), d.ok ? "success" : "error");
    }).catch(showErr);
  }

  function loadLessons() {
    var box = $("moex-lessons");
    if (!box) return;
    get("/api/moex/lessons").then(function (d) {
      if (!d.ok) return showErr(d);
      if (!d.lessons.length) { box.innerHTML = "<div class='form-hint'>Пока нет выводов по закрытым сделкам (§11).</div>"; return; }
      box.innerHTML = d.lessons.map(function (l) {
        // record_lesson schema: {proposal:{ticker,side}, realized_pnl,
        // explanation, created_at} — flat keys kept as legacy fallback.
        var pr = l.proposal || {};
        var ticker = pr.ticker || l.ticker || "";
        var side = pr.side || l.side || "";
        var pnl = (l.realized_pnl != null ? l.realized_pnl : (l.result_text || l.outcome || ""));
        var when = l.created_at || l.recorded_at || "";
        var text = l.explanation || l.lesson || l.summary || "";
        return "<div style='padding:6px 0;border-bottom:1px solid var(--border,#333);'>" +
          "<label style='display:flex;gap:8px;align-items:center;'>" +
          "<input type='checkbox' class='lesson-cb' data-id='" + esc(l.id) + "'" + (l.enabled ? " checked" : "") + ">" +
          "<span style='flex:1'>" + esc(ticker) + " " + esc(side) + " → " + esc(pnl) +
          " <span style='opacity:.5;font-size:11px'>" + esc(when) + "</span></span></label>" +
          "<div style='font-size:12px;opacity:.85;padding-left:26px'>" + esc(String(text).slice(0, 300)) + "</div>" +
          "<button class='btn btn-ghost btn-sm lesson-del' data-id='" + esc(l.id) + "' style='margin-left:26px;'>Удалить</button></div>";
      }).join("");
      Array.prototype.forEach.call(box.querySelectorAll(".lesson-cb"), function (cb) {
        cb.onchange = function () {
          post("/api/moex/lessons", { id: cb.getAttribute("data-id"), enabled: cb.checked })
            .then(function (r) { toast(r.ok ? "Обновлено" : (r.error || "?"), r.ok ? "success" : "error"); });
        };
      });
      Array.prototype.forEach.call(box.querySelectorAll(".lesson-del"), function (btn) {
        btn.onclick = function () {
          if (!confirm("Удалить вывод?")) return;
          fetch("/api/moex/lessons", {
            method: "DELETE", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: btn.getAttribute("data-id") }),
          }).then(j).then(function (r) { toast(r.ok ? "Удалён" : (r.error || "?"), r.ok ? "success" : "error"); loadLessons(); });
        };
      });
    }).catch(showErr);
  }

  // ================= Sandbox tab =================
  var tiState = { account: "", orders: [] };

  function tiStatus() {
    var banner = $("ti-banner");
    get("/api/tinvest/status").then(function (d) {
      if (!banner) return;
      if (!d.configured) {
        banner.innerHTML = "<b>T-Invest: не подключён.</b> Задайте токен в переменной окружения TINVEST_TOKEN (только окружение, не в настройках).";
        banner.style.opacity = "1";
      } else {
        var modeLabel = esc(d.mode || "sandbox");
        var envLabel = esc(d.environment || "SANDBOX");
        var liveNote = d.production_trading
          ? "<b style='color:#e05353'>Режим 5 — реальные деньги.</b>"
          : (d.can_mutate === false
              ? "Только чтение: заявки и стопы запрещены."
              : "Реальная торговля: выкл.");
        banner.innerHTML = "<b>T-Invest: подключён</b> — режим <span style='color:#2ecc71'>" +
          modeLabel + "</span> · " + envLabel + ". Активный счёт: " +
          esc(d.active_account || "—") + ". " + liveNote +
          (d.auto_sync && d.auto_sync.enabled
            ? " · авто-синк: " + (d.auto_sync.last_sync
              ? esc(d.auto_sync.last_sync)
              : (d.auto_sync.last_error
                ? "<span style='color:#e74c3c'>ошибка: " + esc(d.auto_sync.last_error) + "</span>"
                : "ожидание первого тика"))
            : "");
        tiState.account = d.active_account || "";
      }
      loadAccounts();
      // Boot order: tiStatus() resolves AFTER the unconditional
      // loadBalance()/loadOrders()/loadOperations() calls, which bail out
      // while tiState.account is still "" — so refresh them here too,
      // otherwise the panel stays blank until a manual sync (live bug).
      if (tiState.account) {
        loadBalance();
        loadOrders();
        loadStops();
        loadOperations();
        loadDashboard();
      }
    }).catch(function () { if (banner) banner.textContent = "Статус T-Invest недоступен"; });
  }

  function loadAccounts() {
    var box = $("ti-accounts");
    if (!box) return;
    get("/api/tinvest/accounts").then(function (d) {
      if (!d.ok) { box.innerHTML = "<div class='form-hint'>" + esc(d.error) + "</div>"; return; }
      if (!d.accounts.length) {
        box.innerHTML = "<div class='form-hint'>Виртуальных счетов нет — создайте новый.</div>";
        return;
      }
      box.innerHTML = d.accounts.map(function (a) {
        var active = a.id === tiState.account;
        return "<div style='display:flex;gap:8px;align-items:center;padding:4px 0;'>" +
          "<span><b>" + esc(a.name || a.id) + "</b> <span style='opacity:.6;font-size:11px'>" + esc(a.id) + "</span> " +
          esc((a.status || "").toUpperCase()) + " " + esc((a.currency || "").toUpperCase()) + (active ? " ★ активный" : "") + "</span>" +
          "<button class='btn btn-ghost btn-sm ti-sel' data-id='" + esc(a.id) + "'>Выбрать</button>" +
          "<button class='btn btn-danger btn-sm ti-close' data-id='" + esc(a.id) + "'>Закрыть</button></div>";
      }).join("");
      Array.prototype.forEach.call(box.querySelectorAll(".ti-sel"), function (b) {
        b.onclick = function () {
          post("/api/tinvest/accounts", { action: "select", account_id: b.getAttribute("data-id") }).then(function (r) {
            if (r.ok) { tiState.account = r.active_account; toast("Активный счёт выбран", "success"); tiStatus(); loadBalance(); loadOrders(); }
            else showErr(r);
          });
        };
      });
      Array.prototype.forEach.call(box.querySelectorAll(".ti-close"), function (b) {
        b.onclick = function () {
          if (!confirm("Закрыть виртуальный счёт? Действие только для песочницы.")) return;
          post("/api/tinvest/accounts", { action: "close", account_id: b.getAttribute("data-id") }).then(function (r) {
            toast(r.ok ? "Виртуальный счёт закрыт" : (r.error || "Ошибка"), r.ok ? "success" : "error");
            if (r.ok) tiState.account = "";
            tiStatus();
          });
        };
      });
    }).catch(showErr);
  }

  function payIn() {
    var amount = Number(($("ti-payin-amount") || {}).value);
    var cur = ($("ti-payin-cur") || {}).value || "rub";
    if (!amount || amount <= 0) { toast("Укажите сумму", "error"); return; }
    if (!tiState.account) { toast("Выберите счёт", "error"); return; }
    if (!confirm("Пополнить ВИРТУАЛЬНЫЙ счёт " + tiState.account + " на " + amount + " " + cur.toUpperCase() + "?")) return;
    post("/api/tinvest/payin", { account_id: tiState.account, amount: amount, currency: cur, confirm: true })
      .then(function (d) {
        toast(d.ok ? "Виртуальные средства зачислены" : (d.error || "Ошибка"), d.ok ? "success" : "error");
        if (d.ok) loadBalance();
      }).catch(showErr);
  }

  function loadBalance() {
    if (!tiState.account) return;
    var box = $("ti-balance");
    if (box) box.textContent = "Загрузка…";
    Promise.all([
      get("/api/tinvest/balance?account_id=" + encodeURIComponent(tiState.account)),
      get("/api/tinvest/portfolio?account_id=" + encodeURIComponent(tiState.account)),
    ]).then(function (rs) {
      var b = rs[0], p = rs[1];
      if (b.ok && box) {
        var bal = b.balance || {};
        var cash = bal.cash_by_currency || [];
        var cashTxt = cash.length
          ? cash.map(function (c) {
              return (c.amount != null ? Number(c.amount).toLocaleString("ru-RU") : "—") +
                " " + String(c.currency || "").toUpperCase();
            }).join(" · ")
          : (bal.currencies != null ? String(bal.currencies) : "—");
        box.innerHTML = "Наличные: <b>" + esc(cashTxt) + "</b>" +
          (bal.fetched_at ? " <span style='opacity:.6;font-size:11px'>" + esc(bal.fetched_at) + "</span>" : "");
      } else if (box && !b.ok) box.textContent = b.error || "Ошибка баланса";
      if (p.ok) renderPositions(p.portfolio || {});
      else {
        var pos = $("ti-positions");
        if (pos) pos.innerHTML = "<div class='form-hint'>" + esc(p.error || "") + "</div>";
      }
    }).catch(function () { if (box) box.textContent = "Нет соединения с брокером"; });
  }

  function renderPositions(pf) {
    var box = $("ti-positions");
    if (!box) return;
    var pos = (pf.positions || []).filter(function (p) {
      return String(p.type || "").toLowerCase() !== "currency";
    });
    if (!pos.length) { box.innerHTML = "<div class='form-hint'>Позиций нет. Стоимость портфеля: " + esc(pf.total_value == null ? "—" : pf.total_value) + " " + esc(pf.currency || "") + "</div>"; return; }
    box.innerHTML = "<table class='data-table' style='width:100%'><tr><th>Тикер</th><th>Лоты</th><th>Ср. цена</th><th>Текущая</th><th>Стоимость</th><th>P&L</th></tr>" +
      pos.map(function (p) {
        return "<tr><td>" + esc(p.ticker) + "</td><td>" + esc(p.quantity_lots) + "</td><td>" + esc(p.avg_price) +
          "</td><td>" + esc(p.current_price) + "</td><td>" + esc(p.value) + "</td><td>" + esc(p.unrealized_pnl) + "</td></tr>";
      }).join("") + "</table><div class='form-hint'>Стоимость: " + esc(pf.total_value == null ? "—" : pf.total_value) + " " + esc(pf.currency || "") +
      " · " + esc(pf.fetched_at || "") + "</div>";
  }

  // ── Dashboard (spec §5 panel): balance, positions, closed deals, PnL ──
  function _num(v, digits) {
    if (v == null || v === "") return "—";
    var n = Number(v);
    if (isNaN(n)) return String(v);
    return n.toLocaleString("ru-RU", {
      minimumFractionDigits: digits || 0,
      maximumFractionDigits: digits == null ? 2 : digits,
    });
  }
  function _pnlColor(v) {
    var n = Number(v);
    if (!isFinite(n) || n === 0) return "inherit";
    return n > 0 ? "#2ecc71" : "#e74c3c";
  }
  function _kpi(label, value, sub, color) {
    return "<div style='border:1px solid var(--border,#2a2f3a);border-radius:8px;padding:10px 12px'>" +
      "<div style='font-size:11px;opacity:.65;text-transform:uppercase;letter-spacing:.04em'>" + esc(label) + "</div>" +
      "<div style='font-size:19px;font-weight:600;margin-top:4px;color:" + (color || "inherit") + "'>" + esc(String(value)) + "</div>" +
      (sub ? "<div style='font-size:11px;opacity:.6;margin-top:2px'>" + esc(String(sub)) + "</div>" : "") +
      "</div>";
  }

  function loadDashboard() {
    var kpi = $("ti-dash-kpi"), posBox = $("ti-dash-positions"),
        dealBox = $("ti-dash-deals"), foot = $("ti-dash-foot");
    if (!kpi) return;
    if (!tiState.account) {
      kpi.innerHTML = "<div class='form-hint'>Выберите виртуальный счёт, чтобы увидеть дашборд.</div>";
      if (posBox) posBox.innerHTML = ""; if (dealBox) dealBox.innerHTML = "";
      return;
    }
    kpi.innerHTML = "<div class='form-hint'>Загрузка дашборда…</div>";
    get("/api/tinvest/dashboard?account_id=" + encodeURIComponent(tiState.account))
      .then(function (d) {
        if (!d.ok) { kpi.innerHTML = "<div class='form-hint' style='color:#e74c3c'>" + esc(d.error || "Ошибка") + "</div>"; return; }
        renderDashboard(d.dashboard);
      })
      .catch(function () {
        kpi.innerHTML = "<div class='form-hint' style='color:#e74c3c'>Нет соединения с брокером</div>";
      });
  }

  function renderDashboard(db) {
    var bal = db.balance || {}, pnl = db.pnl || {}, w = pnl.windows || {};
    var cur = String(bal.currency || "RUB").toUpperCase();

    // KPI row: balance first, then PnL per window.
    var cells = [];
    cells.push(_kpi("Баланс (всего)", _num(bal.total) + " " + cur,
      "обновлено: " + (bal.synced_at || "—")));
    cells.push(_kpi("Доступно", _num(bal.available) + " " + cur,
      bal.blocked ? ("заблокировано: " + _num(bal.blocked)) : "блокировок нет"));
    ["day", "week", "month", "all"].forEach(function (k) {
      var b = pnl[k];
      if (!b) return;
      cells.push(_kpi("P&L · " + (w[k] || k), _num(b.pnl) + " " + cur,
        b.deals + " сделок", _pnlColor(b.pnl)));
    });
    cells.push(_kpi("Нереализованный P&L", _num(db.unrealized_pnl) + " " + cur,
      db.positions_count + " позиц.", _pnlColor(db.unrealized_pnl)));
    var kpi = $("ti-dash-kpi");
    if (kpi) kpi.innerHTML = cells.join("");

    // Positions.
    var posBox = $("ti-dash-positions");
    if (posBox) {
      var pos = db.positions || [];
      if (!pos.length) {
        posBox.innerHTML = "<div class='form-hint'>Открытых позиций нет.</div>";
      } else {
        posBox.innerHTML = "<table class='data-table' style='width:100%'><tr>" +
          "<th>Тикер</th><th>Лоты</th><th>Ср. цена</th><th>Текущая</th><th>Стоимость</th><th>P&L</th></tr>" +
          pos.map(function (p) {
            return "<tr><td>" + esc(p.ticker) + "</td><td>" + esc(p.quantity_lots) +
              "</td><td>" + esc(_num(p.avg_price)) + "</td><td>" + esc(_num(p.current_price)) +
              "</td><td>" + esc(_num(p.value)) + "</td>" +
              "<td style='color:" + _pnlColor(p.unrealized_pnl) + "'>" + esc(_num(p.unrealized_pnl)) + "</td></tr>";
          }).join("") + "</table>";
      }
    }

    // Closed deals.
    var dealBox = $("ti-dash-deals");
    if (dealBox) {
      var deals = db.deals || [];
      if (!deals.length) {
        dealBox.innerHTML = "<div class='form-hint'>Закрытых сделок пока нет.</div>";
      } else {
        dealBox.innerHTML = "<table class='data-table' style='width:100%'><tr>" +
          "<th>Тикер</th><th>Открыта</th><th>Закрыта</th><th>Покупка</th><th>Продажа</th><th>Комиссия</th><th>P&L</th></tr>" +
          deals.map(function (x) {
            return "<tr><td>" + esc(x.ticker) + "</td>" +
              "<td style='font-size:11px'>" + esc(String(x.opened_at || "").slice(0, 16)) + "</td>" +
              "<td style='font-size:11px'>" + esc(String(x.closed_at || "").slice(0, 16)) + "</td>" +
              "<td>" + esc(_num(x.buy_payment)) + "</td>" +
              "<td>" + esc(_num(x.sell_payment)) + "</td>" +
              "<td>" + esc(_num(x.commission, 4)) + "</td>" +
              "<td style='color:" + _pnlColor(x.pnl) + "'>" + esc(_num(x.pnl)) + "</td></tr>";
          }).join("") + "</table>";
      }
    }

    var foot = $("ti-dash-foot");
    if (foot) {
      foot.textContent = "Закрытых сделок всего: " + (db.deals_count || 0) +
        " · комиссии: " + _num(db.commissions_total) + " " + cur +
        " · источник: " + (db.fetched_at || "") +
        (db.sandbox === false ? " · ⚠ НЕ песочница" : "");
    }
  }

  function syncNow() {
    if (!tiState.account) { toast("Выберите счёт", "error"); return; }
    post("/api/tinvest/sync", { account_id: tiState.account }).then(function (d) {
      var info = $("ti-sync-info");
      if (d.ok) {
        var s = d.snapshot || {};
        if (info) info.textContent = "Синхронизировано: " + (s.synced_at || s.fetched_at || "") + (s.stale ? " (данные могут быть устаревшими)" : "");
        toast("Портфель синхронизирован", "success");
        loadBalance(); loadOrders(); loadStops(); loadOperations(); loadDashboard();
      } else {
        if (info) info.textContent = "Ошибка синхронизации: " + (d.error || "");
        showErr(d);
      }
    }).catch(showErr);
  }

  function orderPreview() {
    var ticker = (($("ord-ticker") || {}).value || "").trim().toUpperCase();
    var side = ($("ord-side") || {}).value || "buy";
    var type = ($("ord-type") || {}).value || "market";
    var lots = ($("ord-lots") || {}).value || "";
    var price = ($("ord-price") || {}).value || "";
    var box = $("ord-preview");
    if (!ticker) { toast("Укажите тикер", "error"); return; }
    if (!tiState.account) { toast("Выберите виртуальный счёт", "error"); return; }
    box.textContent = "Готовим превью…";
    post("/api/tinvest/order/preview", {
      ticker: ticker, side: side, order_type: type, lots: lots, limit_price: price,
      account_id: tiState.account,
    }).then(function (d) {
      if (!d.ok) { box.textContent = d.error || "Ошибка"; $("ord-place-btn").style.display = "none"; return; }
      renderOrderPreview(d.preview);
    }).catch(showErr);
  }

  function renderOrderPreview(pv) {
    var box = $("ord-preview");
    var pr = pv.proposal || {}, rk = pv.risk || {};
    window._lastPreview = pv;
    if (box) {
      box.innerHTML =
        "<div><b>ЗАЯВКА (SANDBOX)</b>: " + esc(pr.side) + " " + esc(pr.lots) + " лот(ов) " + esc(pr.ticker) +
        " · " + esc(pr.order_type) + (pr.limit_price ? " @ " + esc(pr.limit_price) : "") +
        (pr.stop_price ? " · стоп из отчёта @" + esc(pr.stop_price) : "") +
        " · ≈ " + esc(pr.estimated_amount != null ? pr.estimated_amount : "—") + " " + esc((pv.quote || {}).currency || "") + "</div>" +
        (pr.rationale ? "<div class='form-hint'>Основание: " + esc(String(pr.rationale).slice(0, 300)) + "</div>" : "") +
        "<div>Средства: " + esc(pv.cash != null ? pv.cash : "—") + " · Портфель: " + esc(pv.portfolio_total == null ? "—" : pv.portfolio_total) + "</div>" +
        "<div style='color:" + (rk.allowed ? "#2ecc71" : "#e74c3c") + "'>" + (rk.allowed ? "✓ риск: разрешено" : "✗ риск: нарушения") + "</div>" +
        (rk.allowed ? "" : "<ul>" + (rk.violations || []).map(function (v) { return "<li>" + esc(v) + "</li>"; }).join("") + "</ul>") +
        "<div class='form-hint'>preview_id: " + esc(pv.preview_id) + " · " + esc(pv.created_at) +
        " · требуется явное подтверждение ниже</div>";
    }
    if ($("ord-place-btn")) $("ord-place-btn").style.display = rk.allowed ? "" : "none";
    // Prefill the stop-order card from the report's stop-loss hint (§6.4):
    // direction is the OPPOSITE of the main order (a buy needs a protective
    // sell stop, a sell/short needs a buy stop). Nothing is placed — the
    // user still confirms via the stop card's own button.
    if (pr.stop_price && pr.ticker) {
      if ($("stop-ticker")) $("stop-ticker").value = pr.ticker;
      if ($("stop-direction")) $("stop-direction").value = pr.side === "buy" ? "sell" : "buy";
      if ($("stop-type")) $("stop-type").value = "stop_loss";
      if ($("stop-lots")) $("stop-lots").value = pr.lots || "";
      if ($("stop-price")) $("stop-price").value = pr.stop_price;
    }
  }

  // Report → proposal bridge (spec §6.2): parse BUY/SELL/HOLD from the
  // analyst markdown, run the standard preview pipeline, park it in the
  // sandbox order panel for explicit confirmation. Never auto-places.
  function proposeFromReport(reportObj) {
    if (!reportObj || !reportObj.markdown) {
      toast("Отчёт не загружен — откройте его в списке отчётов", "error");
      return;
    }
    var nav = document.querySelector('.nav-btn[data-tab="sandbox"]');
    if (nav) nav.click();
    var box = $("ord-preview");
    if (box) box.textContent = "Разбираем отчёт и готовим превью…";
    if ($("ord-ticker")) $("ord-ticker").value = reportObj.ticker || "";
    post("/api/tinvest/order/from_report", {
      ticker: reportObj.ticker,
      report: reportObj.markdown,
      account_id: tiState.account || undefined,
    }).then(function (d) {
      if (!d.ok) {
        if (box) box.innerHTML = "<div class='form-hint' style='color:#e74c3c'>" + esc(d.error || "Ошибка") + "</div>";
        if ($("ord-place-btn")) $("ord-place-btn").style.display = "none";
        return;
      }
      if (d.hold) {
        window._lastPreview = null;
        if ($("ord-place-btn")) $("ord-place-btn").style.display = "none";
        if (box) box.innerHTML = "<div class='form-hint'>" + esc(d.reason || "HOLD — активного действия нет") +
          ". Заявка не формируется.</div>";
        toast(d.reason || "В отчёте HOLD — заявка не нужна", "info");
        return;
      }
      renderOrderPreview(d.preview);
      toast("Превью из отчёта готово — проверьте риск и подтвердите", "success");
    }).catch(showErr);
  }
  window.taProposeOrder = proposeFromReport;

  function orderPlace() {
    var pv = window._lastPreview;
    if (!pv) { toast("Сначала превью", "error"); return; }
    var pr = pv.proposal || {};
    if (!confirm("Отправить заявку в ПЕСОЧНИЦУ?\n" + pr.side + " " + pr.lots + " " + pr.ticker +
      (pr.limit_price ? " @ " + pr.limit_price : " (рынок)"))) return;
    $("ord-place-btn").disabled = true;
    post("/api/tinvest/order", { preview: pv, confirm: true, account_id: tiState.account })
      .then(function (d) {
        $("ord-place-btn").disabled = false;
        if (d.ok && d.order && d.order.placed) {
          toast("Заявка отправлена: " + (d.order.order_id || d.order.id || "?") + " [" + d.order.state + "]", "success");
          window._lastPreview = null;
          $("ord-preview").textContent = "Заявка отправлена. Статус: " + d.order.state;
          loadOrders(); loadOperations(); loadBalance();
        } else {
          var msg = (d.order && d.order.reason) || d.error || "Не размещено";
          if (d.order && d.order.violations) msg += ": " + d.order.violations.join("; ");
          toast(msg, "error");
        }
      }).catch(function () { $("ord-place-btn").disabled = false; showErr({ error: "сеть недоступна — статус неизвестен, проверьте заявки перед повтором" }); });
  }

  function loadOrders() {
    var box = $("ti-orders");
    if (!box || !tiState.account) return;
    get("/api/tinvest/orders?account_id=" + encodeURIComponent(tiState.account)).then(function (d) {
      if (!d.ok) { box.innerHTML = "<div class='form-hint'>" + esc(d.error) + "</div>"; return; }
      if (!d.orders.length) { box.innerHTML = "<div class='form-hint'>Активных заявок нет.</div>"; return; }
      box.innerHTML = "<table class='data-table' style='width:100%'><tr><th>ID</th><th>Сторона</th><th>Лоты</th><th>Исп.</th><th>Статус</th><th></th></tr>" +
        d.orders.map(function (o) {
          return "<tr><td style='font-size:11px'>" + esc(o.order_id) + "</td><td>" + esc(o.direction || o.side) +
            "</td><td>" + esc(o.lots_requested) + "</td><td>" + esc(o.lots_executed) + "</td><td>" + esc(o.status) +
            "</td><td><button class='btn btn-ghost btn-sm ord-cancel' data-id='" + esc(o.order_id) + "'>Отменить</button></td></tr>";
        }).join("") + "</table>";
      Array.prototype.forEach.call(box.querySelectorAll(".ord-cancel"), function (b) {
        b.onclick = function () {
          if (!confirm("Отменить заявку в песочнице?")) return;
          post("/api/tinvest/order/cancel", { account_id: tiState.account, order_id: b.getAttribute("data-id") })
            .then(function (r) {
              toast(r.ok ? "Запрос на отмену отправлен" : (r.error || "Ошибка"), r.ok ? "success" : "error");
              setTimeout(loadOrders, 800);
            }).catch(showErr);
        };
      });
    }).catch(showErr);
  }

  function loadOperations() {
    var box = $("ti-ops");
    if (!box || !tiState.account) return;
    get("/api/tinvest/operations?account_id=" + encodeURIComponent(tiState.account)).then(function (d) {
      if (!d.ok) { box.innerHTML = "<div class='form-hint'>" + esc(d.error) + "</div>"; return; }
      var ops = d.operations || [];
      if (!ops.length) { box.innerHTML = "<div class='form-hint'>Операций пока нет.</div>"; return; }
      box.innerHTML = "<table class='data-table' style='width:100%'><tr><th>Дата</th><th>Тип</th><th>Инструмент</th><th>Сумма</th></tr>" +
        ops.slice(0, 50).map(function (o) {
          return "<tr><td>" + esc(o.date || o.execution_date) + "</td><td>" + esc(o.type || o.payment_type) +
            "</td><td>" + esc(o.ticker || o.figi || "") + "</td><td>" + esc(o.payment != null ? o.payment : (o.amount != null ? o.amount : "")) +
            " " + esc(o.currency || "") + "</td></tr>";
        }).join("") + "</table>";
    }).catch(showErr);
  }

  // ================= stop orders (§6.4) =================
  function stopPlace() {
    var ticker = (($("stop-ticker") || {}).value || "").trim().toUpperCase();
    var direction = ($("stop-direction") || {}).value || "sell";
    var type = ($("stop-type") || {}).value || "stop_loss";
    var lots = ($("stop-lots") || {}).value || "";
    var price = Number(($("stop-price") || {}).value || 0);
    if (!ticker || !price) { toast("Укажите тикер и стоп-цену", "error"); return; }
    if (!tiState.account) { toast("Выберите виртуальный счёт", "error"); return; }
    if (!confirm("Поставить " + type + " " + direction + " " + (lots || "?") +
      " лот(ов) " + ticker + " @ стоп " + price + "?\n" +
      "Исполнение по цене стопа НЕ гарантировано при гэпе.")) return;
    post("/api/tinvest/stoporder", {
      ticker: ticker, direction: direction, order_type: type,
      lots: lots, stop_price: price,
      account_id: tiState.account, confirm: true,
    }).then(function (d) {
      if (d.ok && d.stop_order) {
        toast("Стоп-заявка поставлена: " + d.stop_order.stop_order_id, "success");
        loadStops();
      } else {
        toast(d.error || "Не поставлена", "error");
      }
    }).catch(showErr);
  }

  function loadStops() {
    var box = $("ti-stops");
    if (!box || !tiState.account) return;
    get("/api/tinvest/stoporders?account_id=" + encodeURIComponent(tiState.account))
      .then(function (d) {
        if (!d.ok) { box.innerHTML = "<div class='form-hint'>" + esc(d.error) + "</div>"; return; }
        var rows = d.stop_orders || [];
        if (!rows.length) {
          box.innerHTML = "<div class='form-hint'>Активных стоп-заявок нет.</div>";
          return;
        }
        box.innerHTML = "<table class='data-table' style='width:100%'><tr><th>Тикер</th><th>Тип</th><th>Сторона</th><th>Лоты</th><th>Стоп</th><th>Статус</th><th></th></tr>" +
          rows.map(function (s) {
            return "<tr><td>" + esc(s.ticker || s.figi) + "</td><td>" + esc(s.order_type) +
              "</td><td>" + esc(s.direction) + "</td><td>" + esc(s.lots) +
              "</td><td>" + esc(s.stop_price) + "</td><td>" + esc(s.status) +
              "</td><td><button class='btn btn-ghost btn-sm stop-cancel' data-id='" +
              esc(s.stop_order_id) + "'>Отменить</button></td></tr>";
          }).join("") + "</table>";
        Array.prototype.forEach.call(box.querySelectorAll(".stop-cancel"), function (b) {
          b.onclick = function () {
            if (!confirm("Отменить стоп-заявку?")) return;
            post("/api/tinvest/stoporder/cancel", {
              account_id: tiState.account, stop_order_id: b.getAttribute("data-id"),
            }).then(function (r) {
              toast(r.ok ? "Запрос на отмену стопа отправлен" : (r.error || "Ошибка"),
                r.ok ? "success" : "error");
              setTimeout(loadStops, 800);
            }).catch(showErr);
          };
        });
      }).catch(showErr);
  }

  // ================= boot =================
  // ------------------------------------------------------------------
  // §5.1 modes + scheduled auto-trading (tab «Авто»)
  // ------------------------------------------------------------------
  var atModeInfo = { modes: [], descriptions: {} };
  var atTimer = null;

  function atActive() {
    var p = $("tab-autotrade");
    return p && p.classList.contains("active");
  }

  function atRenderMode(d) {
    if (!d || d.ok === false) return;
    atModeInfo = d;
    var sel = $("at-mode");
    if (sel) {
      sel.innerHTML = (d.modes || []).map(function (m) {
        var label = (d.descriptions || {})[m] || m;
        return '<option value="' + esc(m) + '">' + esc(label) + "</option>";
      }).join("");
      sel.value = d.mode || "disabled";
      atModeSync();
    }
    var badge = $("at-mode-env");
    if (badge) badge.textContent = d.environment || "—";
  }

  function atModeSync() {
    var sel = $("at-mode");
    if (!sel) return;
    var mode = sel.value;
    var wrap = $("at-mode-confirm-wrap");
    if (wrap) wrap.style.display = mode === "live_trading" ? "" : "none";
    if (mode !== "live_trading" && $("at-mode-confirm-live")) {
      $("at-mode-confirm-live").checked = false;
    }
  }

  function atSaveMode() {
    var mode = ($("at-mode") || {}).value;
    if (!mode) return;
    var body = { mode: mode };
    if (mode === "live_trading") {
      var cb = $("at-mode-confirm-live");
      if (!cb || !cb.checked) {
        toast("Отметьте подтверждение, чтобы включить боевой режим", "error");
        return;
      }
      body.confirm_live = true;
    }
    post("/api/tinvest/mode", body).then(function (r) {
      if (!r.ok) return showErr(r);
      toast("Режим: " + r.mode, "success");
      atRenderMode(r);
      atLoad();
      tiStatus();
    }).catch(showErr);
  }

  var atRejectLabels = {
    not_trading: "не торгуется", no_price: "нет котировки", cheap: "дешёвая",
    illiquid: "неликвидная", wide_spread: "широкий спред",
    low_list: "листинг ниже требуемого", excluded: "в стоп-листе"
  };

  function fmtRub(v) {
    if (v == null) return "—";
    v = Number(v);
    if (v >= 1e9) return (v / 1e9).toFixed(1) + " млрд";
    if (v >= 1e6) return (v / 1e6).toFixed(0) + " млн";
    if (v >= 1e3) return (v / 1e3).toFixed(0) + " тыс";
    return String(Math.round(v));
  }

  function atKpiCard(label, value) {
    return '<div style="background:rgba(128,128,128,.08);border-radius:8px;padding:10px">' +
      '<div class="muted" style="font-size:11px">' + esc(label) + "</div>" +
      '<div style="font-size:18px;font-weight:600">' + esc(String(value)) + "</div></div>";
  }

  function atScrKpi(d) {
    var el = $("at-scr-kpi");
    if (!el) return;
    var b = d.orders_budget || {};
    var sig = d.signals_today || {};
    var scr = d.screener || {};
    var scan = scr.last_scan || {};
    el.innerHTML =
      atKpiCard("Заявок сегодня", (b.used != null ? b.used : (d.orders_today || 0)) + " / " + (b.limit || 0)) +
      atKpiCard("Осталось заявок", b.left != null ? b.left : "—") +
      atKpiCard("Просканировано тикеров", scan.scanned != null ? scan.scanned : "—") +
      atKpiCard("Прошло фильтры", scan.passed != null ? scan.passed : "—") +
      atKpiCard("Анализов сегодня", (scr.funded_today != null ? scr.funded_today : 0) + " / " + (((scr.settings || {}).max_candidates_per_day) || "—")) +
      atKpiCard("Отклонено сигналов", sig.rejected != null ? sig.rejected : "—");
  }

  function atScrScan(scan) {
    var rej = $("at-scr-rejects");
    var cand = $("at-scr-candidates");
    if (!scan) {
      if (rej) rej.textContent = "Сканов ещё не было.";
      if (cand) cand.innerHTML = "<span class='muted'>—</span>";
      return;
    }
    if (rej) {
      if (scan.note) {
        rej.textContent = scan.note + (scan.at ? " (" + scan.at + ")" : "");
      } else {
        var parts = Object.keys(scan.rejected || {}).map(function (k) {
          return (atRejectLabels[k] || k) + ": " + scan.rejected[k];
        });
        var def = scan.deferred || {};
        if ((def.decided || []).length) parts.push("решение принято сегодня: " + def.decided.length);
        if ((def.holding || []).length) parts.push("позиция открыта: " + def.holding.length);
        rej.textContent = (parts.join(" · ") || "отклонений нет") + (scan.at ? " · " + scan.at : "");
      }
    }
    if (cand) {
      var rows = (scan.candidates || []).map(function (c) {
        return "<div style='display:flex;gap:10px;padding:2px 0;border-bottom:1px solid rgba(128,128,128,.15)'>" +
          "<strong style='min-width:60px'>" + esc(c.secid) + "</strong>" +
          "<span>" + esc(c.last != null ? String(c.last) : "—") + " ₽</span>" +
          '<span class="muted">оборот ' + esc(fmtRub(c.turnover_rub)) + "</span>" +
          '<span class="muted">спред ' + esc(c.spread_pct != null ? Number(c.spread_pct).toFixed(2) : "—") + "%</span></div>";
      }).join("");
      cand.innerHTML = rows ||
        "<span class='muted'>" + esc(scan.note || "Кандидатов нет.") + "</span>";
    }
  }

  function atOrders(rows) {
    var el = $("at-orders");
    if (!el) return;
    if (!rows.length) {
      el.innerHTML = "<span class='muted'>Заявок сегодня не было.</span>";
      return;
    }
    el.innerHTML = rows.map(function (r) {
      var bits = [
        esc(String(r.ts || "").slice(11, 19) || r.date || ""),
        "<strong>" + esc(r.ticker || "?") + "</strong>",
        esc(r.side || ""),
        esc(r.kind || "open") + (r.lots != null ? " ×" + esc(String(r.lots)) : ""),
        r.placed ? "✓ отправлена" : "✕ не отправлена",
        esc(r.status || ""),
        r.order_id ? '<span class="muted">' + esc(String(r.order_id).slice(0, 8)) + "</span>" : "",
        r.counts_toward_limit === false ? '<span class="muted">(лимит не расходует)</span>' : "",
        r.reason ? '<span class="muted">' + esc(r.reason) + "</span>" : ""
      ];
      return "<div style='display:flex;gap:10px;padding:2px 0;border-bottom:1px solid rgba(128,128,128,.15);flex-wrap:wrap'>" +
        bits.filter(Boolean).join(" · ") + "</div>";
    }).join("");
  }

  function atRender(d) {
    if (!d || d.ok === false) return;
    var s = d.settings || {};
    var set = function (id, v) { var e = $(id); if (e) e.value = v == null ? "" : v; };
    var chk = function (id, v) { var e = $(id); if (e) e.checked = !!v; };
    chk("at-enabled", s.enabled);
    set("at-env", s.environment || "sandbox");
    set("at-tick", s.tick_seconds);
    set("at-watch", (s.watchlist || []).join(", "));
    set("at-maxorders", s.max_orders_per_day);
    set("at-maxpos", s.max_open_positions);
    chk("at-markethours", s.only_when_market_open);
    chk("at-skipholding", s.skip_if_holding);
    chk("at-kill", s.kill_switch);
    chk("at-liveconf", s.live_confirmed);

    var scr = d.screener || {};
    var ss = scr.settings || {};
    chk("at-screener", scr.enabled);
    set("at-scr-turnover", ss.min_turnover_rub);
    set("at-scr-spread", ss.max_spread_pct);
    set("at-scr-price", ss.min_price);
    set("at-scr-cap", ss.max_candidates_per_day);
    set("at-scr-exclude", (ss.exclude || []).join(", "));
    var scrBadge = $("at-scr-state");
    if (scrBadge) {
      scrBadge.textContent = scr.enabled ? "ВКЛ" : "ВЫКЛ";
      scrBadge.className = "badge" + (scr.enabled ? " badge-purple" : "");
    }
    atScrKpi(d);
    atScrScan(scr.last_scan || null);
    atOrders(d.orders || []);

    var liveWrap = $("at-liveconf-wrap");
    if (liveWrap) liveWrap.style.display = d.environment === "live" ? "" : "none";

    var state = $("at-state");
    if (state) {
      state.textContent = s.enabled ? (s.kill_switch ? "СТОП" : "ВКЛ") : "ВЫКЛ";
      state.className = "badge" + (s.enabled && !s.kill_switch ? " badge-purple" : "");
    }

    // Gate chain — one row per precondition, honest reason when blocked.
    var g = d.gates || {};
    var rows = (g.gates || []).map(function (x) {
      return '<div style="display:flex;gap:8px;align-items:baseline;padding:3px 0;' +
        "color:" + (x.ok ? "inherit" : "#e05353") + '">' +
        "<span>" + (x.ok ? "✓" : "✕") + "</span>" +
        "<strong style='min-width:150px'>" + esc(x.name) + "</strong>" +
        "<span>" + esc(x.detail) + "</span></div>";
    }).join("");
    var gates = $("at-gates");
    if (gates) gates.innerHTML = rows || "—";

    var cyc = $("at-cycle");
    if (cyc) {
      var lc = d.last_cycle;
      if (!lc) cyc.textContent = "Циклов ещё не было.";
      else if (lc.skipped) cyc.textContent = lc.skipped;
      else cyc.textContent =
        (lc.at || "") + " · " + (lc.ok ? "ок" : "заблокирован") +
        (lc.blocked_by ? " (" + lc.blocked_by + ": " + lc.reason + ")" : "") +
        (lc.results && lc.results.length
          ? " · тикеров обработано: " + lc.results.length +
            (lc.orders_today != null ? " · заявок сегодня: " + lc.orders_today : "")
          : "");
    }
    if (d.last_error) {
      var le = $("at-cycle");
      if (le) le.textContent += " · ошибка: " + d.last_error;
    }

    atLog(d.journal || []);
  }

  function atLog(rows) {
    var el = $("at-log");
    if (!el) return;
    if (!rows.length) { el.innerHTML = "<span class='muted'>Журнал пуст.</span>"; return; }
    el.innerHTML = rows.slice().reverse().map(function (r) {
      var txt = r.event || "?";
      if (r.ticker) txt += " " + r.ticker;
      if (r.action || r.side) txt += " " + (r.action || r.side);
      if (r.gate) txt += " [" + r.gate + "]";
      if (r.reason || r.error) txt += " — " + (r.reason || r.error);
      if (r.mode && r.event === "mode_changed") txt = "режим → " + r.mode;
      return "<div style='padding:2px 0;border-bottom:1px solid rgba(128,128,128,.15)'>" +
        "<span class='muted'>" + esc(r.ts || r.date || "") + "</span> " + esc(txt) + "</div>";
    }).join("");
  }

  function atLoad() {
    get("/api/autotrade").then(atRender).catch(function () {});
    get("/api/tinvest/mode").then(atRenderMode).catch(function () {});
  }

  function atSave() {
    var body = {
      enabled: $("at-enabled") ? $("at-enabled").checked : false,
      environment: ($("at-env") || {}).value || "sandbox",
      live_confirmed: $("at-liveconf") ? $("at-liveconf").checked : false,
      kill_switch: $("at-kill") ? $("at-kill").checked : false,
      only_when_market_open: $("at-markethours") ? $("at-markethours").checked : true,
      skip_if_holding: $("at-skipholding") ? $("at-skipholding").checked : true,
      tick_seconds: ($("at-tick") || {}).value || 300,
      max_orders_per_day: ($("at-maxorders") || {}).value || 0,
      max_open_positions: ($("at-maxpos") || {}).value || 0,
      watchlist: String(($("at-watch") || {}).value || "")
        .split(/[,\s]+/).filter(Boolean),
      screener: {
        enabled: $("at-screener") ? $("at-screener").checked : false,
        min_turnover_rub: ($("at-scr-turnover") || {}).value || 1000000,
        max_spread_pct: ($("at-scr-spread") || {}).value || 1.5,
        min_price: ($("at-scr-price") || {}).value || 1,
        max_candidates_per_day: ($("at-scr-cap") || {}).value || 3,
        exclude: String(($("at-scr-exclude") || {}).value || "")
          .split(/[,\s]+/).filter(Boolean),
      },
    };
    if (body.enabled && body.environment === "live" && !body.live_confirmed) {
      toast("Для live обязательно отдельное подтверждение", "error");
      return;
    }
    post("/api/autotrade", body).then(function (r) {
      if (!r.ok) return showErr(r);
      toast("Авторежим сохранён", "success");
      atLoad();
    }).catch(showErr);
  }

  function atScrPreview() {
    var btn = $("at-scr-preview");
    if (btn) { btn.disabled = true; btn.textContent = "Сканирую борд…"; }
    var done = function () {
      if (btn) { btn.disabled = false; btn.textContent = "Показать кандидатов сейчас"; }
    };
    get("/api/autotrade/screener").then(function (r) {
      done();
      if (!r || r.ok === false) return showErr(r);
      atScrScan(r);
      toast("Скан готов: " + (r.scanned != null ? r.scanned : 0) +
            " тикеров, кандидатов " + ((r.candidates || []).length), "success");
    }).catch(function (e) { done(); showErr(e); });
  }

  function atRun(force) {
    post("/api/autotrade/run", force ? { force: true } : {}).then(function (r) {
      if (!r.ok) return showErr(r);
      toast(r.cycle && r.cycle.ok ? "Цикл выполнен" : "Цикл заблокирован гейтами",
            r.cycle && r.cycle.ok ? "success" : "info");
      atLoad();
    }).catch(showErr);
  }

  function atStartPolling() {
    if (atTimer) clearInterval(atTimer);
    atTimer = setInterval(function () {
      if (atActive()) atLoad();
    }, 20000);
  }

  document.addEventListener("DOMContentLoaded", function () {
    // --- analyze tab: market selector ---
    var row = document.querySelector("#tab-analyze .form-row.tight");
    var tickerGroup = document.querySelector("#tab-analyze #ticker-input") && document.querySelector("#tab-analyze #ticker-input").closest(".form-group");
    if (row && tickerGroup && !$("market-select")) {
      var mgroup = document.createElement("div");
      mgroup.className = "form-group flex1";
      mgroup.innerHTML =
        "<label for='market-select'>Market</label>" +
        "<select id='market-select' class='input-lg'><option value='US'>US / global</option><option value='MOEX'>MOEX (Москва)</option></select>" +
        "<div id='board-group' style='display:none'><label for='board-input' style='margin-top:6px'>Board</label>" +
        "<input id='board-input' type='text' class='input-lg' placeholder='TQBR' maxlength='7'></div>" +
        "<span class='form-hint' id='market-hint'>Original behaviour — US/JP/CN/HK data sources.</span>";
      row.insertBefore(mgroup, tickerGroup);
      $("market-select").addEventListener("change", onMarketChange);
      onMarketChange();
    }

    // --- MOEX tab ---
    var sb = $("moex-search-btn");
    if (sb) {
      sb.addEventListener("click", searchInstruments);
      $("moex-search").addEventListener("keydown", function (e) { if (e.key === "Enter") searchInstruments(); });
    }
    var nr = $("moex-news-refresh");
    if (nr) nr.addEventListener("click", refreshNews);
    var np = $("moex-news-preview-btn");
    if (np) np.addEventListener("click", previewNews);
    var rs = $("risk-save");
    if (rs) rs.addEventListener("click", saveRisk);
    loadNewsSources();
    loadRisk();
    loadLessons();

    // --- Sandbox tab ---
    var cc = $("ti-create-btn");
    if (cc) cc.addEventListener("click", function () {
      var cur = ($("ti-create-cur") || {}).value || "rub";
      if (!confirm("Создать НОВЫЙ виртуальный счёт в песочнице (" + cur.toUpperCase() + ")?")) return;
      post("/api/tinvest/accounts", { action: "create", currency: cur }).then(function (r) {
        toast(r.ok ? "Виртуальный счёт создан и выбран" : (r.error || "Ошибка"), r.ok ? "success" : "error");
        if (r.ok) tiState.account = (r.account || {}).id || "";
        tiStatus();
      }).catch(showErr);
    });
    var pi = $("ti-payin-btn");
    if (pi) pi.addEventListener("click", payIn);
    var sy = $("ti-sync-btn");
    if (sy) sy.addEventListener("click", syncNow);
    var dr = $("ti-dash-refresh");
    if (dr) dr.addEventListener("click", loadDashboard);
    var op = $("ord-preview-btn");
    if (op) op.addEventListener("click", orderPreview);
    var pl = $("ord-place-btn");
    if (pl) { pl.addEventListener("click", orderPlace); pl.style.display = "none"; }
    var sp = $("stop-place-btn");
    if (sp) sp.addEventListener("click", stopPlace);
    tiStatus();
    loadDashboard();
    if (tiState.account) { loadBalance(); loadOrders(); loadOperations(); }
    else { loadBalance(); loadOrders(); loadOperations(); }

    // --- Auto-trading tab (§5.1 modes + §6 cycle) ---
    var st = document.createElement("style");
    st.textContent =
      ".at-check{display:flex;gap:8px;align-items:flex-start;font-size:13px;line-height:1.45}" +
      ".at-check input{margin-top:2px}";
    document.head.appendChild(st);
    if ($("at-mode")) $("at-mode").addEventListener("change", atModeSync);
    if ($("at-mode-save")) $("at-mode-save").addEventListener("click", atSaveMode);
    if ($("at-save")) $("at-save").addEventListener("click", atSave);
    if ($("at-run")) $("at-run").addEventListener("click", function () { atRun(false); });
    if ($("at-refresh")) $("at-refresh").addEventListener("click", atLoad);
    if ($("at-log-refresh")) $("at-log-refresh").addEventListener("click", atLoad);
    if ($("at-scr-preview")) $("at-scr-preview").addEventListener("click", atScrPreview);
    atLoad();
    atStartPolling();
  });
})();
