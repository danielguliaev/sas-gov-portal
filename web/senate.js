"use strict";
(() => {
  const API = "/api/v1",
    ASSETS = "/static/senate-assets/";
  const content = document.querySelector("#content");
  const state = {
    auth: null,
    me: null,
    role: sessionStorage.getItem("senate-demo-role") || "",
    token: sessionStorage.getItem("sas-oidc-token") || "",
    config: { document_types: [], categories: [], settings: {} },
    public: {},
    routeId: 0,
    navigationController: null,
    calendar: new Date(),
    editor: null,
  };
  const statusNames = {
    project: "Черновик",
    draft: "Черновик",
    submitted: "Подан",
    committee: "На рассмотрении в комитете",
    awaiting_report: "Ожидает отчёта",
    report_ready: "Отчёт готов",
    held: "Оставлен в комитете",
    revision: "На доработке",
    rejected: "Отклонён",
    ready: "Готов к пленарному рассмотрению",
    ready_for_plenary: "Готов к пленарному рассмотрению",
    agenda: "Включён в повестку",
    published: "Голосование открыто",
    closed: "Заседание завершено",
    failed: "Заседание не состоялось",
    adopted: "Принят",
    accepted: "Принят",
    passed: "Принят",
    not_adopted: "Не принят",
    discussion: "На рассмотрении",
    voting: "Голосование",
    decision: "Решение принято",
  };
  Object.assign(statusNames, {
    committee_review: "На рассмотрении в комитете",
    ready_plenary: "Готов к пленарному рассмотрению",
    committee_rejected: "Отклонён комитетом",
    revision_requested: "На доработке у автора",
    agenda_published: "Включён в повестку",
    not_adopted: "Не принят",
    needs_review: "Требуется пересмотр результатов",
    review_required: "Требуется пересмотр",
  });
  const choiceNames = {
    yes: "За",
    no: "Против",
    abstain: "Воздержался",
    absent: "Отсутствовал",
  };
  const decisionNames = {
    do_pass: "Рекомендовать к принятию",
    amend_do_pass: "Внести поправку и рекомендовать к принятию",
    not_pass: "Не рекомендовать к принятию",
    held: "Оставить в комитете",
    transfer: "Передать в другой комитет",
    amend_transfer: "Внести поправку и передать в другой комитет",
    return_to_author: "Вернуть на доработку",
  };
  const ruleNames = {
    predominantly_for: "Преимущественно за",
    predominantly_yes: "Преимущественно за",
    simple_majority: "Простое большинство",
    absolute_majority: "Абсолютное большинство",
    two_thirds: "2/3",
    predominant: "Преимущественно за",
    simple: "Простое большинство",
    absolute: "Абсолютное большинство",
  };
  const esc = (v) =>
    String(v ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const arr = (v) => (Array.isArray(v) ? v : []);
  const label = (items, id) =>
    arr(items).find((x) => x.id === id)?.name || id || "Не указано";
  const date = (v, withTime = false) => {
    if (!v) return "Не указано";
    const d = new Date(v);
    return Number.isNaN(d.valueOf())
      ? esc(v)
      : d.toLocaleString(
          "ru-RU",
          withTime
            ? {
                day: "2-digit",
                month: "2-digit",
                year: "numeric",
                hour: "2-digit",
                minute: "2-digit",
                timeZoneName: "short",
              }
            : { day: "2-digit", month: "2-digit", year: "numeric" },
        );
  };
  const status = (v) =>
    `<span class="status ${esc(v)}">${esc(statusNames[v] || v)}</span>`;
  const button = (text, action, extra = "") =>
    `<button type="button" data-action="${action}" ${extra}>${text}</button>`;
  function rich(value) {
    const doc = new DOMParser().parseFromString(
      String(value || ""),
      "text/html",
    );
    const allowed = new Set([
      "P",
      "BR",
      "H3",
      "H4",
      "UL",
      "OL",
      "LI",
      "STRONG",
      "EM",
      "B",
      "I",
      "U",
      "BLOCKQUOTE",
      "A",
    ]);
    for (const el of [...doc.body.querySelectorAll("*")]) {
      if (!allowed.has(el.tagName)) {
        el.replaceWith(...el.childNodes);
        continue;
      }
      for (const a of [...el.attributes]) {
        if (
          el.tagName === "A" &&
          a.name === "href" &&
          /^https?:\/\//i.test(a.value)
        )
          continue;
        el.removeAttribute(a.name);
      }
      if (el.tagName === "A") el.setAttribute("rel", "noopener");
    }
    return doc.body.innerHTML;
  }
  function stripped(v) {
    const d = new DOMParser().parseFromString(String(v || ""), "text/html");
    return d.body.textContent || "";
  }
  function headers() {
    const h = { Accept: "application/json" };
    if (state.auth?.mode === "demo" && state.role)
      h["X-Demo-User"] = state.role;
    if (state.auth?.mode === "oidc" && state.token)
      h.Authorization = "Bearer " + state.token;
    return h;
  }
  async function api(path, options = {}) {
    const h = headers();
    if (options.body !== undefined) h["Content-Type"] = "application/json";
    const r = await fetch(API + path, {
      ...options,
      signal:
        options.signal ||
        (!options.method || options.method === "GET"
          ? state.navigationController?.signal
          : undefined),
      headers: { ...h, ...options.headers },
      body:
        options.body === undefined ? undefined : JSON.stringify(options.body),
    });
    const type = r.headers.get("content-type") || "";
    const data = type.includes("json") ? await r.json() : null;
    if (!r.ok) {
      const e = Error(
        data?.error?.message || `Не удалось выполнить запрос (${r.status}).`,
      );
      e.status = r.status;
      e.code = data?.error?.code;
      throw e;
    }
    return data;
  }
  function toast(message, error = false) {
    const t = document.querySelector("#toast");
    t.textContent = message;
    t.className = error ? "error-toast" : "";
    t.hidden = false;
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => (t.hidden = true), 6000);
  }
  async function confirmAction(title, message) {
    const d = document.querySelector("#confirmation");
    document.querySelector("#confirmTitle").textContent = title;
    document.querySelector("#confirmMessage").textContent = message;
    return new Promise((resolve) => {
      d.addEventListener("close", () => resolve(d.returnValue === "ok"), {
        once: true,
      });
      d.showModal();
    });
  }
  const empty = (title, text = "") =>
    `<div class="empty"><h2>${esc(title)}</h2>${text ? `<p>${esc(text)}</p>` : ""}</div>`;
  const heading = (title, subtitle = "", actions = "") =>
    `<div class="page-heading"><div><h1>${esc(title)}</h1>${subtitle ? `<p class="subtitle">${esc(subtitle)}</p>` : ""}</div>${actions ? `<div class="actions">${actions}</div>` : ""}</div>`;
  const field = (name, title, value = "", type = "text", extra = "") =>
    `<label class="field"><span>${title}</span><input name="${name}" type="${type}" value="${esc(value)}" ${extra}></label>`;
  const select = (name, title, options, value = "", extra = "") =>
    `<label class="field"><span>${title}</span><select name="${name}" ${extra}>${options.map((o) => `<option value="${esc(o.id)}" ${String(o.id) === String(value) ? "selected" : ""}>${esc(o.name)}</option>`).join("")}</select></label>`;
  const textArea = (name, title, value = "", extra = "") =>
    `<label class="field"><span>${title}</span><textarea name="${name}" ${extra}>${esc(value)}</textarea></label>`;
  function isFlag(flag) {
    return (
      !!state.me &&
      (arr(state.me.flags).includes(flag) || arr(state.me.roles).includes(flag))
    );
  }
  const isAdmin = () => !!state.me?.capabilities?.admin || isFlag("superadmin");
  const canCreate = () =>
    !!state.me?.capabilities?.create_bill ||
    isFlag("senator") ||
    isFlag("legislator");
  const canManageSessions = () => isAdmin() || isFlag("pro_tempore");
  function signedIn() {
    if (state.me) return true;
    content.innerHTML =
      heading("Вход в кабинет") +
      empty(
        "Для этого раздела требуется вход",
        "Откройте меню «Вход» в верхней строке сайта. После входа доступны действия по вашим полномочиям.",
      );
    return false;
  }
  function route() {
    const raw = location.hash.slice(1) || "home";
    const [path, query = ""] = raw.split("?");
    return { parts: path.split("/"), params: new URLSearchParams(query), path };
  }
  function go(path) {
    if (location.hash.slice(1) === path) render();
    else location.hash = path;
  }
  async function refreshMe() {
    state.me = null;
    if (
      (state.auth?.mode === "demo" && state.role) ||
      (state.auth?.mode === "oidc" && state.token)
    ) {
      try {
        state.me = await api("/senate/me");
      } catch (e) {
        if (e.status !== 401) throw e;
        state.role = "";
        state.token = "";
        sessionStorage.removeItem("senate-demo-role");
        sessionStorage.removeItem("sas-oidc-token");
      }
    }
    renderAccount();
  }
  function renderAccount() {
    const menu = document.querySelector("#accountMenu"),
      btn = document.querySelector("#accountButton");
    btn.textContent = state.me ? "Кабинет" : "Вход";
    let html = state.me
      ? `<p><b>${esc(state.me.profile?.name || state.me.id)}</b></p><a href="#cabinet">Мой кабинет</a><a href="#elections">Выбор Pro Tempore</a>${isAdmin() ? '<a href="#admin">Администрирование</a>' + (state.auth?.mode === 'demo' ? '<a href="/accounts">Тестовые аккаунты</a>' : '') : ""}`
      : "<p>Вход для участников Сената</p>";
    if (state.auth?.mode === "demo")
      html += `<label for="demoRole">Локальная демонстрация<select id="demoRole"><option value="">Гость</option>${[
        ["user", "Зарегистрированный читатель"],
        ["legislator", "Сенатор"],
        ["superadmin", "Суперадминистратор"],
        ["lawyer", "Юрист"],
        ["editor", "Редактор"],
        ...(state.testChoices || []).map((c) => [
          c.account_id + "@" + c.character_id,
          c.account_name + " · " + c.name,
        ]),
      ]
        .map(
          ([id, n]) =>
            `<option value="${esc(id)}" ${state.role === id ? "selected" : ""}>${esc(n)}</option>`,
        )
        .join(
          "",
        )}</select></label><p class="small-text muted">Тестовые учётные записи. Не используются при публикации портала.</p>`;
    else if (state.auth?.mode === "oidc")
      html += button(
        state.me ? "Выйти" : "Войти через сервис авторизации",
        state.me ? "logout" : "login",
      );
    menu.innerHTML = html;
  }
  async function startLogin() {
    const cfg = state.auth;
    if (cfg?.mode !== "oidc") return;
    const base64 = (bytes) =>
      btoa(String.fromCharCode(...bytes))
        .replace(/\+/g, "-")
        .replace(/\//g, "_")
        .replace(/=+$/, "");
    const verifier = base64(crypto.getRandomValues(new Uint8Array(32))),
      nonce = base64(crypto.getRandomValues(new Uint8Array(24)));
    const digest = await crypto.subtle.digest(
      "SHA-256",
      new TextEncoder().encode(verifier),
    );
    sessionStorage.setItem("sas-oidc-verifier", verifier);
    sessionStorage.setItem("sas-oidc-state", nonce);
    sessionStorage.setItem("sas-oidc-return", "/senate" + location.hash);
    const u = new URL(cfg.authorization_url);
    Object.entries({
      response_type: "code",
      client_id: cfg.client_id,
      redirect_uri: cfg.redirect_uri,
      scope: cfg.scope,
      state: nonce,
      code_challenge: base64(new Uint8Array(digest)),
      code_challenge_method: "S256",
    }).forEach(([k, v]) => u.searchParams.set(k, v));
    location.assign(u);
  }
  async function finishLogin() {
    const u = new URL(location.href);
    if (!u.searchParams.has("code")) return;
    const expected = sessionStorage.getItem("sas-oidc-state"),
      verifier = sessionStorage.getItem("sas-oidc-verifier");
    if (!expected || u.searchParams.get("state") !== expected || !verifier)
      throw Error(
        "Не пройдена проверка безопасности входа. Начните вход заново.",
      );
    const result = await api("/auth/token", {
      method: "POST",
      body: { code: u.searchParams.get("code"), code_verifier: verifier },
    });
    state.token = result.access_token;
    sessionStorage.setItem("sas-oidc-token", state.token);
    sessionStorage.removeItem("sas-oidc-state");
    sessionStorage.removeItem("sas-oidc-verifier");
    history.replaceState(
      null,
      "",
      sessionStorage.getItem("sas-oidc-return") || "/senate#cabinet",
    );
    sessionStorage.removeItem("sas-oidc-return");
  }
  function breadcrumbs(title) {
    return `<p class="breadcrumbs"><a href="#home">Главная</a> / ${esc(title)}</p>`;
  }
  async function render() {
    state.navigationController?.abort();
    state.navigationController = new AbortController();
    const id = ++state.routeId,
      { parts, params } = route();
    document.querySelector("#accountMenu").hidden = true;
    document
      .querySelector("#accountButton")
      .setAttribute("aria-expanded", "false");
    setMobileNavigation(false);
    for (const submenu of document.querySelectorAll(".has-submenu.open")) {
      submenu.classList.remove("open");
      submenu.querySelector("button").setAttribute("aria-expanded", "false");
    }
    document
      .querySelector("#pageBanner")
      .classList.toggle("internal", parts[0] !== "home");
    document
      .querySelector(".site-shell")
      .classList.toggle(
        "work-page",
        ["editor", "admin", "session"].includes(parts[0]),
      );
    for (const a of document.querySelectorAll(".main-nav a")) {
      if (a.getAttribute("href") === "#" + parts[0])
        a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
    content.innerHTML = '<p class="loading">Загрузка материалов…</p>';
    try {
      const views = {
        home: home,
        bills: () => bills(params),
        archive: () => bills(params, true),
        bill: () => billDetail(parts[1], params),
        editor: () => editor(parts[1]),
        sessions: () => sessions(params),
        session: () => sessionDetail(parts[1]),
        committees: committees,
        committee: () => committeeDetail(parts[1]),
        senators: senators,
        news: () => news(parts[1]),
        cabinet: cabinet,
        elections: elections,
        election: () => electionDetail(parts[1]),
        admin: () => admin(params),
        about: about,
      };
      const view = views[parts[0]];
      if (!view) {
        content.innerHTML =
          heading("Страница не найдена") +
          empty("Такого раздела нет", "Выберите раздел в меню Сената.");
        return;
      }
      await view();
      if (id !== state.routeId) return;
      document.title =
        (content.querySelector("h1")?.textContent || "Главная") +
        " | Сенат штата Сан-Андреас";
    } catch (e) {
      if (id !== state.routeId) return;
      content.innerHTML =
        heading(
          e.status === 403
            ? "Доступ ограничен"
            : e.status === 404
              ? "Материал не найден"
              : "Не удалось загрузить страницу",
        ) +
        `<div class="error"><p>${esc(e.message)}</p>${button("Повторить загрузку", "retry", 'class="secondary"')}</div>`;
    }
  }
  function calendarHtml() {
    const month = state.calendar.getMonth(),
      year = state.calendar.getFullYear(),
      today = new Date(),
      start = new Date(year, month, 1),
      offset = (start.getDay() + 6) % 7;
    let cells = "";
    for (let i = 0; i < 42; i++) {
      const d = new Date(year, month, 1 - offset + i),
        current = d.getMonth() === month;
      const matches = arr(state.public.sessions).filter(
        (s) =>
          new Date(s.starts_at || s.held_at).toDateString() ===
          d.toDateString(),
      );
      const classes = [
        !current ? "outside" : "",
        d.toDateString() === today.toDateString() ? "today" : "",
        matches.length ? "has-event" : "",
      ]
        .filter(Boolean)
        .join(" ");
      const dest =
        matches.length === 1
          ? "session/" + encodeURIComponent(matches[0].id)
          : "sessions?date=" +
            `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      cells += `${i % 7 === 0 ? "<tr>" : ""}<td class="${classes}"><a href="#${dest}" aria-label="${date(d.toISOString())}${matches.length ? ", заседаний: " + matches.length : ""}">${d.getDate()}</a></td>${i % 7 === 6 ? "</tr>" : ""}`;
    }
    return `<div class="calendar-head">${button("‹", "calendar-prev", 'aria-label="Предыдущий месяц"')}<span>${esc(new Date(year, month).toLocaleDateString("ru-RU", { month: "long", year: "numeric" }))}</span>${button("›", "calendar-next", 'aria-label="Следующий месяц"')}</div><table class="calendar"><thead><tr>${["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map((x) => '<th scope="col">' + x + "</th>").join("")}</tr></thead><tbody>${cells}</tbody></table>`;
  }
  async function home() {
    state.public = await api("/senate/public");
    const d = state.public,
      today = new Date(),
      events = arr(d.sessions).filter(
        (s) =>
          new Date(s.starts_at || s.held_at).toDateString() ===
          today.toDateString(),
      ),
      latest = arr(d.news)[0];
    content.innerHTML = `<div class="home-top"><section class="home-message"><h1 class="section-title">Сенат штата Сан-Андреас</h1><img class="message-seal" src="${ASSETS}seal.png" alt="Печать Сената"><p>Добро пожаловать на портал Сената штата Сан-Андреас. Здесь вы можете ознакомиться с законопроектами, работой комитетов и решениями пленарных заседаний.</p><p>Читайте опубликованные документы, следите за повесткой и изучайте поимённые результаты голосований. Все редакции проектов и история рассмотрения доступны в архиве.</p><p class="byline">Законодательные материалы штата<br>Открытый парламентский процесс</p></section><section class="home-events"><h2 class="section-title today-date">События дня: ${date(today.toISOString())}</h2><p class="today-label">Заседания и повестки Сената</p>${events.length ? `<ul class="event-list">${events.map((s) => `<li><time>${date(s.starts_at, true)}</time><a href="#session/${esc(s.id)}">${esc(s.title)}</a></li>`).join("")}</ul>` : '<p class="small-text">На сегодня опубликованных заседаний нет.</p>'}<a class="more" href="#sessions">Посмотреть все заседания Сената</a>${latest ? `<h3>Последняя публикация</h3><a href="#news/${esc(latest.id)}">${esc(latest.title)}</a>` : ""}</section></div><div class="home-middle"><section><h2 class="section-title">Зал заседаний Сената</h2><figure class="home-photo"><img src="${ASSETS}session.webp" alt="Исторический зал Сената в Капитолии Калифорнии"><figcaption>Капитолий Калифорнии. Архитектурный образец портала.</figcaption></figure></section><section><h2 class="section-title">Информация</h2><div class="quick-links"><a class="quick-link" href="#sessions"><span>Повестки<br>заседаний</span></a><a class="quick-link" href="#bills"><span>Найти<br>законопроект</span></a><a class="quick-link" href="#committees"><span>Работа<br>комитетов</span></a><a class="quick-link" href="#senators"><span>Состав<br>Сената</span></a><a class="quick-link" href="/"><span>Правовая<br>библиотека</span></a></div></section><section><h2 class="section-title">Календарь Сената</h2><div id="homeCalendar">${calendarHtml()}</div><p class="calendar-note">Красным отмечены дни заседаний.</p></section></div><div class="home-tiles"><a class="photo-tile" href="#about"><img src="${ASSETS}dome.jpg" alt="Купол Капитолия"><span>О Сенате</span></a><a class="photo-tile" href="#committees"><img src="${ASSETS}capitol.jpg" alt="Здание Капитолия"><span>Комитеты Сената</span></a><a class="photo-tile" href="#archive"><img src="${ASSETS}chamber.jpg" alt="Интерьер зала заседаний"><span>Архив документов</span></a></div>`;
  }
  function billRows(items) {
    return items
      .map(
        (b) =>
          `<tr><td><a href="#bill/${esc(b.id)}">${esc(b.number || b.id.slice(0, 8).toUpperCase())}</a></td><td><a href="#bill/${esc(b.id)}">${esc(b.title)}</a><small>${esc(label(state.config.document_types, b.document_type))}</small></td><td>${esc(b.author_name || b.author_id)}</td><td>${status(b.status)}</td><td>${date(b.submitted_at || b.created_at)}</td></tr>`,
      )
      .join("");
  }
  function billsTable(items) {
    return items.length
      ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>Номер</th><th>Документ</th><th>Автор</th><th>Этап рассмотрения</th><th>Дата</th></tr></thead><tbody>${billRows(items)}</tbody></table></div>`
      : empty(
          "Документы не найдены",
          "Измените условия поиска. Поданные проекты появятся здесь после поступления в Сенат.",
        );
  }
  async function bills(params, archive = false) {
    let items = await api("/senate/bills?" + params.toString()),
      coms = await api("/senate/committees");
    if (archive)
      items = items.filter((b) => !["project", "draft"].includes(b.status));
    const page = Math.max(1, Number(params.get("page")) || 1),
      pageSize = 20,
      total = items.length;
    content.innerHTML =
      breadcrumbs(archive ? "Архив" : "Проекты") +
      heading(
        archive ? "Архив Сената" : "Проекты документов",
        "Тексты, редакции и история рассмотрения",
        canCreate()
          ? '<a class="button" href="#editor/new">Создать проект</a>'
          : "",
      ) +
      `<form id="billFilters" class="filters" data-target="${archive ? "archive" : "bills"}"><label><span>Название, номер или автор</span><input type="search" name="q" value="${esc(params.get("q") || "")}" placeholder="Поиск проекта"></label><label><span>Тип документа</span><select name="type"><option value="">Все типы</option>${arr(
        state.config.document_types,
      )
        .map(
          (o) =>
            `<option value="${esc(o.id)}" ${params.get("type") === o.id ? "selected" : ""}>${esc(o.name)}</option>`,
        )
        .join(
          "",
        )}</select></label><label><span>Категория</span><select name="category"><option value="">Все категории</option>${arr(
        state.config.categories,
      )
        .map(
          (o) =>
            `<option value="${esc(o.id)}" ${params.get("category") === o.id ? "selected" : ""}>${esc(o.name)}</option>`,
        )
        .join(
          "",
        )}</select></label><label><span>Комитет</span><select name="committee"><option value="">Все комитеты</option>${arr(
        coms,
      )
        .map(
          (o) =>
            `<option value="${esc(o.id)}" ${params.get("committee") === o.id ? "selected" : ""}>${esc(o.name)}</option>`,
        )
        .join(
          "",
        )}</select></label><label><span>Статус</span><select name="status"><option value="">Все статусы</option>${["committee_review", "held", "ready_plenary", "adopted", "rejected", "committee_rejected"].map((k) => `<option value="${k}" ${params.get("status") === k ? "selected" : ""}>${statusNames[k]}</option>`).join("")}</select></label>${field("author", "Автор: имя или ID", params.get("author") || "")}${field("coauthor", "Соавтор: имя или ID", params.get("coauthor") || "")}${field("date_from", "Дата подачи с", params.get("date_from") || "", "date")}${field("date_to", "Дата подачи по", params.get("date_to") || "", "date")}<button type="submit">Найти</button><a href="#${archive ? "archive" : "bills"}">Сбросить</a></form><p class="small-text muted">Найдено документов: ${total}</p>${billsTable(items.slice((page - 1) * pageSize, page * pageSize))}${total > pageSize ? `<div class="pagination"><span>Страница ${page} из ${Math.ceil(total / pageSize)}</span><div class="actions">${page > 1 ? button("Предыдущая", "page", 'data-page="' + (page - 1) + '" class="secondary small"') : ""}${page * pageSize < total ? button("Следующая", "page", 'data-page="' + (page + 1) + '" class="secondary small"') : ""}</div></div>` : ""}`;
  }
  async function billDetail(id, params) {
    const b = await api("/senate/bills/" + encodeURIComponent(id)),
      tab = params.get("tab") || "text";
    const mine =
        state.me &&
        (b.author_id === state.me.id ||
          arr(b.coauthors).some(
            (x) => x.user_id === state.me.id && x.status === "accepted",
          )),
      editable =
        state.me &&
        (isAdmin() || mine) &&
        [
          "draft",
          "project",
          "held",
          "revision_requested",
          "revision",
          "returned",
        ].includes(b.status);
    const chosen =
      Number(params.get("version")) ||
      b.version ||
      Math.max(1, ...arr(b.revisions).map((r) => r.version));
    const rev = arr(b.revisions).find((r) => r.version === chosen);
    let actions = editable
      ? `<a class="button secondary" href="#editor/${esc(id)}">Редактировать</a>`
      : "";
    if (
      editable &&
      [
        "project",
        "draft",
        "held",
        "revision_requested",
        "revision",
        "returned",
      ].includes(b.status)
    )
      actions += button(
        ["held"].includes(b.status) ? "Запросить пересмотр" : "Подать проект",
        "submit-bill",
        `data-id="${esc(id)}"`,
      );
    actions +=
      button(
        "PDF",
        "download",
        `class="secondary small" data-url="/senate/bills/${esc(id)}/export.pdf" data-file="${esc(b.number || id)}.pdf"`,
      ) +
      button(
        "JSON",
        "download",
        `class="secondary small" data-url="/senate/bills/${esc(id)}/export.json" data-file="${esc(b.number || id)}.json"`,
      );
    let body = "";
    if (tab === "text") {
      body = `<div class="revision-select"><span>Редакция</span><select id="revisionSelect" data-id="${esc(id)}">${arr(
        b.revisions,
      )
        .map(
          (r) =>
            `<option value="${r.version}" ${chosen === r.version ? "selected" : ""}>№ ${r.version} · ${date(r.created_at)}</option>`,
        )
        .join(
          "",
        )}</select></div>${chosen !== b.version ? '<div class="notice">Вы просматриваете прежнюю редакцию. Для текущего текста выберите последнюю редакцию в списке.</div>' : ""}<article class="document-text">${rich(rev?.text_rich || b.text_rich)}</article>`;
    } else if (tab === "changes") {
      body = arr(b.changes).length
        ? b.changes
            .map(
              (ch) =>
                `<section class="change-row"><h3>${esc({ add: "Добавление", amend: "Изменение", repeal: "Отмена" }[ch.operation] || ch.operation)} · ${esc(ch.title || ch.article?.title || ch.target?.number || ch.article_id || "Новое положение")}</h3><p class="small-text muted">ID: ${esc(ch.target?.article_id || ch.article_id || ch.target?.node_id || "Новая статья")}</p><div class="compare-grid"><div><h4>Исходная редакция</h4>${rich(ch.original_text || ch.base_text || ch.source_text_rich || ch.source?.text_rich || ch.original?.text_rich || "Не применимо")}</div><div><h4>Предлагаемая редакция</h4>${ch.operation === "repeal" ? "<p>Положение предлагается отменить.</p>" : rich(ch.text_rich || ch.article?.text_rich || "")}</div></div></section>`,
            )
            .join("")
        : empty(
            "Отдельные поправки не указаны",
            "Полный текст документа доступен во вкладке «Текст».",
          );
    } else if (tab === "reviews") {
      body = arr(b.reviews).length
        ? b.reviews
            .map(
              (r) =>
                `<section class="panel"><h2>${esc(r.committee_name || r.committee_id)} · цикл ${r.cycle}</h2><p class="small-text">Редакция № ${r.version} · Передано ${date(r.assigned_at, true)} · Срок ${date(r.due_at, true)}</p><h3>Отчёт консультанта</h3>${r.report_text ? `<p>${esc(r.report_text).replace(/\n/g, "<br>")}</p><p class="small-text">${esc(r.report_by)} · ${esc(decisionNames[r.recommendation] || r.recommendation)}</p>` : '<p class="muted">Отчёт ещё не предоставлен.</p>'}<h3>Решение председателя</h3><p>${esc(decisionNames[r.decision] || r.decision || "Решение не зафиксировано.")}</p>${r.comment ? `<p>${esc(r.comment)}</p>` : ""}${r.transcript ? `<details><summary>Стенограмма совещания</summary><p>${esc(r.transcript).replace(/\n/g, "<br>")}</p></details>` : ""}<a href="#committee/${esc(r.committee_id)}">Перейти в комитет</a></section>`,
            )
            .join("")
        : empty(
            "Рассмотрений пока нет",
            "После подачи здесь появятся отчёты, решения и циклы рассмотрения.",
          );
    } else if (tab === "versions") {
      body = `<div class="table-wrap"><table class="data-table"><thead><tr><th>Редакция</th><th>Дата</th><th>Автор</th><th>Текст</th></tr></thead><tbody>${arr(
        b.revisions,
      )
        .map(
          (r) =>
            `<tr><td>№ ${r.version}</td><td>${date(r.created_at, true)}</td><td>${esc(r.author_id)}</td><td><a href="#bill/${esc(id)}?version=${r.version}">Открыть редакцию</a></td></tr>`,
        )
        .join(
          "",
        )}</tbody></table></div><h2 class="panel">История действий</h2><ol class="history">${arr(
        b.events,
      )
        .map(
          (e) =>
            `<li><time>${date(e.created_at, true)} · ${esc(e.actor_id)}</time><b>${esc(statusNames[e.event_type] || e.event_type)}</b><p>${esc(typeof e.body === "string" ? e.body : JSON.stringify(e.body || {}))}</p></li>`,
        )
        .join("")}</ol>`;
    } else if (tab === "votes") {
      body = arr(b.votes).length
        ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>Заседание</th><th>Результат</th><th>Дата</th></tr></thead><tbody>${b.votes.map((v) => `<tr><td>${v.session_id ? `<a href="#session/${esc(v.session_id)}">${esc(v.session_title || "Заседание Сената")}</a>` : "Комитет"}</td><td>${status(v.result)}</td><td>${date(v.created_at)}</td></tr>`).join("")}</tbody></table></div>`
        : empty("Голосований пока нет");
    }
    content.innerHTML =
      breadcrumbs("Проект документа") +
      heading(b.title, b.number || "Проект документа", actions) +
      status(b.status) +
      `<dl class="metadata"><div><dt>Автор</dt><dd>${esc(b.author_name || b.author_id)}</dd></div><div><dt>Соавторы</dt><dd>${
        arr(b.coauthors)
          .filter((x) => x.status === "accepted")
          .map((x) => esc(x.name || x.user_id))
          .join(", ") || "Нет подтверждённых соавторов"
      }</dd></div><div><dt>Тип документа</dt><dd>${esc(label(state.config.document_types, b.document_type))}</dd></div><div><dt>Категория</dt><dd>${esc(label(state.config.categories, b.category_id))}</dd></div><div><dt>Дата подачи</dt><dd>${b.submitted_at ? date(b.submitted_at) : "Ещё не подан"}</dd></div><div><dt>Вступление в силу</dt><dd>${date(b.effective_at)}</dd></div></dl><nav class="tabs" aria-label="Разделы документа">${[
        ["text", "Текст"],
        ["changes", "Поправки"],
        ["reviews", "Рассмотрение"],
        ["versions", "Редакции"],
        ["votes", "Голосования"],
      ]
        .map(
          ([t, n]) =>
            `<a class="${tab === t ? "active" : ""}" href="#bill/${esc(id)}?tab=${t}" ${tab === t ? 'aria-current="page"' : ""}>${n}</a>`,
        )
        .join(
          "",
        )}</nav>${body}${isAdmin() ? `<details class="panel"><summary>Административное исправление предмета</summary><form id="billCorrectionForm" data-id="${esc(id)}" data-version="${b.version}">${field("title", "Исправленное название", b.title)}${select("status", "Состояние", [{ id: "", name: "Оставить текущее" }, ...["draft", "committee_review", "held", "revision_requested", "ready_plenary", "adopted", "rejected", "committee_rejected"].map((v) => ({ id: v, name: statusNames[v] }))])}${textArea("reason", "Основание исправления", "", "required")}<p class="small-text muted">Исходные значения сохранятся в журнале. Изменение итогового статуса не применяет закон к библиотеке.</p><div class="form-error" role="alert"></div><button type="submit">Сохранить исправление</button></form></details>` : ""}${editable ? `<details class="panel"><summary>Пригласить соавтора</summary><form id="coauthorForm" data-id="${esc(id)}">${field("user_id", "Идентификатор зарегистрированного пользователя", "", "text", "required")}<p class="small-text muted">Соавторство начнёт действовать после подтверждения приглашения.</p><div class="form-error" role="alert"></div><button type="submit">Отправить приглашение</button></form></details>` : ""}`;
  }
  function editorCapture() {
    if (!state.editor) return;
    const form = document.querySelector("#billEditor");
    if (!form) return;
    for (const name of [
      "title",
      "document_type",
      "category_id",
      "effective_at",
    ])
      if (form.elements[name]) state.editor[name] = form.elements[name].value;
    const richBox = document.querySelector("#richEditor");
    if (richBox) state.editor.text_rich = rich(richBox.innerHTML);
    for (const row of document.querySelectorAll("[data-change-index]")) {
      const ch = state.editor.changes[Number(row.dataset.changeIndex)];
      if (row.querySelector("select"))
        ch.operation = row.querySelector("select").value;
      if (row.querySelector("textarea")) {
        ch.text_rich =
          "<p>" +
          esc(row.querySelector("textarea").value).replace(/\n/g, "</p><p>") +
          "</p>";
        ch.article = { ...(ch.article || {}), text_rich: ch.text_rich };
      }
      for (const input of row.querySelectorAll("[data-change-field]")) {
        const name = input.dataset.changeField;
        if (name === "code_id") {
          ch.code_id = input.value;
          ch.target = { ...(ch.target || {}), code_id: input.value };
        } else {
          ch.article = { ...(ch.article || {}), [name]: input.value };
          ch[name] = input.value;
        }
      }
    }
  }
  async function editor(id) {
    if (!signedIn()) return;
    if (!canCreate() && !isAdmin()) {
      content.innerHTML =
        heading("Редактор проектов") +
        empty("Право подачи есть только у сенаторов");
      return;
    }
    const key = id || "new";
    if (!state.editor || state.editor.id !== key) {
      const b =
        key !== "new"
          ? await api("/senate/bills/" + encodeURIComponent(key))
          : {};
      state.editor = {
        ...b,
        id: key,
        title: b.title || "",
        document_type:
          b.document_type ||
          state.config.document_types.find((t) => t.active)?.id ||
          "senate_bill",
        category_id:
          b.category_id ||
          state.config.categories.find((t) => t.active)?.id ||
          "general",
        effective_at: b.effective_at?.slice(0, 10) || "",
        text_rich: b.text_rich || "<p></p>",
        changes: arr(b.changes).map((x) => ({ ...x })),
        step: 0,
      };
    }
    drawEditor();
  }
  function drawEditor() {
    const e = state.editor,
      type = state.config.document_types.find((t) => t.id === e.document_type);
    let required = [];
    try {
      required = Array.isArray(type?.required_fields)
        ? type.required_fields
        : JSON.parse(type?.required_fields_json || "[]");
    } catch {}
    const requiredLabels = {
      title: "Название",
      text_rich: "Текст проекта",
      effective_at: "Дата вступления в силу",
      changes: "Поправки",
      category_id: "Категория",
    };
    let body = "";
    if (e.step === 0)
      body = `<div class="form-grid"><label class="field full"><span>Название проекта *</span><input name="title" maxlength="250" required value="${esc(e.title)}"></label><div>${select(
        "document_type",
        "Тип документа",
        state.config.document_types.filter(
          (t) => t.active || t.id === e.document_type,
        ),
        e.document_type,
      )}<p id="typeRequirements" class="small-text muted">Обязательные поля типа: ${required.map((k) => esc(requiredLabels[k] || k)).join(", ") || "название и текст проекта"}.</p>${type?.template ? button("Вставить шаблон типа", "use-template", 'class="secondary small"') : ""}</div>${select(
        "category_id",
        "Категория",
        state.config.categories.filter(
          (c) => c.active || c.id === e.category_id,
        ),
        e.category_id,
      )}${field("effective_at", "Дата вступления в силу", e.effective_at, "date")}<div class="full"><label id="textLabel" class="field"><span>Текст проекта *</span></label><div class="rich-toolbar" aria-label="Форматирование текста">${[
        ["bold", "Жирный"],
        ["italic", "Курсив"],
        ["insertUnorderedList", "Список"],
      ]
        .map(([cmd, n]) =>
          button(n, "format", `data-command="${cmd}" class="secondary small"`),
        )
        .join(
          "",
        )}</div><div id="richEditor" class="rich-editor" contenteditable="true" role="textbox" aria-multiline="true" aria-labelledby="textLabel">${rich(e.text_rich)}</div></div></div>`;
    else if (e.step === 1)
      body = `<p>Выберите положения правовой библиотеки, которые проект добавляет, изменяет или отменяет. Для резолюций список может быть пустым.</p><div class="filters"><label><span>Поиск в правовой библиотеке</span><input id="libraryQuery" type="search" placeholder="Номер или название статьи"></label>${button("Найти положение", "library-search")}</div><div id="libraryResults"></div><div class="actions">${button("Добавить новую статью", "add-change", 'class="secondary"')}</div><div id="changeList">${e.changes
        .map(
          (ch, i) =>
            `<section class="change-row" data-change-index="${i}"><header><h3>${esc(ch.title || ch.article?.title || ch.target?.number || ch.article_id || "Новая статья")}</h3>${button("Убрать", "remove-change", `data-index="${i}" class="text-button"`)}</header><p class="small-text muted">${esc(ch.target?.article_id || ch.article_id || "Новое положение")}</p>${ch.operation === "add" ? `<div class="form-grid"><label class="field"><span>ID кодекса *</span><input data-change-field="code_id" value="${esc(ch.target?.code_id || ch.code_id || "")}" placeholder="ID из правовой библиотеки"></label><label class="field"><span>Номер новой статьи *</span><input data-change-field="number" value="${esc(ch.article?.number || ch.number || "")}"></label><label class="field full"><span>Заголовок статьи *</span><input data-change-field="title" value="${esc(ch.article?.title || ch.title || "")}"></label></div>` : ""}<label class="field"><span>Действие</span><select>${[
              ["amend", "Изменить"],
              ["repeal", "Отменить"],
              ["add", "Добавить"],
            ]
              .map(
                ([v, n]) =>
                  `<option value="${v}" ${ch.operation === v ? "selected" : ""}>${n}</option>`,
              )
              .join(
                "",
              )}</select></label><div class="compare-grid"><div><h4>Исходный текст</h4>${rich(ch.original_text || ch.base_text || ch.source_text_rich || ch.source?.text_rich || "Новая статья")}</div><div><label class="field"><span>Предлагаемый текст</span><textarea>${esc(stripped(ch.text_rich || ch.article?.text_rich || ""))}</textarea></label></div></div></section>`,
        )
        .join("")}</div>`;
    else
      body = `<h2>${esc(e.title || "Проект без названия")}</h2><dl class="metadata"><div><dt>Тип</dt><dd>${esc(label(state.config.document_types, e.document_type))}</dd></div><div><dt>Категория</dt><dd>${esc(label(state.config.categories, e.category_id))}</dd></div><div><dt>Вступление в силу</dt><dd>${date(e.effective_at)}</dd></div></dl><article class="document-text">${rich(e.text_rich)}</article><p>Изменений положений библиотеки: ${e.changes.length}.</p><div class="notice">После подачи проект поступит в Rules Committee. Подача и сохранение черновика являются разными действиями.</div>`;
    content.innerHTML =
      breadcrumbs("Редактор проекта") +
      heading(
        e.id === "new" ? "Новый проект" : "Редактирование проекта",
        "Изменения сохраняются отдельными редакциями",
      ) +
      `<div class="editor-steps">${["Реквизиты и текст", "Изменения в законах", "Проверка и подача"].map((n, i) => button(`${i + 1}. ${n}`, "editor-step", `data-step="${i}" class="${e.step === i ? "active" : ""}"`)).join("")}</div><form id="billEditor"><div>${body}</div><div class="form-error" role="alert"></div><div class="form-status" role="status"></div><div class="form-actions"><a href="${e.id === "new" ? "#cabinet" : "#bill/" + esc(e.id)}" class="button secondary">К документу</a>${button("Сохранить черновик", "save-draft", 'class="secondary"')}${e.step < 2 ? button("Продолжить", "editor-step", `data-step="${e.step + 1}"`) : button("Сохранить и подать", "save-submit")}</div></form>`;
  }
  async function saveEditor(submit = false) {
    editorCapture();
    const e = state.editor;
    const form = document.querySelector("#billEditor");
    if (!e.title.trim() || !stripped(e.text_rich).trim()) {
      form.querySelector(".form-error").textContent =
        "Заполните название и текст проекта.";
      return;
    }
    const payload = {
      title: e.title,
      text_rich: e.text_rich,
      document_type: e.document_type,
      category_id: e.category_id,
      effective_at: e.effective_at ? e.effective_at + "T00:00:00Z" : null,
      changes: e.changes,
    };
    const type = state.config.document_types.find(
      (t) => t.id === e.document_type,
    );
    let required = [];
    try {
      required = Array.isArray(type?.required_fields)
        ? type.required_fields
        : JSON.parse(type?.required_fields_json || "[]");
    } catch {}
    if (submit) {
      const missing = required.filter((k) =>
        Array.isArray(payload[k]) ? !payload[k].length : !payload[k],
      );
      if (missing.length) {
        form.querySelector(".form-error").textContent =
          "Заполните обязательные поля выбранного типа: " +
          missing
            .map(
              (k) =>
                ({
                  title: "Название",
                  text_rich: "Текст проекта",
                  effective_at: "Дата вступления в силу",
                  changes: "Поправки",
                  category_id: "Категория",
                })[k] || k,
            )
            .join(", ") +
          ".";
        return;
      }
    }
    if (e.id !== "new") payload.base_version = e.version;
    const saved = await api(
      "/senate/bills" + (e.id === "new" ? "" : "/" + encodeURIComponent(e.id)),
      { method: e.id === "new" ? "POST" : "PUT", body: payload },
    );
    e.id = saved.id || e.id;
    e.version = saved.version;
    e.status = saved.status;
    if (submit) {
      await api("/senate/bills/" + encodeURIComponent(e.id) + "/submit", {
        method: "POST",
        body: {},
      });
      toast("Проект подан в Rules Committee.");
      const dest = e.id;
      state.editor = null;
      go("bill/" + dest);
    } else {
      toast("Черновик сохранён. Редакция № " + e.version);
      history.replaceState(null, "", "#editor/" + e.id);
      form.querySelector(".form-status").textContent =
        "Сохранено " +
        new Date().toLocaleTimeString("ru-RU") +
        " · редакция № " +
        e.version;
      form.querySelector(".form-error").textContent = "";
    }
  }
  async function sessions(params) {
    const all = await api("/senate/sessions"),
      day = params.get("date"),
      items = day
        ? all.filter(
            (s) => (s.starts_at || s.held_at || "").slice(0, 10) === day,
          )
        : all;
    const ready = canManageSessions()
      ? await api("/senate/bills?status=ready_plenary")
      : [];
    content.innerHTML =
      breadcrumbs("Заседания") +
      heading(
        "Заседания Сената",
        day
          ? "Повестки на " + date(day)
          : "Опубликованные повестки, голосования и стенограммы",
      ) +
      `${day ? '<p><a href="#sessions">Все даты</a></p>' : ""}${items.length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>Повестка</th><th>Начало</th><th>Окончание</th><th>Состояние</th></tr></thead><tbody>${items.map((s) => `<tr><td><a href="#session/${esc(s.id)}">${esc(s.title)}</a></td><td>${date(s.starts_at || s.held_at, true)}</td><td>${date(s.ends_at, true)}</td><td>${status(s.status)}</td></tr>`).join("")}</tbody></table></div>` : empty("Заседания не опубликованы", "Повестки появятся после публикации уполномоченным пользователем.")}${canManageSessions() ? `<details class="panel" ${params.get("new") ? "open" : ""}><summary>Создать повестку</summary><form id="sessionForm"><div class="form-grid">${field("title", "Название заседания", "", "text", 'required maxlength="250"')}${field("quorum", "Кворум, сенаторов", state.config.settings.quorum || 1, "number", 'required min="1"')}${field("starts_at", "Начало заседания", "", "datetime-local", "required")}${field("ends_at", "Окончание голосования", "", "datetime-local", "required")}</div><h3>Готовые к пленарному рассмотрению проекты</h3>${ready.length ? `<div class="check-list">${ready.map((b) => `<label><input type="checkbox" name="bill_ids" value="${esc(b.id)}"><span>${esc(b.number || "Проект")} · ${esc(b.title)}</span></label>`).join("")}</div>` : '<p class="muted">Готовых предметов пока нет.</p>'}<div class="form-error" role="alert"></div><div class="form-actions"><button type="submit">Сохранить повестку</button></div></form></details>` : ""}`;
  }
  async function sessionDetail(id) {
    const s = await api("/senate/sessions/" + encodeURIComponent(id)),
      attendance = s.attendance || {},
      open = s.status === "published" && new Date(s.ends_at) > new Date();
    let actions = button(
      "Скачать PDF",
      "download",
      `data-url="/senate/sessions/${esc(id)}/export.pdf" data-file="session-${esc(id)}.pdf" class="secondary small"`,
    );
    if (canManageSessions() && s.status === "draft")
      actions += button(
        "Опубликовать повестку",
        "publish-session",
        `data-id="${esc(id)}"`,
      );
    if (
      canManageSessions() &&
      s.status === "published" &&
      new Date(s.ends_at) <= new Date()
    )
      actions += button(
        "Завершить заседание",
        "close-session",
        `data-id="${esc(id)}"`,
      );
    const eligible =
      state.me &&
      arr(attendance.participants).some((p) => p.user_id === state.me.id);
    const mine = arr(attendance.participants).find(
        (p) => p.user_id === state.me?.id,
      ),
      absent = !!mine?.absent;
    content.innerHTML =
      breadcrumbs("Заседание") +
      heading(s.title, "Пленарная повестка Сената", actions) +
      status(s.status) +
      `<div class="session-meta"><p>Начало: <b>${date(s.starts_at, true)}</b></p><p>Голосование закрывается: <b>${date(s.ends_at, true)}</b></p><p>Кворум: <b>${attendance.present ?? 0} из ${attendance.required ?? s.quorum}</b> сенаторов. Лейтенант-Губернатор в кворум не входит.</p>${s.published_at ? "<p>Повестка опубликована: " + date(s.published_at, true) + ". Редакции предметов зафиксированы.</p>" : ""}</div>${open ? '<div class="notice">Результаты предварительные. Свой голос можно изменить до закрытия заседания. Выбор «Отсутствовал» исключает все ваши голоса на этом заседании.</div>' : ""}${absent && open ? '<div class="notice">Вы отмечены отсутствующим на всём заседании. Голос «За», «Против» или «Воздержался» восстановит присутствие. По остальным предметам потребуется проголосовать заново.</div>' : ""}${s.status === "failed" ? '<div class="error">Кворум не достигнут. Заседание не состоялось, его предметы возвращены к пленарному рассмотрению.</div>' : ""}${
        arr(s.items)
          .map((item, i) => {
            const my = arr(item.choices).find(
                (c) => c.voter_id === state.me?.id,
              ),
              t = item.tally || {};
            return `<section class="vote-item"><h2>${i + 1}. <a href="#bill/${esc(item.bill_id)}?version=${item.version}">${esc(item.title)}</a></h2><p class="small-text muted">Редакция № ${item.version} · Правило: ${esc(ruleNames[item.rule] || item.rule)}${item.threshold ? " · Требуется «За»: " + item.threshold : ""}</p>${item.result && item.result !== "pending" ? status(item.result) : ""}<div class="tally"><span>За: <b>${t.yes ?? 0}</b></span><span>Против: <b>${t.no ?? 0}</b></span><span>Воздержались: <b>${t.abstain ?? 0}</b></span><span>Отсутствовали: <b>${t.absent ?? 0}</b></span>${t.deciding_vote ? "<span>Учтён решающий голос Лейтенант-Губернатора</span>" : ""}</div>${
              eligible && open
                ? `<p class="small-text">Ваш голос: <b>${esc(my ? choiceNames[my.choice] : "Вы ещё не голосовали")}</b></p><div class="vote-actions">${Object.entries(
                    choiceNames,
                  )
                    .map(([v, n]) =>
                      button(
                        n,
                        "cast-vote",
                        `data-session="${esc(id)}" data-bill="${esc(item.bill_id)}" data-choice="${v}" class="${my?.choice === v ? "selected" : ""}" aria-pressed="${my?.choice === v}"`,
                      ),
                    )
                    .join("")}</div>`
                : ""
            }<details><summary>Поимённые голоса</summary>${arr(item.choices).length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>Участник</th><th>Голос</th></tr></thead><tbody>${item.choices.map((c) => `<tr><td>${esc(c.name || c.voter_id)}${c.is_lieutenant ? " · Лейтенант-Губернатор" : ""}</td><td>${esc(choiceNames[c.choice] || c.choice)}</td></tr>`).join("")}</tbody></table></div>` : '<p class="small-text muted">Голоса ещё не поданы.</p>'}</details></section>`;
          })
          .join("") || empty("В повестке пока нет предметов")
      }${
        isAdmin()
          ? `<details class="panel"><summary>Административное исправление голоса</summary><form id="voteCorrectionForm" data-id="${esc(id)}">${select(
              "bill_id",
              "Предмет повестки",
              arr(s.items).map((i) => ({ id: i.bill_id, name: i.title })),
            )}${field("voter_id", "Идентификатор голосующего", "", "text", "required")}${select(
              "choice",
              "Исправленный голос",
              Object.entries(choiceNames).map(([id, name]) => ({ id, name })),
            )}${textArea("reason", "Основание исправления", "", "required")}<p class="small-text muted">Исправление после закрытия заседания потребует отдельного пересмотра всех его результатов. Прежние значения сохранятся в истории.</p><div class="form-error" role="alert"></div><button type="submit">Зафиксировать исправление</button></form></details>`
          : ""
      }${
        isAdmin() && s.status === "needs_review"
          ? `<details class="panel" open><summary>Зафиксировать результаты пересмотра</summary><form id="resolutionForm" data-id="${esc(id)}">${arr(
              s.items,
            )
              .map((i) =>
                select("result:" + i.bill_id, i.title, [
                  { id: "adopted", name: "Принят" },
                  { id: "rejected", name: "Не принят" },
                  { id: "no_quorum", name: "Нет кворума" },
                ]),
              )
              .join(
                "",
              )}${textArea("reason", "Основание итогового решения", "", "required")}<label class="field"><input type="checkbox" name="committee_actions_reviewed" required> Я проверил последствия для назначений и состава комитетов.</label><div class="form-error" role="alert"></div><button type="submit">Зафиксировать итоги пересмотра</button></form></details>`
          : ""
      }<section class="panel"><h2>Стенограмма заседания</h2>${s.transcript ? `<div class="document-text"><p>${esc(s.transcript).replace(/\n/g, "<br>")}</p></div>` : '<p class="muted">Стенограмма ещё не загружена.</p>'}${canManageSessions() && new Date(s.ends_at) <= new Date() ? `<form id="transcriptForm" data-id="${esc(id)}">${textArea("text", "Текст стенограммы", s.transcript || "", "required")}<div class="form-error" role="alert"></div><button type="submit">Сохранить стенограмму</button></form>` : ""}</section>`;
  }
  async function committees() {
    const items = await api("/senate/committees");
    content.innerHTML =
      breadcrumbs("Комитеты") +
      heading(
        "Комитеты Сената",
        "Состав, предметы рассмотрения и материалы совещаний",
      ) +
      (items.length
        ? items
            .map(
              (c) =>
                `<section class="news-entry"><h2><a href="#committee/${esc(c.id)}">${esc(c.name)}</a></h2><p>${esc(c.description)}</p>${c.chair_name || c.chair_id ? '<p class="small-text">Председатель: ' + esc(c.chair_name || c.chair_id) + "</p>" : ""}</section>`,
            )
            .join("")
        : empty("Комитеты не созданы")) +
      (await proposalFormHtml());
  }
  async function committeeDetail(id) {
    const c = await api("/senate/committees/" + encodeURIComponent(id)),
      member = isFlag("committee:" + id),
      chair =
        isFlag("chair:" + id) &&
        member &&
        (isFlag("senator") || isFlag("legislator")),
      consultant = member && !isFlag("senator") && !isFlag("legislator"),
      queue = arr(c.queue);
    const agendaReviews = arr(c.reviews).filter(
      (r) => r.status === "report_ready",
    );
    const options = queue.map((b) => ({
      id: b.id || b.bill_id,
      name: (b.number || "") + " " + b.title,
    }));
    content.innerHTML =
      breadcrumbs("Комитет") +
      heading(c.name, c.description || "") +
      `<div class="split"><section><h2>Состав комитета</h2>${arr(c.members).length ? "<ul>" + c.members.map((m) => `<li>${esc(m.name || m.user_id)}${m.chair ? " · председатель" : ""}</li>`).join("") : '<p class="muted">Состав ещё не назначен.</p>'}</section><section><h2>Порядок рассмотрения</h2><p class="small-text">Консультант предоставляет отчёт. После открытого совещания председатель отдельно фиксирует результаты голосования и решение о дальнейшем маршруте предмета.</p></section></div><section class="panel"><h2>Предметы на рассмотрении</h2>${billsTable(queue)}</section>${
        consultant && queue.length
          ? `<details class="panel"><summary>Предоставить отчёт консультанта</summary><form id="reportForm" data-id="${esc(id)}">${select("bill_id", "Предмет", options)}${textArea("text", "Аналитический отчёт", "", "required")}${select(
              "recommendation",
              "Рекомендация",
              Object.entries(decisionNames).map(([id, name]) => ({ id, name })),
            )}<div class="form-error" role="alert"></div><button type="submit">Подать отчёт</button></form></details>`
          : ""
      }${chair && agendaReviews.length ? `<details class="panel"><summary>Сформировать повестку комитета</summary><form id="committeeAgendaForm" data-id="${esc(id)}">${field("title", "Название совещания", "", "text", "required")}<div class="check-list">${agendaReviews.map((r) => `<label><input type="checkbox" name="review_ids" value="${esc(r.id)}"><span>${esc(r.title || r.bill_id)} · цикл ${r.cycle}</span></label>`).join("")}</div><div class="form-error" role="alert"></div><div class="form-actions"><button type="submit">Сформировать повестку</button></div></form></details>` : ""}${
        arr(c.agendas).length
          ? `<section class="panel"><h2>Повестки комитета</h2>${c.agendas
              .map(
                (a) =>
                  `<details><summary>${esc(a.title)} · ${date(a.published_at || a.created_at, true)}</summary><ol>${arr(
                    a.items,
                  )
                    .map(
                      (i) =>
                        `<li><a href="#bill/${esc(i.bill_id)}?tab=reviews">${esc(i.title || i.bill_id)}</a> · редакция ${i.version || ""}</li>`,
                    )
                    .join("")}</ol></details>`,
              )
              .join("")}</section>`
          : ""
      }${
        chair && queue.length
          ? `<details class="panel"><summary>Зафиксировать решение председателя</summary><form id="decisionForm" data-id="${esc(id)}">${select("bill_id", "Предмет", options)}${select(
              "decision",
              "Решение",
              Object.entries(decisionNames).map(([id, name]) => ({ id, name })),
            )}${field("target_committee_id", "ID комитета назначения, только для передачи")}${textArea("amendment_text", "Новый полный текст, только для решения с поправкой")}${textArea("comment", "Основание и комментарий", "", "required")}${textArea("transcript", "Стенограмма совещания", "", "required")}<div class="form-grid">${field("yes", "Голосов «За»", 0, "number", 'min="0" required')}${field("no", "Голосов «Против»", 0, "number", 'min="0" required')}${field("abstain", "Воздержались", 0, "number", 'min="0" required')}${field("absent", "Отсутствовали", 0, "number", 'min="0" required')}</div><p class="small-text muted">Для решения с поправкой укажите новый полный текст выше. Новая редакция сохранится вместе с решением. Результаты голосования и решение председателя сохраняются отдельно.</p><div class="form-error" role="alert"></div><button type="submit">Зафиксировать решение</button></form></details>`
          : ""
      }${arr(c.reviews).length ? `<section class="panel"><h2>История рассмотрений</h2><div class="table-wrap"><table class="data-table"><thead><tr><th>Предмет</th><th>Цикл</th><th>Отчёт</th><th>Решение</th></tr></thead><tbody>${c.reviews.map((r) => `<tr><td><a href="#bill/${esc(r.bill_id)}?tab=reviews">${esc(r.title || r.bill_id)}</a></td><td>${r.cycle}</td><td>${r.report_by ? esc(r.report_by) : "Ожидается"}</td><td>${esc(decisionNames[r.decision] || r.decision || "Ожидается")}</td></tr>`).join("")}</tbody></table></div></section>` : ""}`;
  }
  async function senators() {
    const items = await api("/senate/senators");
    content.innerHTML =
      breadcrumbs("Сенаторы") +
      heading(
        "Сенаторы штата",
        "Публичные сведения и контакты представителей",
      ) +
      (items.length
        ? `<div class="people-list">${items
            .map(
              (s) =>
                `<article class="person"><h2>${esc(s.name || s.user_id || s.id)}</h2><dl>${[
                  ["capitol_office", "Офис в Капитолии"],
                  ["district_office", "Офис в дистрикте"],
                  ["phone", "Телефон"],
                  ["email", "Электронная почта"],
                ]
                  .filter(([k]) => s[k])
                  .map(
                    ([k, n]) =>
                      `<dt>${n}</dt><dd>${k === "email" ? '<a href="mailto:' + esc(s[k]) + '">' + esc(s[k]) + "</a>" : esc(s[k])}</dd>`,
                  )
                  .join(
                    "",
                  )}</dl>${!s.capitol_office && !s.phone && !s.email ? '<p class="small-text muted">Контактные сведения не заполнены.</p>' : ""}</article>`,
            )
            .join("")}</div>`
        : empty(
            "Состав Сената ещё не опубликован",
            "Профили появятся после назначения сенаторских полномочий.",
          ));
  }
  async function news(id) {
    const items = await api("/senate/news");
    if (id) {
      const n = items.find((x) => x.id === id);
      if (!n)
        throw Object.assign(Error("Публикация не найдена."), { status: 404 });
      content.innerHTML =
        breadcrumbs("Новости") +
        heading(n.title, date(n.published_at || n.created_at)) +
        `<article class="document-text">${rich(n.body || n.text_rich)}</article><a href="#news">Все новости Сената</a>`;
      return;
    }
    content.innerHTML =
      breadcrumbs("Новости") +
      heading("Новости Сената") +
      (items.length
        ? items
            .map(
              (n) =>
                `<article class="news-entry"><time>${date(n.published_at || n.created_at)}</time><h2><a href="#news/${esc(n.id)}">${esc(n.title)}</a></h2><p>${esc(stripped(n.body || n.text_rich).slice(0, 220))}</p></article>`,
            )
            .join("")
        : empty("Публикаций пока нет")) +
      (isAdmin()
        ? `<details class="panel"><summary>Опубликовать новость</summary><form id="newsForm">${field("title", "Заголовок", "", "text", 'required maxlength="250"')}${textArea("body", "Текст публикации", "", "required")}<div class="form-error" role="alert"></div><button type="submit">Опубликовать</button></form></details>`
        : "");
  }
  async function cabinet() {
    if (!signedIn()) return;
    const bills = await api("/senate/bills?mine=true"),
      p = state.me.profile || {},
      invitations = arr(state.me.invitations);
    content.innerHTML =
      breadcrumbs("Кабинет") +
      heading(
        "Мой кабинет",
        p.name || state.me.id,
        canCreate()
          ? '<a class="button" href="#editor/new">Создать проект</a>'
          : "",
      ) +
      `<section><h2 class="section-title">Мои черновики и проекты</h2>${billsTable(bills)}</section><section class="panel"><h2>Приглашения в соавторы</h2>${invitations.length ? invitations.map((i) => `<div class="notice"><p><a href="#bill/${esc(i.bill_id)}">${esc(i.title || i.bill_id)}</a></p><div class="actions">${button("Подтвердить соавторство", "coauthor-respond", `data-id="${esc(i.bill_id)}" data-accept="true"`)}${button("Отклонить", "coauthor-respond", `data-id="${esc(i.bill_id)}" data-accept="false" class="secondary"`)}</div></div>`).join("") : '<p class="muted">Нет ожидающих приглашений.</p>'}</section><section class="panel"><h2>Публичный профиль</h2><p class="small-text muted">Заполненные контактные поля сенатора отображаются в публичном разделе.</p><form id="profileForm"><div class="form-grid">${field("name", "Имя", p.name || "", "text", 'maxlength="200"')}${field("capitol_office", "Номер офиса в Капитолии", p.capitol_office || "")}${field("district_office", "Адрес офиса в дистрикте", p.district_office || "")}${field("phone", "Телефон", p.phone || "", "tel")}${field("email", "Электронная почта", p.email || "", "email")}</div><div class="form-error" role="alert"></div><div class="form-actions"><button type="submit">Сохранить профиль</button></div></form></section>`;
  }
  function flagPermissions(flag) {
    if (Array.isArray(flag.permissions)) return flag.permissions;
    try {
      return arr(JSON.parse(flag.permissions_json || "[]"));
    } catch {
      return [];
    }
  }
  function ruleOptions() {
    return [
      ["predominantly_for", "Преимущественно за"],
      ["simple_majority", "Простое большинство"],
      ["absolute_majority", "Абсолютное большинство"],
      ["two_thirds", "2/3"],
    ].map(([id, name]) => ({ id, name }));
  }
  async function admin(params) {
    if (!signedIn()) return;
    if (!isAdmin()) {
      content.innerHTML =
        heading("Администрирование") +
        empty("Недостаточно прав", "Этот раздел доступен суперадминистратору.");
      return;
    }
    const data = await api("/senate/admin"),
      tab = params.get("tab") || "settings",
      settings = data.settings || {};
    state.flagTypes = arr(data.flag_types);
    let body = "";
    if (tab === "settings") {
      const settingFields = [
        [
          "committee_review_days",
          "Срок отчёта консультанта, календарных дней",
          10,
        ],
        ["consideration_days", "Срок решения комитета, календарных дней", 10],
        ["quorum", "Кворум, присутствующих сенаторов", 1],
        ["min_notice_hours", "Минимум часов от публикации до заседания", 0],
      ];
      body = `<form id="settingsForm"><div class="form-grid">${settingFields.map(([key, n, def]) => field(key, n, settings[key] ?? def, "number", 'min="0" required')).join("")}${select("pro_tempore_rule", "Правило выборов Pro Tempore", [{ id: "", name: "Пока не настроено" }, ...ruleOptions().filter((x) => x.id !== "predominantly_for")], settings.pro_tempore_rule || "")}${field("pro_tempore_threshold", "Порог выборов, для абсолютного большинства и 2/3", settings.pro_tempore_threshold || "", "number", 'min="1"')}</div><p class="small-text muted">Срок отчёта и срок решения комитета настраиваются независимо. Изменение параметров не меняет снимки правил уже опубликованных голосований.</p><div class="form-error" role="alert"></div><div class="form-actions"><button type="submit">Сохранить настройки</button></div></form>`;
    } else if (tab === "flags") {
      body = `<h2>Назначить или отозвать флаг</h2><form id="flagForm"><div class="form-grid">${field("user_id", "Идентификатор пользователя", "", "text", "required")}${select(
        "flag",
        "Флаг",
        [
          { id: "senator", name: "Сенатор" },
          { id: "pro_tempore", name: "Pro Tempore" },
          { id: "lieutenant_governor", name: "Лейтенант-Губернатор" },
          { id: "committee:rules", name: "Участник Rules Committee" },
          { id: "chair:rules", name: "Председатель Rules Committee" },
          ...arr(data.flag_types)
            .filter((f) => f.active)
            .map((f) => ({ id: f.id, name: f.name })),
        ],
      )}${select("active", "Действие", [
        { id: "true", name: "Назначить" },
        { id: "false", name: "Отозвать" },
      ])}${field("custom_flag", "ID другого флага, при необходимости", "", "text", 'placeholder="committee:committee-id"')}</div><p class="small-text muted">Флаг комитета даёт право голосовать только вместе с сенаторским флагом. Административный доступ сам по себе не даёт права голоса.</p><div class="form-error" role="alert"></div><button type="submit">Применить</button></form><section class="panel"><h2>Группы полномочий</h2><p class="small-text muted">Группа объединяет права, которые получают все пользователи с этим флагом. Отключение группы прекращает выдачу её полномочий.</p><div class="table-wrap"><table class="data-table"><thead><tr><th>Название</th><th>Права</th><th>Состояние</th><th>Изменение</th></tr></thead><tbody>${
        arr(data.flag_types)
          .map(
            (f, i) =>
              `<tr><td>${esc(f.name)}<small>${esc(f.id)}</small></td><td>${flagPermissions(f).map(esc).join(", ") || "Нет прав"}</td><td>${f.active ? "Включена" : "Отключена"}</td><td>${button("Изменить", "flag-type-edit", `data-index="${i}" class="secondary small"`)}</td></tr>`,
          )
          .join("") ||
        '<tr><td colspan="4">Дополнительные группы пока не созданы.</td></tr>'
      }</tbody></table></div><h3>Создать или изменить группу</h3><form id="flagTypeForm"><div class="form-grid">${field("id", "ID группы, для изменения существующей")}${field("name", "Название группы", "", "text", 'required maxlength="150"')}${select(
        "active",
        "Доступность",
        [
          { id: "true", name: "Включена" },
          { id: "false", name: "Отключена" },
        ],
      )}</div>${textArea("permissions", "Права через запятую", "", 'required placeholder="senator, committee:rules"')}<p class="small-text muted">Доступные права: senator, pro_tempore, lieutenant_governor, superadmin, committee:ID_комитета и chair:ID_комитета. Ссылки на другие группы не поддерживаются.</p><div class="form-error" role="alert"></div><button type="submit">Сохранить группу</button></form></section><h2 class="panel">Назначенные полномочия</h2><div class="table-wrap"><table class="data-table"><thead><tr><th>Пользователь</th><th>Флаг</th><th>Состояние</th></tr></thead><tbody>${
        arr(data.flags)
          .map(
            (f) =>
              `<tr><td>${esc(f.user_id)}</td><td>${esc(f.flag)}</td><td>${f.active ? "Действует" : "Отозван"}</td></tr>`,
          )
          .join("") || '<tr><td colspan="3">Назначений пока нет.</td></tr>'
      }</tbody></table></div><details class="panel"><summary>Исправить публичный профиль</summary><form id="adminProfileForm">${field("user_id", "ID пользователя", "", "text", "required")}${field("name", "Имя")}${field("capitol_office", "Офис в Капитолии")}${field("district_office", "Офис в дистрикте")}${field("phone", "Телефон")}${field("email", "Электронная почта", "", "email")}${textArea("reason", "Основание исправления", "", "required")}<div class="form-error" role="alert"></div><button type="submit">Сохранить исправление</button></form></details><h2 class="panel">Зарегистрированные пользователи</h2><ul>${arr(
        data.users,
      )
        .map(
          (u) =>
            `<li>${esc(u.name || u.user_id || u.id)} <span class="muted">${esc(u.user_id || u.id)}</span></li>`,
        )
        .join("")}</ul>`;
    } else if (tab === "categories") {
      body = `<h2>Категории предметов</h2><div class="table-wrap"><table class="data-table"><thead><tr><th>Категория</th><th>Комитет</th><th>Пленарное заседание</th><th>Состояние</th></tr></thead><tbody>${arr(
        data.categories,
      )
        .map(
          (c) =>
            `<tr><td>${esc(c.name)}<small>${esc(c.id)}</small></td><td>${esc(ruleNames[c.committee_rule] || c.committee_rule)}${c.committee_threshold ? " · " + c.committee_threshold : ""}</td><td>${esc(ruleNames[c.plenary_rule] || c.plenary_rule)}${c.plenary_threshold ? " · " + c.plenary_threshold : ""}</td><td>${c.active ? "Доступна" : "Отключена"}</td></tr>`,
        )
        .join(
          "",
        )}</tbody></table></div><h2 class="panel">Создать или изменить категорию</h2><form id="categoryForm"><div class="form-grid">${field("id", "ID категории", "", "text", 'required placeholder="general"')}${field("name", "Название", "", "text", "required")}${select("committee_rule", "Правило в комитете", ruleOptions())}${field("committee_threshold", "Порог голосов в комитете", "", "number", 'min="1"')}${select("plenary_rule", "Правило на пленарном заседании", ruleOptions())}${field("plenary_threshold", "Порог пленарных голосов", "", "number", 'min="1"')}${select(
        "active",
        "Доступность",
        [
          { id: "true", name: "Включена" },
          { id: "false", name: "Отключена" },
        ],
      )}</div><div class="form-error" role="alert"></div><button type="submit">Сохранить категорию</button></form>`;
    } else if (tab === "types") {
      body = `<h2>Типы документов</h2><div class="table-wrap"><table class="data-table"><thead><tr><th>Название</th><th>ID</th><th>Состояние</th></tr></thead><tbody>${arr(
        data.document_types,
      )
        .map(
          (t) =>
            `<tr><td>${esc(t.name)}</td><td>${esc(t.id)}</td><td>${t.active ? "Доступен" : "Отключён"}</td></tr>`,
        )
        .join(
          "",
        )}</tbody></table></div><h2 class="panel">Создать или изменить тип</h2><form id="typeForm"><div class="form-grid">${field("id", "ID типа", "", "text", "required")}${field("name", "Название", "", "text", "required")}${select(
        "active",
        "Доступность",
        [
          { id: "true", name: "Включён" },
          { id: "false", name: "Отключён" },
        ],
      )}</div>${textArea("template", "Шаблон текста")}${field("required_fields", "Обязательные поля через запятую", "title,text_rich")}<div class="form-error" role="alert"></div><button type="submit">Сохранить тип документа</button></form>`;
    } else if (tab === "audit") {
      body = `<p class="small-text muted">Журнал административных действий. Записи не редактируются.</p><div class="table-wrap"><table class="data-table"><thead><tr><th>Дата</th><th>Исполнитель</th><th>Действие</th><th>Объект</th><th>Изменение</th></tr></thead><tbody>${
        arr(data.audit)
          .map(
            (e) =>
              `<tr><td>${date(e.created_at, true)}</td><td>${esc(e.user_id)}</td><td>${esc(e.action)}</td><td>${esc(e.object_id)}</td><td class="audit-value">${esc(e.after_json || "")}</td></tr>`,
          )
          .join("") || '<tr><td colspan="5">Записей пока нет.</td></tr>'
      }</tbody></table></div>`;
    }
    content.innerHTML =
      breadcrumbs("Администрирование") +
      heading("Администрирование Сената") +
      `<nav class="tabs admin-tabs" aria-label="Настройки">${[
        ["settings", "Регламент"],
        ["flags", "Полномочия"],
        ["categories", "Категории"],
        ["types", "Типы документов"],
        ["audit", "Аудит"],
      ]
        .map(
          ([v, n]) =>
            `<a href="#admin?tab=${v}" class="${v === tab ? "active" : ""}">${n}</a>`,
        )
        .join("")}</nav>${body}`;
  }
  async function about() {
    content.innerHTML =
      breadcrumbs("О Сенате") +
      heading("О Сенате штата Сан-Андреас") +
      `<article class="document-text"><p>Портал хранит проекты документов, их редакции, материалы комитетов, пленарные повестки и открытые результаты голосований.</p><h3>Путь законопроекта</h3><p>Сенатор готовит черновик и подаёт его в Rules Committee. Комитет рассматривает предмет, сохраняет отчёт и решение председателя. Готовые предметы включаются в пленарную повестку.</p><p>Принятый документ можно скачать вместе с историей рассмотрения. Принятие не изменяет автоматически статьи правовой библиотеки.</p><h3>Материалы портала</h3><p>Это портал вымышленного штата Сан-Андреас для ролевого проекта. Его документы не являются нормативными актами реальной юрисдикции.</p><p>Оформление воспроизводит сайт Сената Калифорнии. Архитектурные фотографии Капитолия используются как визуальный образец и не изображают события проекта.</p><p><a href="#bills">Найти законопроект</a> · <a href="#sessions">Посмотреть повестки</a> · <a href="/">Открыть правовую библиотеку</a></p></article>`;
  }

  async function elections() {
    const items = await api("/senate/elections");
    const canCreateElection = isFlag("lieutenant_governor");
    content.innerHTML =
      breadcrumbs("Выбор Pro Tempore") +
      heading("Выбор Pro Tempore", "Прямое открытое голосование сенаторов") +
      (items.length
        ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>Вопрос</th><th>Начало</th><th>Окончание</th><th>Состояние</th></tr></thead><tbody>${items.map((e) => `<tr><td><a href="#election/${esc(e.id)}">${esc(e.title)}</a></td><td>${date(e.starts_at, true)}</td><td>${date(e.ends_at, true)}</td><td>${status(e.status)}</td></tr>`).join("")}</tbody></table></div>`
        : empty("Выборы ещё не назначены")) +
      (canCreateElection
        ? `<details class="panel"><summary>Инициировать выбор Pro Tempore</summary><form id="electionForm"><div class="form-grid">${field("title", "Название вопроса", "Выбор Pro Tempore Сената", "text", "required")}${field("quorum", "Кворум", state.config.settings.quorum || 1, "number", 'min="1" required')}${field("starts_at", "Начало", "", "datetime-local", "required")}${field("ends_at", "Окончание", "", "datetime-local", "required")}</div>${field("parent_election_id", "ID предыдущей попытки, для повторного голосования")}<p class="small-text muted">Все сенаторы включаются в список кандидатов. До публикации требуется настроенное правило выборов в разделе администрирования.</p><div class="form-error" role="alert"></div><button type="submit">Создать голосование</button></form></details>`
        : "");
  }
  async function electionDetail(id) {
    const e = await api("/senate/elections/" + encodeURIComponent(id)),
      open = e.status === "published" && new Date(e.ends_at) > new Date(),
      mine = arr(e.choices).find((c) => c.voter_id === state.me?.id),
      candidates = arr(e.candidates),
      winner = candidates.find((c) => c.id === e.winner_id);
    content.innerHTML =
      breadcrumbs("Выбор Pro Tempore") +
      heading(
        e.title,
        "Прямое открытое голосование",
        isFlag("lieutenant_governor") && e.status === "draft"
          ? button(
              "Опубликовать голосование",
              "publish-election",
              `data-id="${esc(id)}"`,
            )
          : "",
      ) +
      status(e.status) +
      `<div class="session-meta"><p>Начало: ${date(e.starts_at, true)}</p><p>Закрытие голосования: ${date(e.ends_at, true)}</p><p>Кворум: ${e.quorum} сенаторов</p>${e.parent_election_id ? `<p><a href="#election/${esc(e.parent_election_id)}">Предыдущая попытка голосования</a></p>` : ""}</div>${e.status !== "needs_review" && e.winner_id ? `<div class="success">Избран Pro Tempore: ${esc(winner?.name || e.winner_id)}.</div>` : e.status === "closed" || e.status === "failed" ? '<div class="notice">Победитель не определён. Лейтенант-Губернатор может назначить повторное голосование с сохранением связи с этой попыткой.</div>' : ""}<section class="panel"><h2>Кандидаты</h2>${open ? '<p class="small-text">Можно выбрать одного кандидата и изменить голос до закрытия заседания.</p>' : ""}<div class="table-wrap"><table class="data-table"><thead><tr><th>Сенатор</th><th>Голосов</th><th>Ваш выбор</th></tr></thead><tbody>${candidates.map((c) => `<tr><td>${esc(c.name || c.id)}</td><td>${arr(e.tally).find((t) => t.candidate_id === c.id)?.count || 0}</td><td>${open && state.me && candidates.some((c) => c.id === state.me.id) ? button(mine?.candidate_id === c.id ? "Ваш голос" : "Голосовать", "election-vote", `data-id="${esc(id)}" data-candidate="${esc(c.id)}" class="${mine?.candidate_id === c.id ? "" : "secondary"} small" aria-pressed="${mine?.candidate_id === c.id}"`) : mine?.candidate_id === c.id ? "Ваш голос" : ""}</td></tr>`).join("")}</tbody></table></div></section><section class="panel"><h2>Поимённое голосование</h2>${arr(e.choices).length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>Сенатор</th><th>Кандидат</th></tr></thead><tbody>${e.choices.map((c) => `<tr><td>${esc(c.voter_id)}</td><td>${esc(candidates.find((x) => x.id === c.candidate_id)?.name || c.candidate_id)}</td></tr>`).join("")}</tbody></table></div>` : '<p class="muted">Голоса ещё не поданы.</p>'}</section>`;
    content.insertAdjacentHTML(
      "beforeend",
      button(
        "Скачать PDF",
        "download",
        `data-url="/senate/sessions/${esc(e.session_id)}/export.pdf" data-file="election-${esc(id)}.pdf" class="secondary small"`,
      ),
    );
    if (isAdmin() && e.status !== "draft") {
      const people = candidates.map((c) => ({
        id: c.id,
        name: c.name || c.id,
      }));
      content.insertAdjacentHTML(
        "beforeend",
        `<details class="panel"><summary>Исправить голос за кандидата</summary><form id="electionCorrectionForm" data-id="${esc(id)}">${select("voter_id", "Сенатор, чей голос исправляется", people)}${select("candidate_id", "Исправленный выбор", [{ id: "", name: "Снять ошибочно записанный голос" }, ...people])}${textArea("reason", "Основание исправления", "", "required")}<p class="small-text muted">Исправление закрытых выборов требует отдельного утверждения результата. Предыдущие полномочия сохраняются до этого решения.</p><div class="form-error" role="alert"></div><button type="submit">Сохранить исправление голоса</button></form></details>`,
      );
      if (e.status === "needs_review")
        content.insertAdjacentHTML(
          "beforeend",
          `<details class="panel" open><summary>Утвердить исправленный результат выборов</summary><form id="electionResolutionForm" data-id="${esc(id)}">${select("winner_id", "Итоговое решение", [{ id: "", name: "Победитель не определён" }, ...people])}${textArea("reason", "Основание итогового решения", "", "required")}<label class="field"><input type="checkbox" name="officeholders_reviewed" required> Я проверил последствия для полномочий Pro Tempore.</label><div class="form-error" role="alert"></div><button type="submit">Утвердить результат выборов</button></form></details>`,
        );
    }
  }
  async function proposalFormHtml() {
    if (!isFlag("pro_tempore")) return "";
    return `<details class="panel"><summary>Предложить создание или изменение комитета</summary><form id="proposalForm"><div class="form-grid">${field("name", "Название комитета", "", "text", "required")}${field("committee_id", "ID существующего комитета, для изменения состава")}${field("chair_id", "ID сенатора, назначаемого председателем", "", "text", "required")}</div>${textArea("description", "Полномочия комитета", "", "required")}${textArea("members", "ID участников, по одному на строку", "", "required")}<p class="small-text muted">Предложение создаётся как отдельный предмет. Флаги назначаются после пленарного одобрения, а не при сохранении проекта.</p><div class="form-error" role="alert"></div><button type="submit">Создать предложение</button></form></details>`;
  }

  async function download(path, file) {
    const target = new URL(API + path, location.origin);
    if (file.endsWith(".pdf"))
      target.searchParams.set(
        "time_zone",
        Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      );
    const r = await fetch(target, { headers: headers() });
    if (!r.ok) {
      let d;
      try {
        d = await r.json();
      } catch {}
      throw Error(d?.error?.message || "Не удалось скачать документ.");
    }
    const blob = await r.blob(),
      url = URL.createObjectURL(blob),
      a = document.createElement("a");
    a.href = url;
    a.download = file;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function handleAction(el) {
    const action = el.dataset.action;
    if (action === "publish-election") {
      await api(
        "/senate/elections/" + encodeURIComponent(el.dataset.id) + "/publish",
        { method: "POST", body: {} },
      );
      toast("Голосование опубликовано.");
      await render();
      return;
    }
    if (action === "election-vote") {
      await api(
        "/senate/elections/" + encodeURIComponent(el.dataset.id) + "/votes",
        { method: "POST", body: { candidate_id: el.dataset.candidate } },
      );
      toast("Голос сохранён.");
      await render();
      return;
    }
    if (action === "flag-type-edit") {
      const flag = state.flagTypes[Number(el.dataset.index)],
        form = document.querySelector("#flagTypeForm");
      form.elements.id.value = flag.id;
      form.elements.name.value = flag.name;
      form.elements.active.value = flag.active ? "true" : "false";
      form.elements.permissions.value = flagPermissions(flag).join(", ");
      form.scrollIntoView({ block: "center", behavior: "smooth" });
      form.elements.name.focus();
      return;
    }
    if (action === "retry") {
      await render();
      return;
    }
    if (action === "login") {
      await startLogin();
      return;
    }
    if (action === "logout") {
      state.token = "";
      sessionStorage.removeItem("sas-oidc-token");
      await refreshMe();
      go("home");
      return;
    }
    if (action === "calendar-prev" || action === "calendar-next") {
      state.calendar = new Date(
        state.calendar.getFullYear(),
        state.calendar.getMonth() + (action === "calendar-prev" ? -1 : 1),
        1,
      );
      document.querySelector("#homeCalendar").innerHTML = calendarHtml();
      return;
    }
    if (action === "page") {
      const r = route();
      r.params.set("page", el.dataset.page);
      go(r.path + "?" + r.params);
      return;
    }
    if (action === "editor-step") {
      editorCapture();
      state.editor.step = Number(el.dataset.step);
      drawEditor();
      return;
    }
    if (action === "use-template") {
      editorCapture();
      const type = state.config.document_types.find(
        (t) => t.id === state.editor.document_type,
      );
      if (!type?.template) return;
      if (
        stripped(state.editor.text_rich).trim() &&
        !(await confirmAction(
          "Вставить шаблон документа?",
          "Шаблон заменит текущий текст в редакторе. Сохранённые редакции проекта останутся в истории.",
        ))
      )
        return;
      state.editor.text_rich = rich(type.template);
      drawEditor();
      return;
    }
    if (action === "format") {
      document.querySelector("#richEditor").focus();
      document.execCommand(el.dataset.command, false);
      return;
    }
    if (action === "save-draft" || action === "save-submit") {
      await saveEditor(action === "save-submit");
      return;
    }
    if (action === "library-search") {
      editorCapture();
      const q = document.querySelector("#libraryQuery").value;
      document.querySelector("#libraryResults").innerHTML =
        '<p class="loading">Поиск положений…</p>';
      const d = await api("/senate/library?q=" + encodeURIComponent(q));
      state.libraryResults = [
        ...arr(d.articles),
        ...arr(d.nodes).map((x) => ({ ...x, is_node: true })),
      ];
      document.querySelector("#libraryResults").innerHTML = state.libraryResults
        .length
        ? `<div class="list-results">${state.libraryResults.map((a, i) => button(`${esc(a.number || "Раздел")} · ${esc(a.title)}<small>${esc(a.code_title || a.code_id || "")} · ID ${esc(a.id)}</small>`, "pick-article", `data-index="${i}"`)).join("")}</div>`
        : empty("Положения не найдены");
      return;
    }
    if (action === "pick-article") {
      editorCapture();
      const a = state.libraryResults[Number(el.dataset.index)];
      if (
        state.editor.changes.some(
          (ch) =>
            (ch.article_id || ch.target?.article_id || ch.target?.node_id) ===
            a.id,
        )
      ) {
        toast("Это положение уже добавлено.");
        return;
      }
      state.editor.changes.push({
        operation: "amend",
        article_id: a.is_node ? undefined : a.id,
        node_id: a.is_node ? a.id : undefined,
        title: a.title,
        code_id: a.code_id,
        base_revision_id: a.revision_id,
        target: {
          code_id: a.code_id,
          ...(a.is_node ? { node_id: a.id } : { article_id: a.id }),
          number: a.number,
        },
        original_text: a.text_rich || "",
        text_rich: a.text_rich || "",
      });
      drawEditor();
      return;
    }
    if (action === "add-change") {
      editorCapture();
      state.editor.changes.push({
        operation: "add",
        title: "Новая статья",
        text_rich: "<p></p>",
        article: {
          id: crypto.randomUUID(),
          title: "Новая статья",
          text_rich: "",
        },
      });
      drawEditor();
      return;
    }
    if (action === "remove-change") {
      editorCapture();
      state.editor.changes.splice(Number(el.dataset.index), 1);
      drawEditor();
      return;
    }
    if (action === "submit-bill") {
      if (
        !(await confirmAction(
          "Подать проект в Сенат?",
          "Проект поступит в Rules Committee. Текущая редакция и время подачи будут сохранены в истории.",
        ))
      )
        return;
      await api(
        "/senate/bills/" + encodeURIComponent(el.dataset.id) + "/submit",
        { method: "POST", body: {} },
      );
      toast("Проект подан.");
      await render();
      return;
    }
    if (action === "coauthor-respond") {
      await api(
        "/senate/bills/" +
          encodeURIComponent(el.dataset.id) +
          "/coauthors/respond",
        { method: "POST", body: { accept: el.dataset.accept === "true" } },
      );
      await refreshMe();
      toast("Ответ на приглашение сохранён.");
      await render();
      return;
    }
    if (action === "download") {
      await download(el.dataset.url, el.dataset.file);
      return;
    }
    if (action === "publish-session") {
      if (
        !(await confirmAction(
          "Опубликовать повестку?",
          "После публикации состав предметов и их редакции фиксируются. Голосование откроется до указанного времени окончания.",
        ))
      )
        return;
      await api(
        "/senate/sessions/" + encodeURIComponent(el.dataset.id) + "/publish",
        { method: "POST", body: {} },
      );
      toast("Повестка опубликована.");
      await render();
      return;
    }
    if (action === "close-session") {
      await api(
        "/senate/sessions/" + encodeURIComponent(el.dataset.id) + "/close",
        { method: "POST", body: {} },
      );
      await render();
      return;
    }
    if (action === "cast-vote") {
      if (
        el.dataset.choice === "absent" &&
        !(await confirmAction(
          "Отметить отсутствие на всём заседании?",
          "Все ваши голоса по этой повестке будут исключены из подсчёта. До закрытия заседания можно восстановить присутствие новым голосом, остальные предметы потребуется проголосовать заново.",
        ))
      )
        return;
      await api(
        "/senate/sessions/" + encodeURIComponent(el.dataset.session) + "/votes",
        {
          method: "POST",
          body: { bill_id: el.dataset.bill, choice: el.dataset.choice },
        },
      );
      toast("Голос сохранён: " + choiceNames[el.dataset.choice] + ".");
      await render();
      return;
    }
  }
  async function submitForm(form) {
    const data = Object.fromEntries(new FormData(form));
    // Controls named "id" override form.id through HTML form named properties.
    switch (form.getAttribute("id")) {
      case "electionCorrectionForm":
        await api(
          "/senate/elections/" +
            encodeURIComponent(form.dataset.id) +
            "/admin-correction",
          {
            method: "POST",
            body: { ...data, candidate_id: data.candidate_id || null },
          },
        );
        break;
      case "electionResolutionForm":
        await api(
          "/senate/elections/" +
            encodeURIComponent(form.dataset.id) +
            "/admin-resolution",
          {
            method: "POST",
            body: {
              ...data,
              winner_id: data.winner_id || null,
              officeholders_reviewed: data.officeholders_reviewed === "on",
            },
          },
        );
        await refreshMe();
        break;
      case "flagTypeForm": {
        const payload = {
          name: data.name,
          active: data.active === "true",
          permissions: data.permissions
            .split(/[,\n]+/)
            .map((v) => v.trim())
            .filter(Boolean),
        };
        if (data.id.trim()) payload.id = data.id.trim();
        await api("/senate/admin/flag-types", {
          method: "POST",
          body: payload,
        });
        state.config = await api("/senate/config");
        await refreshMe();
        break;
      }
      case "voteCorrectionForm":
        await api(
          "/senate/sessions/" +
            encodeURIComponent(form.dataset.id) +
            "/admin-correction",
          { method: "POST", body: data },
        );
        break;
      case "resolutionForm": {
        const results = {};
        for (const [k, v] of Object.entries(data))
          if (k.startsWith("result:")) results[k.slice(7)] = v;
        await api(
          "/senate/sessions/" +
            encodeURIComponent(form.dataset.id) +
            "/admin-resolution",
          {
            method: "POST",
            body: {
              reason: data.reason,
              results,
              committee_actions_reviewed:
                data.committee_actions_reviewed === "on",
            },
          },
        );
        break;
      }
      case "adminProfileForm": {
        const payload = { ...data };
        delete payload.user_id;
        await api(
          "/senate/admin/profiles/" + encodeURIComponent(data.user_id),
          { method: "PUT", body: payload },
        );
        break;
      }
      case "billCorrectionForm": {
        const payload = {
          reason: data.reason,
          title: data.title,
          base_version: Number(form.dataset.version),
        };
        if (data.status) payload.status = data.status;
        await api(
          "/senate/admin/bills/" +
            encodeURIComponent(form.dataset.id) +
            "/correct",
          { method: "POST", body: payload },
        );
        break;
      }
      case "committeeAgendaForm":
        await api(
          "/senate/committees/" +
            encodeURIComponent(form.dataset.id) +
            "/agenda",
          {
            method: "POST",
            body: {
              title: data.title,
              review_ids: new FormData(form).getAll("review_ids"),
            },
          },
        );
        break;
      case "electionForm": {
        const result = await api("/senate/elections", {
          method: "POST",
          body: {
            title: data.title,
            starts_at: new Date(data.starts_at).toISOString(),
            ends_at: new Date(data.ends_at).toISOString(),
            quorum: Number(data.quorum),
            parent_election_id: data.parent_election_id || null,
          },
        });
        go("election/" + result.id);
        return;
      }
      case "proposalForm": {
        const result = await api("/senate/committee-proposals", {
          method: "POST",
          body: {
            name: data.name,
            committee_id: data.committee_id || undefined,
            description: data.description,
            chair_id: data.chair_id,
            members: data.members
              .split(/[\n,]+/)
              .map((x) => x.trim())
              .filter(Boolean),
          },
        });
        toast("Предложение создано. Подайте его в Rules Committee.");
        go("bill/" + result.id);
        return;
      }
      case "billFilters": {
        const p = new URLSearchParams();
        for (const [k, v] of Object.entries(data)) if (v) p.set(k, v);
        go(form.dataset.target + (p.size ? "?" + p : ""));
        return;
      }
      case "coauthorForm":
        await api(
          "/senate/bills/" + encodeURIComponent(form.dataset.id) + "/coauthors",
          { method: "POST", body: data },
        );
        break;
      case "profileForm":
        await api("/senate/profile", { method: "PUT", body: data });
        await refreshMe();
        break;
      case "newsForm":
        await api("/senate/news", {
          method: "POST",
          body: {
            title: data.title,
            body: "<p>" + esc(data.body).replace(/\n/g, "</p><p>") + "</p>",
          },
        });
        break;
      case "flagForm":
        await api("/senate/admin/flags", {
          method: "POST",
          body: {
            user_id: data.user_id,
            flag: data.custom_flag || data.flag,
            active: data.active === "true",
          },
        });
        await refreshMe();
        break;
      case "settingsForm": {
        const payload = {};
        for (const [k, v] of Object.entries(data)) {
          if (k === "pro_tempore_rule") {
            if (v) payload[k] = v;
          } else if (k === "pro_tempore_threshold") {
            if (v) payload[k] = Number(v);
          } else payload[k] = Number(v);
        }
        await api("/senate/admin/settings", { method: "PUT", body: payload });
        state.config = await api("/senate/config");
        break;
      }
      case "categoryForm":
        await api("/senate/admin/categories", {
          method: "POST",
          body: {
            ...data,
            active: data.active === "true",
            committee_threshold: data.committee_threshold
              ? Number(data.committee_threshold)
              : null,
            plenary_threshold: data.plenary_threshold
              ? Number(data.plenary_threshold)
              : null,
          },
        });
        state.config = await api("/senate/config");
        break;
      case "typeForm":
        await api("/senate/admin/document-types", {
          method: "POST",
          body: {
            id: data.id,
            name: data.name,
            active: data.active === "true",
            template:
              "<p>" + esc(data.template).replace(/\n/g, "</p><p>") + "</p>",
            required_fields: data.required_fields
              .split(",")
              .map((x) => x.trim())
              .filter(Boolean),
          },
        });
        state.config = await api("/senate/config");
        break;
      case "sessionForm": {
        const payload = {
          title: data.title,
          starts_at: new Date(data.starts_at).toISOString(),
          ends_at: new Date(data.ends_at).toISOString(),
          quorum: Number(data.quorum),
          bill_ids: new FormData(form).getAll("bill_ids"),
        };
        const result = await api("/senate/sessions", {
          method: "POST",
          body: payload,
        });
        toast("Повестка сохранена.");
        go("session/" + result.id);
        return;
      }
      case "transcriptForm":
        await api(
          "/senate/sessions/" +
            encodeURIComponent(form.dataset.id) +
            "/transcript",
          { method: "PUT", body: data },
        );
        break;
      case "reportForm":
        await api(
          "/senate/committees/" +
            encodeURIComponent(form.dataset.id) +
            "/reports",
          { method: "POST", body: data },
        );
        break;
      case "decisionForm": {
        const payload = {
          bill_id: data.bill_id,
          decision: data.decision,
          comment: data.comment,
          transcript: data.transcript,
          target_committee_id: data.target_committee_id || null,
          tally: {
            yes: Number(data.yes),
            no: Number(data.no),
            abstain: Number(data.abstain),
            absent: Number(data.absent),
          },
        };
        if (data.decision.startsWith("amend_")) {
          if (!data.amendment_text.trim())
            throw Error("Укажите новый полный текст для решения с поправкой.");
          const bill = await api(
            "/senate/bills/" + encodeURIComponent(data.bill_id),
          );
          payload.base_version = bill.version;
          payload.text_rich =
            "<p>" + esc(data.amendment_text).replace(/\n/g, "</p><p>") + "</p>";
        }
        await api(
          "/senate/committees/" +
            encodeURIComponent(form.dataset.id) +
            "/decisions",
          { method: "POST", body: payload },
        );
        break;
      }
      default:
        return;
    }
    toast("Изменения сохранены.");
    await render();
  }
  document.querySelector("#accountButton").addEventListener("click", () => {
    const menu = document.querySelector("#accountMenu");
    menu.hidden = !menu.hidden;
    document
      .querySelector("#accountButton")
      .setAttribute("aria-expanded", String(!menu.hidden));
  });
  function setMobileNavigation(opened) {
    document.querySelector(".main-nav").classList.toggle("expanded", opened);
    document
      .querySelector("#menuToggle")
      .setAttribute("aria-expanded", String(opened));
  }
  document.querySelector("#menuToggle").addEventListener("click", () => {
    setMobileNavigation(
      !document.querySelector(".main-nav").classList.contains("expanded"),
    );
  });
  for (const b of document.querySelectorAll(".has-submenu>button"))
    b.addEventListener("click", () => {
      const open = b.parentElement.classList.toggle("open");
      b.setAttribute("aria-expanded", String(open));
    });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      const active = document.activeElement;
      if (document.querySelector("#accountMenu").contains(active))
        document.querySelector("#accountButton").focus();
      document.querySelector("#accountMenu").hidden = true;
      document
        .querySelector("#accountButton")
        .setAttribute("aria-expanded", "false");
      for (const el of document.querySelectorAll(".has-submenu.open")) {
        if (el.contains(active)) el.querySelector("button").focus();
        el.classList.remove("open");
        el.querySelector("button").setAttribute("aria-expanded", "false");
      }
      if (document.querySelector(".main-nav").classList.contains("expanded")) {
        setMobileNavigation(false);
        document.querySelector("#menuToggle").focus();
      }
    }
  });
  document.addEventListener("click", async (e) => {
    const el = e.target.closest("[data-action]");
    if (!el) {
      if (e.target.closest('a[href="#content"]')) {
        e.preventDefault();
        content.focus();
        window.scrollTo({ top: 0, behavior: "smooth" });
      }
      if (!e.target.closest(".account")) {
        document.querySelector("#accountMenu").hidden = true;
        document
          .querySelector("#accountButton")
          .setAttribute("aria-expanded", "false");
      }
      return;
    }
    e.preventDefault();
    if (el.disabled) return;
    el.disabled = true;
    try {
      await handleAction(el);
    } catch (err) {
      const form = el.closest("form");
      if (form?.querySelector(".form-error"))
        form.querySelector(".form-error").textContent = err.message;
      else toast(err.message, true);
    } finally {
      el.disabled = false;
    }
  });
  document.addEventListener("submit", async (e) => {
    if (e.target.closest("dialog")) return;
    e.preventDefault();
    if (e.target.getAttribute("id") === "siteSearch") {
      go(
        "bills?q=" +
          encodeURIComponent(document.querySelector("#globalSearch").value),
      );
      return;
    }
    const form = e.target;
    const submit = form.querySelector("button[type=submit]");
    if (submit) submit.disabled = true;
    const error = form.querySelector(".form-error");
    if (error) error.textContent = "";
    try {
      await submitForm(form);
    } catch (err) {
      if (error) error.textContent = err.message;
      else toast(err.message, true);
    } finally {
      if (submit) submit.disabled = false;
    }
  });
  document.addEventListener("change", async (e) => {
    if (e.target.id === "demoRole") {
      state.role = e.target.value;
      sessionStorage.setItem("senate-demo-role", state.role);
      state.editor = null;
      await refreshMe();
      await render();
    }
    if (e.target.name === "document_type" && e.target.closest("#billEditor")) {
      editorCapture();
      const type = state.config.document_types.find(
        (t) => t.id === state.editor.document_type,
      );
      if (!stripped(state.editor.text_rich).trim() && type?.template)
        state.editor.text_rich = rich(type.template);
      drawEditor();
    }
    if (e.target.id === "revisionSelect")
      go("bill/" + e.target.dataset.id + "?version=" + e.target.value);
  });
  window.addEventListener("hashchange", () => {
    if (location.hash === "#content") {
      document.querySelector("#content").focus();
      window.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }
    render();
  });
  async function bootstrap() {
    try {
      state.auth = await api("/auth/config");
      if (state.auth.mode === "demo")
        state.testChoices = await api("/test-accounts/choices");
      await finishLogin();
      await refreshMe();
      state.config = await api("/senate/config");
      await render();
    } catch (e) {
      content.innerHTML =
        heading("Портал временно недоступен") +
        `<div class="error"><p>${esc(e.message)}</p><p>Проверьте доступность сервера и повторите загрузку.</p>${button("Повторить", "reload")}</div>`;
      document
        .querySelector('[data-action="reload"]')
        ?.addEventListener("click", () => location.reload());
    }
  }
  bootstrap();
})();
