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
        return "<div style='padding:6px 0;border-bottom:1px solid var(--border,#333);'>" +
          "<label style='display:flex;gap:8px;align-items:center;'>" +
          "<input type='checkbox' class='lesson-cb' data-id='" + esc(l.id) + "'" + (l.enabled ? " checked" : "") + ">" +
          "<span style='flex:1'>" + esc(l.ticker || "") + " " + esc(l.side || "") + " → " + esc(l.result_text || l.outcome || "") +
          " <span style='opacity:.5;font-size:11px'>" + esc(l.recorded_at || "") + "</span></span></label>" +
          "<div style='font-size:12px;opacity:.85;padding-left:26px;'>" + esc(l.lesson || l.summary || "") + "</div>" +
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
        banner.innerHTML = "<b>T-Invest: не подключён.</b> Задайте токен в переменной окружения TINVEST_TOKEN (только окружение, не в настройках). Реальная торговля отключена (§6.5).";
        banner.style.opacity = "1";
      } else {
        banner.innerHTML = "<b>T-Invest: подключён</b> — режим <span style='color:#2ecc71'>SANDBOX</span>, виртуальный счёт. Активный счёт: " +
          esc(d.active_account || "—") + ". Реальная торговля: выкл.";
        tiState.account = d.active_account || "";
      }
      loadAccounts();
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
        box.innerHTML = "Баланс: <b>" + esc(JSON.stringify(bal.currencies || bal.available || bal)) + "</b>" +
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
    var pos = pf.positions || [];
    if (!pos.length) { box.innerHTML = "<div class='form-hint'>Позиций нет. Стоимость портфеля: " + esc(pf.total_value == null ? "—" : pf.total_value) + " " + esc(pf.currency || "") + "</div>"; return; }
    box.innerHTML = "<table class='data-table' style='width:100%'><tr><th>Тикер</th><th>Лоты</th><th>Ср. цена</th><th>Текущая</th><th>Стоимость</th><th>P&L</th></tr>" +
      pos.map(function (p) {
        return "<tr><td>" + esc(p.ticker) + "</td><td>" + esc(p.quantity_lots) + "</td><td>" + esc(p.avg_price) +
          "</td><td>" + esc(p.current_price) + "</td><td>" + esc(p.value) + "</td><td>" + esc(p.unrealized_pnl) + "</td></tr>";
      }).join("") + "</table><div class='form-hint'>Стоимость: " + esc(pf.total_value == null ? "—" : pf.total_value) + " " + esc(pf.currency || "") +
      " · " + esc(pf.fetched_at || "") + "</div>";
  }

  function syncNow() {
    if (!tiState.account) { toast("Выберите счёт", "error"); return; }
    post("/api/tinvest/sync", { account_id: tiState.account }).then(function (d) {
      var info = $("ti-sync-info");
      if (d.ok) {
        var s = d.snapshot || {};
        if (info) info.textContent = "Синхронизировано: " + (s.synced_at || s.fetched_at || "") + (s.stale ? " (данные могут быть устаревшими)" : "");
        toast("Портфель синхронизирован", "success");
        loadBalance(); loadOrders(); loadOperations();
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
      var pv = d.preview, pr = pv.proposal || {}, rk = pv.risk || {};
      window._lastPreview = pv;
      box.innerHTML =
        "<div><b>ЗАЯВКА (SANDBOX)</b>: " + esc(pr.side) + " " + esc(pr.lots) + " лот(ов) " + esc(pr.ticker) +
        " · " + esc(pr.order_type) + (pr.limit_price ? " @ " + esc(pr.limit_price) : "") +
        " · ≈ " + esc(pr.estimated_amount != null ? pr.estimated_amount : "—") + " " + esc((pv.quote || {}).currency || "") + "</div>" +
        "<div>Средства: " + esc(pv.cash ? JSON.stringify(pv.cash) : "—") + " · Портфель: " + esc(pv.portfolio_total == null ? "—" : pv.portfolio_total) + "</div>" +
        "<div style='color:" + (rk.allowed ? "#2ecc71" : "#e74c3c") + "'>" + (rk.allowed ? "✓ риск: разрешено" : "✗ риск: нарушения") + "</div>" +
        (rk.allowed ? "" : "<ul>" + (rk.violations || []).map(function (v) { return "<li>" + esc(v) + "</li>"; }).join("") + "</ul>") +
        "<div class='form-hint'>preview_id: " + esc(pv.preview_id) + " · " + esc(pv.created_at) + "</div>";
      $("ord-place-btn").style.display = rk.allowed ? "" : "none";
    }).catch(showErr);
  }

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
            "</td><td>" + esc(o.ticker || o.figi || "") + "</td><td>" + esc(o.amount != null ? o.amount : "") + "</td></tr>";
        }).join("") + "</table>";
    }).catch(showErr);
  }

  // ================= boot =================
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
    var op = $("ord-preview-btn");
    if (op) op.addEventListener("click", orderPreview);
    var pl = $("ord-place-btn");
    if (pl) { pl.addEventListener("click", orderPlace); pl.style.display = "none"; }
    tiStatus();
    if (tiState.account) { loadBalance(); loadOrders(); loadOperations(); }
    else { loadBalance(); loadOrders(); loadOperations(); }
  });
})();
