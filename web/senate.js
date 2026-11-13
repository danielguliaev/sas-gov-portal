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
    cancelled: "Отменено",
    postponed: "Перенесено",
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
    not_pass: "Окончательно отклонить",
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
  function rich(value, documentView = false) {
    const doc = new DOMParser().parseFromString(
      String(value || ""),
      "text/html",
    );
    const allowed = new Set([
      "P",
      "DIV",
      "HR",
      "H2",
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
      if (!documentView && el.tagName === "DIV") {
        const p = doc.createElement("p");
        p.append(...el.childNodes);
        el.replaceWith(p);
        continue;
      }
      if (!allowed.has(el.tagName)) {
        el.replaceWith(...el.childNodes);
        continue;
      }
      for (const a of [...el.attributes]) {
        if (
          documentView &&
          a.name === "class" &&
          /^(bill-document-header|document-date|document-legislature|document-number|document-authors|document-attributes|document-body-title|document-preamble|resolution-body|enacting-clause)$/.test(
            a.value,
          )
        )
          continue;
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
  function paragraphText(v) {
    return stripped(
      String(v || "").replace(
        /<\/(?:p|h[1-6]|li|blockquote)>|<br\s*\/?>/gi,
        "\n",
      ),
    ).trim();
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
  const canManageSessions = () => !!state.me?.capabilities?.manage_sessions;
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
      ? `<p><b>${esc(state.me.profile?.name || state.me.id)}</b></p><a href="#cabinet">Мой кабинет</a><a href="#elections">Выбор Pro Tempore</a>${isAdmin() ? '<a href="#admin">Администрирование</a>' + (state.auth?.mode === "demo" ? '<a href="/accounts">Тестовые аккаунты</a>' : "") : ""}`
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
    editorCapture();
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
        agenda: () => agendaPage(parts[1], parts[2]),
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
      const matches = [
        ...arr(state.public.sessions),
        ...arr(state.public.committee_agendas),
      ].filter(
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
          ? matches[0].committee_id
            ? "committee/" +
              encodeURIComponent(matches[0].committee_id) +
              "?agenda=" +
              encodeURIComponent(matches[0].id)
            : "session/" + encodeURIComponent(matches[0].id)
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
          arr(b.author_ids).includes(state.me.id) ||
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
        )}</select></div>${chosen !== b.version ? '<div class="notice">Вы просматриваете прежнюю редакцию. Для текущего текста выберите последнюю редакцию в списке.</div>' : ""}<article class="document-text">${rich(rev?.document_rich || b.document_rich || b.text_rich, true)}</article>`;
    } else if (tab === "changes") {
      body = arr(b.changes).length
        ? b.changes
            .map(
              (ch) =>
                `<section class="change-row"><h3>${esc({ add: "Добавление", amend: "Изменение", repeal: "Отмена" }[ch.operation] || ch.operation)} · ${esc(ch.title || ch.article?.title || ch.target?.number || ch.article_id || "Новое положение")}</h3><p class="small-text muted">${esc(articleRef({ ...ch.target, number: ch.article?.number || ch.target?.number }))}</p><div class="compare-grid"><div><h4>Исходная редакция</h4>${rich(ch.original_text || ch.base_text || ch.source_text_rich || ch.source?.text_rich || ch.original?.text_rich || "Не применимо")}</div><div><h4>Предлагаемая редакция</h4>${ch.operation === "repeal" ? "<p>Положение предлагается отменить.</p>" : rich(ch.text_rich || ch.article?.text_rich || "")}</div></div></section>`,
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
            `<tr><td>№ ${r.version}</td><td>${date(r.created_at, true)}</td><td>${esc(r.author_name || "Участник Сената")}</td><td><a href="#bill/${esc(id)}?version=${r.version}">Открыть редакцию</a></td></tr>`,
        )
        .join(
          "",
        )}</tbody></table></div><h2 class="panel">История действий</h2><ol class="history">${arr(
        b.events,
      )
        .map(
          (e) =>
            `<li><time>${date(e.created_at, true)} · ${esc(e.actor_name)}</time><b>${esc(e.title)}</b><p>${esc(e.description)}</p>${arr(
              e.links,
            )
              .map((l) => `<a href="${esc(l.href)}">${esc(l.label)}</a>`)
              .join(" · ")}</li>`,
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
      }</dd></div><div><dt>Тип документа</dt><dd>${esc(label(state.config.document_types, b.document_type))}</dd></div><div><dt>Категория</dt><dd>${esc(label(state.config.categories, b.category_id))}</dd></div><div><dt>Дата подачи</dt><dd>${b.submitted_at ? date(b.submitted_at) : "Ещё не подан"}</dd></div><div><dt>Вступление в силу</dt><dd>${esc(effectiveText(b))}</dd></div></dl><nav class="tabs" aria-label="Разделы документа">${[
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
  const effectiveOptions = [
    {
      id: "standard",
      name: "Стандартное правило: спустя 14 дней после принятия",
    },
    { id: "immediate", name: "Немедленное действие: сразу после принятия" },
    { id: "specified_date", name: "Отдельная дата применения" },
  ];
  const effectiveText = (e) =>
    e.effective_rule === "specified_date"
      ? date(e.effective_at)
      : e.effective_rule === "immediate"
        ? "Немедленно после подписания губернатором"
        : "Спустя 14 дней после подписания губернатором";
  const articleRef = (a) =>
    `${a.code_abbreviation || state.editorCatalog?.codes?.find((c) => c.id === a.code_id)?.abbreviation || ""} §${a.number || "…"}`.trim();
  const resolutionPrefixes = {
    resolution_simple: "Сенат Штата постановил, что",
    resolution_joint: "Мы, Легислатура Сан Андреаса, призываем..",
    resolution_concurrent:
      "Сенат Штата и Ассамблея Штата Сан Андреас совместно постановили, что",
  };
  const isResolution = (e) =>
    Object.hasOwn(resolutionPrefixes, e.document_type);
  const editorCacheKey = (id) =>
    `senate:editor:v2:${state.me?.id || "guest"}:${id}`;
  function cacheEditor() {
    if (!state.editor || !state.me) return;
    try {
      localStorage.setItem(
        editorCacheKey(state.editor.id),
        JSON.stringify(state.editor),
      );
      state.cacheFailed = false;
    } catch {
      state.cacheFailed = true;
    }
    const note = document.querySelector("#editorCacheStatus");
    if (note)
      note.textContent = state.cacheFailed
        ? "Не удалось сохранить на этом устройстве. Сохраните черновик на сервере."
        : "Изменения сохранены на этом устройстве";
  }
  function editorPayload(e) {
    return {
      title: e.title,
      text_rich: e.text_rich,
      document_type: e.document_type,
      category_id: e.category_id,
      author_ids: e.author_ids,
      vote_rule: e.vote_rule,
      appropriation: e.appropriation,
      fiscal_committee: e.fiscal_committee,
      local_project: e.local_project,
      resolution_text_rich: e.resolution_text_rich || "",
      number: e.number,
      introduced_at: e.introduced_at,
      revised_at: e.revised_at,
      effective_rule: e.effective_rule,
      effective_at:
        e.effective_rule === "specified_date" ? e.effective_at || null : null,
      changes: e.changes,
      ...(e.author_id && e.author_id !== state.me.id
        ? {}
        : { coauthor_ids: e.coauthor_ids }),
    };
  }
  function editorCapture() {
    if (!state.editor) return;
    const form = document.querySelector("#billEditor");
    if (!form) return;
    const e = state.editor;
    for (const name of [
      "title",
      "document_type",
      "category_id",
      "effective_at",
      "effective_rule",
      "vote_rule",
    ])
      if (form.elements[name]) e[name] = form.elements[name].value;
    if (form.querySelector("#authorChoices"))
      e.author_ids = [
        ...form.querySelectorAll('[name="author_ids"]:checked'),
      ].map((x) => x.value);
    for (const name of ["appropriation", "fiscal_committee", "local_project"])
      if (form.elements[name]) e[name] = form.elements[name].checked;
    const resolutionBox = form.querySelector("#resolutionEditor");
    if (resolutionBox) e.resolution_text_rich = rich(resolutionBox.innerHTML);
    if (form.querySelector("#coauthorChoices"))
      e.coauthor_ids = [
        ...form.querySelectorAll('[name="coauthor_ids"]:checked'),
      ].map((x) => x.value);
    const box = form.querySelector("#richEditor");
    if (box) e.text_rich = rich(box.innerHTML);
    for (const row of form.querySelectorAll("[data-change-index]")) {
      const ch = e.changes[Number(row.dataset.changeIndex)];
      ch.operation = row.querySelector("[data-operation]").value;
      ch.article ||= {};
      ch.target ||= {};
      const text = row.querySelector("[data-change-text]");
      if (text && text.value !== paragraphText(ch.article.text_rich))
        ch.article.text_rich =
          "<p>" + esc(text.value).replace(/\n/g, "</p><p>") + "</p>";
      for (const input of row.querySelectorAll("[data-article-field]"))
        ch.article[input.dataset.articleField] = input.value;
      for (const input of row.querySelectorAll("[data-target-field]"))
        ch.target[input.dataset.targetField] = input.value || null;
      if (row.querySelector("[data-category]"))
        ch.article.categories = [
          ...row.querySelectorAll("[data-category]:checked"),
        ].map((x) => x.value);
      const criminal = row.querySelector("[data-criminal-class]");
      if (criminal) ch.article.criminal_class = criminal.value || null;
      ch.article.sanctions ||= {};
      for (const input of row.querySelectorAll("[data-sanction]"))
        ch.article.sanctions[input.dataset.sanction] =
          input.value === "" ? null : Number(input.value);
      ch.article.custom_values ||= {};
      for (const input of row.querySelectorAll("[data-custom]")) {
        const key = input.dataset.custom,
          type = input.dataset.customType;
        if (type === "range") {
          const bounds = [...row.querySelectorAll("[data-custom]")].filter(
            (x) => x.dataset.custom === key,
          );
          ch.article.custom_values[key] = bounds.every((x) => x.value === "")
            ? null
            : bounds.map((x) => Number(x.value));
        } else
          ch.article.custom_values[key] = input.multiple
            ? [...input.selectedOptions].map((x) => x.value)
            : input.value === ""
              ? null
              : type === "number"
                ? Number(input.value)
                : type === "boolean"
                  ? input.value === "true"
                  : input.value;
      }
    }
    cacheEditor();
  }
  async function editor(id) {
    if (!signedIn()) return;
    const key = id || "new";
    if (key === "new" && !canCreate()) {
      content.innerHTML =
        heading("Редактор проектов") +
        empty("Право создания проекта есть только у сенаторов");
      return;
    }
    state.editorCatalog = await api("/senate/library?catalog=true");
    state.coauthorCandidates = await api("/senate/coauthor-candidates");
    if (!state.editor || state.editor.id !== key) {
      const b =
        key === "new"
          ? {}
          : await api("/senate/bills/" + encodeURIComponent(key));
      let cached;
      try {
        cached = JSON.parse(
          localStorage.getItem(editorCacheKey(key)) || "null",
        );
      } catch {}
      state.editor = {
        ...b,
        id: key,
        title: b.title || "",
        document_type: b.document_type || "senate_bill",
        category_id: b.category_id || "general",
        author_ids: b.author_ids || [state.me.id],
        vote_rule: b.vote_rule || "simple_majority",
        appropriation: !!b.appropriation,
        fiscal_committee: !!b.fiscal_committee,
        local_project: !!b.local_project,
        resolution_text_rich: b.resolution_text_rich || "",
        effective_rule: b.effective_rule || "standard",
        effective_at: b.effective_at?.slice(0, 10) || "",
        text_rich: b.text_rich || "",
        coauthor_ids: arr(b.coauthors)
          .filter((c) => c.status !== "declined")
          .map((c) => c.user_id),
        changes: arr(b.changes),
        step: 0,
      };
      if (cached?.id === key && Array.isArray(cached.changes)) {
        state.editor = {
          ...state.editor,
          ...cached,
          conflict: key !== "new" && cached.version !== b.version,
        };
        state.editor.restored = true;
      }
    }
    for (const change of state.editor.changes) {
      const alias = arr(state.editorCatalog.code_aliases).find(
        (a) => a.old_id === change.target?.code_id,
      );
      if (alias) change.target.code_id = alias.code_id;
    }
    await drawEditor();
  }
  function customFieldHtml(f, value) {
    const attrs = `data-custom="${esc(f.id)}" data-custom-type="${esc(f.field_type)}"`;
    const opts = arr(state.editorCatalog.options).filter(
      (o) => o.field_id === f.id,
    );
    if (f.field_type === "choice" || f.field_type === "boolean") {
      const choices =
        f.field_type === "boolean"
          ? [
              { id: "true", name: "Да" },
              { id: "false", name: "Нет" },
            ]
          : opts;
      return `<label class="field"><span>${esc(f.name)}</span><select ${attrs} ${f.selection_mode === "multiple" ? "multiple" : ""}><option value="">Не указано</option>${choices.map((o) => `<option value="${esc(o.id)}" ${arr(Array.isArray(value) ? value : [String(value)]).includes(o.id) ? "selected" : ""}>${esc(o.name)}</option>`).join("")}</select></label>`;
    }
    if (f.field_type === "range")
      return `<fieldset class="field"><legend>${esc(f.name)}</legend><div class="form-grid">${[0, 1].map((i) => `<label>${i ? "До" : "От"}<input type="number" step="any" ${attrs} value="${esc(value?.[i] ?? "")}"></label>`).join("")}</div></fieldset>`;
    return `<label class="field"><span>${esc(f.name)}</span>${f.field_type === "rich_text" ? `<textarea ${attrs}>${esc(stripped(value || ""))}</textarea>` : `<input ${attrs} type="${f.field_type === "number" ? "number" : "text"}" step="any" value="${esc(value ?? "")}">`}</label>`;
  }
  function changeHtml(ch, i) {
    const a = ch.article || {},
      t = ch.target || {},
      cat = state.editorCatalog;
    const isNode = !!t.node_id,
      adding = ch.operation === "add",
      repealing = ch.operation === "repeal";
    const codes = cat.codes,
      nodes = cat.all_nodes.filter((n) => n.code_id === t.code_id);
    const articles = cat.article_index.filter((n) => n.code_id === t.code_id);
    const selectOptions = (items, value) =>
      items
        .map(
          (o) =>
            `<option value="${esc(o.id)}" ${o.id === value ? "selected" : ""}>${esc(o.name || o.title)}</option>`,
        )
        .join("");
    const categories = cat.categories.filter(
      (c) => !c.code_id || c.code_id === t.code_id,
    );
    return `<section class="change-row" data-change-index="${i}"><header><h3>${esc(articleRef({ ...t, number: a.number || t.number }))} · ${esc(a.title || "Новая статья")}</h3>${button("Убрать", "remove-change", `data-index="${i}" class="text-button"`)}</header>
      <div class="form-grid"><label class="field"><span>Действие</span><select data-operation>${(adding
        ? [["add", "Добавить"]]
        : [
            ["amend", "Изменить"],
            ["repeal", "Отменить"],
          ]
      )
        .map(
          ([v, n]) =>
            `<option value="${v}" ${ch.operation === v ? "selected" : ""}>${n}</option>`,
        )
        .join("")}</select></label>
      ${
        adding
          ? `<label class="field"><span>Кодекс *</span><select data-target-field="code_id" required><option value="">Выберите кодекс</option>${selectOptions(codes, t.code_id)}</select></label><label class="field"><span>Раздел кодекса</span><select data-target-field="parent_id"><option value="">Корень кодекса</option>${selectOptions(
              nodes.map((n) => ({
                id: n.id,
                name: `${n.number} · ${n.title}`,
              })),
              t.parent_id,
            )}</select></label><label class="field"><span>Разместить после существующей статьи</span><select data-target-field="after_article_id"><option value="">Без привязки к статье</option>${selectOptions(
              articles.map((n) => ({
                id: n.id,
                name: `${articleRef(n)} · ${n.title}`,
              })),
              t.after_article_id,
            )}</select></label>`
          : ""
      }
      ${!repealing ? `<label class="field"><span>Номер статьи *</span><input data-article-field="number" value="${esc(a.number || t.number)}" required></label><label class="field full"><span>Заголовок *</span><input data-article-field="title" value="${esc(a.title)}" required></label>` : ""}</div>
      ${
        repealing
          ? `<div class="notice">Статья предлагается к отмене целиком. Её исходный текст будет включён в документ.</div>${rich(ch.source?.text_rich)}`
          : `<div class="compare-grid"><div><h4>Исходный текст</h4>${rich(ch.source?.text_rich || "Новая статья")}</div><label class="field"><span>Предлагаемый текст *</span><textarea data-change-text required>${esc(paragraphText(a.text_rich))}</textarea></label></div>
      ${
        isNode
          ? ""
          : `<details class="article-parameters" open><summary>Параметры статьи</summary><div class="form-grid"><fieldset><legend>Категории</legend><div class="check-list">${categories.map((c) => `<label><input type="checkbox" data-category value="${esc(c.id)}" ${arr(a.categories).includes(c.id) ? "checked" : ""}>${esc(c.name)}</label>`).join("")}</div></fieldset><label class="field"><span>Уголовно-правовой класс</span><select data-criminal-class>${selectOptions(
              [
                { id: "", name: "Не применяется" },
                { id: "felony", name: "Felony" },
                { id: "misdemeanor", name: "Misdemeanor" },
                { id: "infraction", name: "Infraction" },
              ],
              a.criminal_class || "",
            )}</select></label>${[
              ["prison_min_months", "Лишение свободы, минимум месяцев"],
              ["prison_max_months", "Лишение свободы, максимум месяцев"],
              ["fine_min_usd", "Штраф, минимум USD"],
              ["fine_max_usd", "Штраф, максимум USD"],
            ]
              .map(
                ([k, n]) =>
                  `<label class="field"><span>${n}</span><input type="number" min="0" step="1" data-sanction="${k}" value="${esc(a.sanctions?.[k] ?? "")}"></label>`,
              )
              .join("")}${cat.fields
              .filter((f) => arr(a.categories).includes(f.category_id))
              .map((f) => customFieldHtml(f, a.custom_values?.[f.id]))
              .join("")}</div>
      <h4>Юридические связи</h4>${
        arr(a.relations)
          .map((r, j) => {
            const other = cat.article_index.find(
              (x) =>
                x.id === (r.source_id === a.id ? r.target_id : r.source_id),
            );
            return `<div class="relation-row"><span>${esc(label(cat.relation_types, r.type_id))} · ${esc(other ? articleRef(other) + " · " + other.title : r.label || "Статья библиотеки")}${r.target_id === a.id ? " · входящая" : ""}</span>${button("Убрать связь", "remove-relation", `data-index="${i}" data-relation="${j}" class="text-button"`)}</div>`;
          })
          .join("") || '<p class="muted small-text">Юридических связей нет.</p>'
      }
      <div class="form-grid"><label class="field"><span>Тип новой связи</span><select data-relation-type>${selectOptions(cat.relation_types, "")}</select></label><label class="field"><span>Направление</span><select data-relation-direction><option value="outgoing">Из этой статьи</option><option value="incoming">В эту статью</option></select></label><label class="field full"><span>Связанная статья библиотеки</span><select data-relation-target><option value="">Выберите статью</option>${selectOptions(
        cat.article_index
          .filter((x) => x.id !== a.id)
          .map((x) => ({ id: x.id, name: `${articleRef(x)} · ${x.title}` })),
        "",
      )}</select></label></div>${button("Добавить связь", "add-relation", `data-index="${i}" class="secondary small"`)}<p class="small-text muted">Научные сноски не изменяются законопроектом.</p></details>`
      }`
      }</section>`;
  }
  async function drawEditor() {
    const e = state.editor,
      cat = state.editorCatalog;
    cacheEditor();
    let body;
    if (isResolution(e) && e.step === 1) e.step = 2;
    const picker = (name, caption, selected, excluded = []) =>
      `<fieldset class="full coauthor-picker"><legend>${caption}</legend><label class="field"><span>Найти ${name === "author_ids" ? "автора" : "соавтора"}</span><input type="search" data-person-search="${name}" placeholder="Имя сенатора"></label><div id="${name === "author_ids" ? "authorChoices" : "coauthorChoices"}" class="check-list">${state.coauthorCandidates
        .filter((u) => !excluded.includes(u.id))
        .map(
          (u) =>
            `<label data-person-list="${name}" data-person-name="${esc(u.name.toLocaleLowerCase())}"><input name="${name}" type="checkbox" value="${esc(u.id)}" ${selected.includes(u.id) ? "checked" : ""}>${esc(u.name)}</label>`,
        )
        .join("")}</div></fieldset>`;
    const toolbar = (target) =>
      `<div class="rich-toolbar" aria-label="Форматирование текста">${[
        ["bold", "Жирный"],
        ["italic", "Курсив"],
        ["insertUnorderedList", "Список"],
      ]
        .map(([cmd, n]) =>
          button(
            n,
            "format",
            `data-target="${target}" data-command="${cmd}" class="secondary small"`,
          ),
        )
        .join(
          "",
        )}${target === "richEditor" ? button("WHEREAS", "insert-clause", `data-target="${target}" data-clause="whereas" class="secondary small"`) : ""}${button("RESOLVED", "insert-clause", `data-target="${target}" data-clause="resolved" class="secondary small"`)}</div>`;
    if (e.step === 0)
      body = `<div class="form-grid">${field("title", "Рабочее название проекта *", e.title, "text", 'required maxlength="250"')}${select(
        "document_type",
        "Тип документа",
        state.config.document_types.filter(
          (t) => t.active || t.id === e.document_type,
        ),
        e.document_type,
      )}${select(
        "category_id",
        "Категория",
        state.config.categories.filter(
          (t) => t.active || t.id === e.category_id,
        ),
        e.category_id,
      )}${select("vote_rule", "Голосование", ruleOptions(), e.vote_rule)}<p class="small-text muted full">Числовые пороги абсолютного большинства и 2/3 задаёт администратор для выбранной категории.</p>
      ${
        !isResolution(e)
          ? `${select("effective_rule", "Вступление в силу", effectiveOptions, e.effective_rule)}${e.effective_rule === "specified_date" ? field("effective_at", "Дата применения *", e.effective_at, "date", "required") : ""}<div class="full document-attribute-controls">${[
              ["appropriation", "Ассигнование"],
              ["fiscal_committee", "Фискальный комитет"],
              ["local_project", "Локальный проект"],
            ]
              .map(
                ([k, n]) =>
                  `<label><input name="${k}" type="checkbox" ${e[k] ? "checked" : ""}>${n}</label>`,
              )
              .join(
                "",
              )}</div><p class="small-text muted full">Принятие акта подразумевает его подписание губернатором.</p>`
          : '<p class="notice full">Резолюция состоит из преамбулы и постановляющей части. Изменения статей кодексов для этого типа недоступны.</p>'
      }
      ${picker("author_ids", "Авторы", e.author_ids || [])}${picker("coauthor_ids", "Соавторы", e.coauthor_ids, e.author_ids || [])}<p class="small-text muted full">В списках только действующие сенаторы. Соавторы подтверждают приглашение в своём кабинете.</p>
      <div class="full"><label id="textLabel" class="field"><span>Преамбула проекта <small>необязательно</small></span></label>${toolbar("richEditor")}<div id="richEditor" class="rich-editor" contenteditable="true" role="textbox" aria-multiline="true" aria-labelledby="textLabel">${rich(e.text_rich)}</div></div>
      ${isResolution(e) ? `<div class="full"><label id="resolutionLabel" class="field"><span>Постановляющая часть резолюции</span></label>${toolbar("resolutionEditor")}<div id="resolutionEditor" class="rich-editor" contenteditable="true" role="textbox" aria-multiline="true" aria-labelledby="resolutionLabel">${rich(e.resolution_text_rich || "<p>" + resolutionPrefixes[e.document_type] + " </p>")}</div></div>` : ""}</div>`;
    else if (e.step === 1)
      body = `<p>Выберите статьи библиотеки для изменения или отмены, либо добавьте новую статью.</p><div class="filters"><label><span>Поиск в правовой библиотеке</span><input id="libraryQuery" type="search" placeholder="Номер, название или кодекс"></label>${button("Найти статью", "library-search")}</div><div id="libraryResults"></div><div class="actions">${button("Добавить новую статью", "add-change", 'class="secondary"')}</div><div id="changeList">${e.changes.map(changeHtml).join("")}</div>`;
    else {
      try {
        e.preview = await api("/senate/bills/preview", {
          method: "POST",
          body: editorPayload(e),
        });
        body = `${isResolution(e) ? "" : `<dl class="metadata"><div><dt>Вступление в силу</dt><dd>${esc(e.preview.effective_label)}</dd></div></dl>`}<article class="document-text bill-paper">${rich(e.preview.document_rich, true)}</article><div class="notice">Проверьте документ перед подачей. После подачи проект поступит в Rules Committee.</div>`;
      } catch (err) {
        body = `<div class="error"><h2>Документ ещё не готов</h2><p>${esc(err.message)}</p><p>Вернитесь к реквизитам или изменениям и заполните недостающие поля.</p></div>`;
      }
    }
    content.innerHTML =
      breadcrumbs("Редактор проекта") +
      heading(
        e.id === "new" ? "Новый проект" : "Редактирование проекта",
        "Подготовка документа для рассмотрения Сенатом",
      ) +
      (e.conflict
        ? '<div class="error">На сервере уже есть новая редакция. Ваши локальные правки восстановлены, но сохранить их поверх новой версии нельзя. Откройте актуальный документ для сравнения.</div>'
        : e.restored
          ? '<div class="notice">Восстановлены изменения, сохранённые на этом устройстве.</div>'
          : "") +
      `<div class="editor-steps">${["Реквизиты и текст", "Изменения в законах", "Проверка"].map((n, i) => button(`${i + 1}. ${n}`, "editor-step", `data-step="${i}" ${isResolution(e) && i === 1 ? "disabled" : ""} class="${e.step === i ? "active" : ""}" aria-current="${e.step === i ? "step" : "false"}"`)).join("")}</div><form id="billEditor">${body}<div class="form-error" role="alert"></div><div class="form-status" role="status" id="editorCacheStatus">Изменения сохранены на этом устройстве</div><div class="form-actions"><a class="button secondary" href="${e.id === "new" ? "#cabinet" : "#bill/" + esc(e.id)}">${e.id === "new" ? "В кабинет" : "К документу"}</a>${button("Сохранить черновик", "save-draft", 'class="secondary"')}${e.step < 2 ? button("Продолжить", "editor-step", `data-step="${isResolution(e) ? 2 : e.step + 1}"`) : button("Сохранить и подать", "save-submit")}</div></form>`;
    if (state.cacheFailed) cacheEditor();
  }
  async function saveEditor(submit = false) {
    editorCapture();
    const e = state.editor,
      form = document.querySelector("#billEditor"),
      oldKey = editorCacheKey(e.id);
    if (!e.title.trim()) {
      form.querySelector(".form-error").textContent =
        "Укажите название проекта.";
      return;
    }
    const payload = editorPayload(e);
    if (e.id !== "new") payload.base_version = e.version;
    const saved = await api(
      "/senate/bills" + (e.id === "new" ? "" : "/" + encodeURIComponent(e.id)),
      { method: e.id === "new" ? "POST" : "PUT", body: payload },
    );
    e.id = saved.id;
    e.number = saved.number || e.number;
    e.version = saved.version;
    e.status = saved.status;
    e.restored = false;
    localStorage.removeItem(oldKey);
    history.replaceState(null, "", "#editor/" + e.id);
    cacheEditor();
    if (submit) {
      await api("/senate/bills/" + encodeURIComponent(e.id) + "/submit", {
        method: "POST",
        body: {},
      });
      localStorage.removeItem(editorCacheKey(e.id));
      const dest = e.id;
      state.editor = null;
      toast("Проект подан в Rules Committee.");
      go("bill/" + dest);
    } else {
      await drawEditor();
      toast("Черновик сохранён. Редакция № " + e.version);
      document.querySelector("#billEditor .form-error").textContent = "";
      document.querySelector("#billEditor .form-status").textContent =
        "Черновик сохранён на сервере · редакция № " + e.version;
    }
  }
  function localDateInput(value) {
    if (!value) return "";
    const d = new Date(value);
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
      .toISOString()
      .slice(0, 16);
  }
  function discussionFields(rows = []) {
    return `<section class="discussion-editor"><h3>Свободные предметы</h3><p class="small-text muted">Только обсуждение, без голосования.</p><div data-discussion-list>${rows.map(discussionField).join("")}</div>${button("Добавить свободный предмет", "add-discussion", 'class="secondary"')}</section>`;
  }
  function discussionField(row = {}) {
    return `<section class="discussion-field" data-discussion><header><h4>Свободный предмет</h4>${button("Убрать", "remove-discussion", 'class="text-button"')}</header><label class="field"><span>Название предмета *</span><input data-discussion-title required value="${esc(row.title || "")}"></label><label class="field"><span>Описание вопроса</span><textarea data-discussion-description>${esc(row.description || "")}</textarea></label></section>`;
  }
  function discussionPayload(form) {
    return [...form.querySelectorAll("[data-discussion]")].map((row) => ({
      title: row.querySelector("[data-discussion-title]").value,
      description: row.querySelector("[data-discussion-description]").value,
    }));
  }
  function sessionFormHtml(choices, session = null, opened = false) {
    const selected = arr(session?.items).map((i) => i.bill_id);
    return `<details class="panel" ${opened ? "open" : ""}><summary>${session ? "Редактировать черновик повестки" : "Создать пленарную повестку"}</summary><form id="sessionForm" data-id="${esc(session?.id || "")}"><div class="form-grid">${field("title", "Название заседания", session?.title || "", "text", "required")}${field("starts_at", "Публичная дата и время заседания", localDateInput(session?.starts_at), "datetime-local", "required")}${field("ends_at", "ООС дата и время закрытия голосования", localDateInput(session?.ends_at), "datetime-local", "required")}${textArea("venue", "Место и адрес проведения *", session?.venue || "", "required maxlength=1000")}</div><p class="small-text muted">Голосование откроется при публикации. Кворум задаёт администратор: ${esc(state.config.settings.quorum)}.</p><h3>Документы и выборы</h3>${choices.length ? `<div class="check-list">${choices.map((b) => `<label><input type="checkbox" name="bill_ids" value="${esc(b.id)}" ${selected.includes(b.id) ? "checked" : ""}>${esc(b.number || "Предмет")} · ${esc(b.title)}</label>`).join("")}</div>` : '<p class="muted">Документов, готовых к голосованию, пока нет.</p>'}${discussionFields(session?.discussions)}<div class="form-error" role="alert"></div><div class="form-actions"><button type="submit">Сохранить повестку</button></div></form></details>`;
  }
  function scheduleForm(kind, row) {
    if (
      !row.can_manage ||
      row.schedule_status === "cancelled" ||
      (kind === "plenary" && !["draft", "published"].includes(row.status))
    )
      return "";
    return `<details class="panel"><summary>Перенести или отменить заседание</summary><form class="schedule-form" data-kind="${kind}" data-agenda-id="${esc(row.id)}">${select(
      "action",
      "Действие",
      [
        { id: "reschedule", name: "Перенести заседание" },
        { id: "cancel", name: "Отменить заседание" },
      ],
    )}<div class="form-grid" data-reschedule-fields>${field("starts_at", "Новая публичная дата и время", localDateInput(row.starts_at), "datetime-local", "required")}${kind === "plenary" ? field("ends_at", "Новое ООС время закрытия голосования", localDateInput(row.ends_at), "datetime-local", "required") : ""}${textArea("venue", "Место и адрес проведения", row.venue || "", "required")}</div>${textArea("reason", "Основание изменения", "", "required")}<p class="small-text muted">${kind === "plenary" ? "При переносе открытого заседания голосование начинается заново. Прежние голоса сохраняются в истории. Отмена прекращает голосование без принятия решений." : "Перенос обновляет дату заседания. Отмена возвращает предметы с отчётами к формированию новой повестки."}</p><div class="form-error" role="alert"></div><button type="submit">Сохранить изменение</button></form></details>`;
  }
  function agendaBody(row) {
    return `<div class="agenda-public-header"><strong>${date(row.starts_at, true)}</strong><strong>${esc(row.venue || "Место проведения не указано").replace(/\n/g, "<br>")}</strong></div>${row.schedule_status !== "scheduled" ? `<div class="notice">${esc(row.schedule_status === "cancelled" ? "Заседание отменено" : "Заседание перенесено")}${row.schedule_reason ? ": " + esc(row.schedule_reason) : ""}</div>` : ""}<hr><ol class="agenda-subjects">${arr(
      row.items,
    )
      .map(
        (i) =>
          `<li><a href="#${i.kind === "election" ? "election/" + esc(i.election.id) : "bill/" + esc(i.bill_id) + "?version=" + i.version}">${esc(i.title)}</a>${i.kind === "election" ? "" : `<small>Редакция № ${i.version}</small>`}</li>`,
      )
      .join("")}${arr(row.discussions)
      .map(
        (d) =>
          `<li><strong>${esc(d.title)}</strong><span class="status discussion">Обсуждение</span><p>${esc(d.description).replace(/\n/g, "<br>")}</p></li>`,
      )
      .join("")}</ol>`;
  }
  async function openAgenda(kind, id) {
    const row = await api(
      `/senate/agendas/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`,
    );
    let dialog = document.querySelector("#agendaViewer");
    if (!dialog) {
      dialog = document.createElement("dialog");
      dialog.id = "agendaViewer";
      document.body.append(dialog);
    }
    dialog.innerHTML = `<header><h2 id="agendaTitle">Повестка: ${esc(row.title)}</h2>${button("×", "close-agenda", 'aria-label="Закрыть повестку" class="text-button"')}</header><div class="agenda-paper">${agendaBody(row)}</div><div class="actions">${kind === "plenary" ? `<a class="button secondary" data-close-agenda href="#session/${esc(id)}">${arr(row.items).length ? "Материалы заседания и голосование" : "Материалы заседания"}</a>` : ""}<a href="#agenda/${kind}/${esc(id)}" data-close-agenda>Открыть отдельной страницей</a></div>`;
    dialog.setAttribute("aria-labelledby", "agendaTitle");
    dialog.showModal();
  }
  async function agendaPage(kind, id) {
    const row = await api(
      `/senate/agendas/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`,
    );
    content.innerHTML =
      breadcrumbs("Повестка") +
      heading(row.title, row.committee_name || "Пленарная повестка Сената") +
      `<article class="agenda-paper">${agendaBody(row)}</article>` +
      scheduleForm(kind, row) +
      (kind === "plenary"
        ? `<p><a href="#session/${esc(id)}">${arr(row.items).length ? "Материалы заседания и голосование" : "Материалы заседания"}</a></p>`
        : "");
  }
  async function sessions(params) {
    const all = await api("/senate/agendas"),
      committees = await api("/senate/committees");
    const from = params.get("date_from") || params.get("date") || "",
      to = params.get("date_to") || params.get("date") || "",
      committee = params.get("committee") || "";
    const upcoming = params.get("upcoming") !== "false",
      past = params.get("past") !== "false",
      now = new Date();
    const items = all.filter((s) => {
      const day = new Date(s.starts_at || s.published_at).toLocaleDateString(
        "en-CA",
      );
      return (
        (!from || day >= from) &&
        (!to || day <= to) &&
        (!committee ||
          (committee === "plenary"
            ? s.kind === "plenary"
            : s.committee_id === committee)) &&
        (new Date(s.starts_at || s.published_at) >= now ? upcoming : past)
      );
    });
    const groups = new Map();
    for (const row of items) {
      const key = new Date(
        row.starts_at || row.published_at,
      ).toLocaleDateString("en-CA");
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(row);
    }
    let form = "";
    if (canManageSessions()) {
      const ready = await api("/senate/bills?status=ready_plenary"),
        questions = (await api("/senate/elections")).filter(
          (e) => e.status === "ready_plenary",
        );
      form = sessionFormHtml(
        [
          ...ready.map((b) => ({ id: b.id, title: b.title, number: b.number })),
          ...questions.map((e) => ({
            id: e.bill_id,
            title: e.title,
            number: "Выборы про темпоре",
          })),
        ],
        null,
        params.has("new"),
      );
    }
    content.innerHTML =
      breadcrumbs("Заседания") +
      heading("Заседания Сената", "Пленарные заседания и заседания комитетов") +
      `<form id="agendaFilters" class="agenda-filters"><div class="form-grid">${field("date_from", "Начало периода", from, "date")}${field("date_to", "Конец периода", to, "date")}${select("committee", "Комитет или пленарное заседание", [{ id: "", name: "Все заседания" }, { id: "plenary", name: "Пленарные заседания" }, ...committees], committee)}</div><div class="filter-toggles"><label><input type="checkbox" name="upcoming" ${upcoming ? "checked" : ""}>Предстоящие заседания</label><label><input type="checkbox" name="past" ${past ? "checked" : ""}>Прошедшие заседания</label></div><div class="actions"><button type="submit">Найти</button><a href="#sessions" class="button secondary">Сбросить</a></div></form><p class="agenda-filter-summary">Найдено заседаний: ${items.length}${from ? " · с " + date(from) : ""}${to ? " · по " + date(to) : ""}</p>` +
      [...groups]
        .map(
          ([day, rows]) =>
            `<section class="agenda-day"><h2>${esc(new Date(day + "T12:00:00").toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long", year: "numeric" }))}</h2><div>${rows.map((row) => `<article class="session-list-card"><h3>${esc(row.committee_name)}</h3><div class="session-card-body"><p>${esc(row.chair_name)} · председатель</p><h4>${esc(row.title)}</h4><p><strong>Время:</strong> ${date(row.starts_at, true)}</p><p class="venue-address">${esc(row.venue || "Место проведения не указано")}</p><div class="actions">${row.schedule_status === "cancelled" ? status("cancelled") : row.schedule_status === "postponed" ? status("postponed") : (row.kind === "committee" || !row.voting_items) && row.status === "published" ? '<span class="status published">Повестка опубликована</span>' : status(row.status)}${button("Повестка", "open-agenda", `data-kind="${row.kind}" data-id="${esc(row.id)}"`)}<a class="button secondary" href="#${row.kind === "plenary" ? "session/" + esc(row.id) : "agenda/committee/" + esc(row.id)}">Материалы</a></div></div></article>`).join("")}</div></section>`,
        )
        .join("") +
      (items.length
        ? ""
        : empty(
            "Заседания не найдены",
            "Измените фильтры или сбросьте ограничения.",
          )) +
      form;
  }
  function electionBallot(e, open) {
    const mine = e.my_choice,
      candidates = arr(e.candidates),
      name = (id) =>
        candidates.find((c) => c.id === id)?.name || choiceNames[id] || id;
    let html = `<p class="small-text">Правило: ${esc(ruleNames[e.rule] || "Будет зафиксировано при публикации")}${e.threshold ? " · требуется голосов: " + e.threshold : ""}</p>`;
    if (e.results_hidden)
      html +=
        '<p class="small-text muted">Результаты скрыты до закрытия голосования.</p>';
    else
      html += `<p>${e.winner_id ? "Избран про темпоре: " + esc(name(e.winner_id)) : "Победитель не определён."}</p>`;
    html += `<div class="candidate-list">${candidates.map((c) => `<div class="candidate-row"><span>${esc(c.name)}${e.results_hidden ? "" : `<small>Голосов: ${arr(e.tally).find((t) => t.candidate_id === c.id)?.count || 0}</small>`}</span>${open && e.can_vote ? button(mine === c.id ? "Ваш голос" : "Голосовать", "election-vote", `data-id="${esc(e.id)}" data-candidate="${esc(c.id)}" class="${mine === c.id ? "selected" : "secondary"} small" aria-pressed="${mine === c.id}"`) : ""}</div>`).join("")}</div>`;
    if (open && e.can_vote)
      html += `<p>Ваш голос: <b>${esc(mine ? name(mine) : "Вы ещё не голосовали")}</b></p><div class="vote-actions">${["abstain", "absent"].map((v) => button(choiceNames[v], "election-vote", `data-id="${esc(e.id)}" data-candidate="${v}" class="${mine === v ? "selected" : "secondary"}"`)).join("")}</div>`;
    if (!e.results_hidden)
      html += `<details><summary>Поимённые голоса</summary><ul>${arr(e.choices)
        .map((c) => `<li>${esc(c.voter_id)}: ${esc(name(c.candidate_id))}</li>`)
        .join("")}</ul></details>`;
    if (isAdmin() && e.session_id && e.status !== "draft")
      html += `<details class="panel"><summary>Исправить голос за кандидата</summary><form id="electionCorrectionForm" data-id="${esc(e.id)}">${select("voter_id", "Сенатор", candidates)}${select("candidate_id", "Исправленный выбор", [{ id: "", name: "Снять голос" }, ...candidates])}${textArea("reason", "Основание исправления", "", "required")}<div class="form-error" role="alert"></div><button type="submit">Сохранить исправление</button></form></details>`;
    if (isAdmin() && e.status === "needs_review")
      html += `<form id="electionResolutionForm" data-id="${esc(e.id)}">${select("winner_id", "Исправленный итог", [{ id: "", name: "Победитель не определён" }, ...candidates])}${textArea("reason", "Основание итогового решения", "", "required")}<label><input type="checkbox" name="officeholders_reviewed" required> Проверены последствия для полномочий про темпоре</label><div class="form-error" role="alert"></div><button type="submit">Утвердить результат</button></form>`;
    return html;
  }
  async function sessionDetail(id) {
    const s = await api("/senate/sessions/" + encodeURIComponent(id)),
      open = s.status === "published",
      attendance = s.attendance || {};
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
    let draftForm = "";
    if (s.status === "draft" && s.can_manage) {
      const ready = await api("/senate/bills?status=ready_plenary"),
        questions = (await api("/senate/elections")).filter(
          (e) => e.status === "ready_plenary" || e.session_id === id,
        );
      const choices = [
        ...ready.map((b) => ({ id: b.id, title: b.title, number: b.number })),
        ...questions.map((e) => ({
          id: e.bill_id,
          title: e.title,
          number: "Выборы про темпоре",
        })),
      ];
      draftForm = sessionFormHtml(
        [...new Map(choices.map((c) => [c.id, c])).values()],
        s,
      );
    }
    content.innerHTML =
      breadcrumbs("Заседание") +
      heading(s.title, "Пленарная повестка Сената", actions) +
      (s.status === "published" && !arr(s.items).length
        ? '<span class="status published">Повестка опубликована</span>'
        : status(s.status)) +
      (s.schedule_status !== "scheduled"
        ? `<div class="notice">${s.schedule_status === "cancelled" ? "Заседание отменено" : "Заседание перенесено, голосование началось заново"}. ${esc(s.schedule_reason)}</div>`
        : "") +
      `<div class="session-meta"><p class="venue-address">${esc(s.venue || "Место проведения не указано")}</p><p>Дата заседания: <b>${date(s.starts_at, true)}</b></p>${s.ends_at ? `<p>ООС закрытие голосования: <b>${date(s.ends_at, true)}</b></p>` : ""}${arr(s.items).length ? `<p>Кворум: ${s.results_hidden ? `требуется ${s.quorum} сенаторов, присутствие будет опубликовано после закрытия` : `${attendance.present} из ${s.quorum} сенаторов`}. Лейтенант-губернатор в кворум не входит.</p>` : ""}</div>` +
      (open && arr(s.items).length
        ? '<div class="notice">Голосование открыто. Результаты скрыты до закрытия. Вы можете менять свой голос. Отметка «Отсутствовал» действует на все предметы заседания.</div>'
        : "") +
      (s.my_absent && open
        ? '<div class="notice">Вы отмечены отсутствующим на всём заседании. Голос по любому предмету восстановит присутствие. По остальным предметам нужно проголосовать заново.</div>'
        : "") +
      (s.status === "failed"
        ? '<div class="error">Кворум не достигнут. Заседание не состоялось. Законопроекты возвращены к пленарному рассмотрению.</div>'
        : "") +
      draftForm +
      scheduleForm("plenary", s) +
      arr(s.discussions)
        .map(
          (d) =>
            `<section class="vote-item"><h2>${esc(d.title)}</h2><p class="muted">Свободный предмет. Только обсуждение.</p><p>${esc(d.description).replace(/\n/g, "<br>")}</p></section>`,
        )
        .join("") +
      arr(s.items)
        .map((item, i) => {
          if (item.kind === "election")
            return `<section class="vote-item"><h2>${i + 1}. ${esc(item.title)}</h2>${electionBallot(item.election, open)}</section>`;
          const my = item.my_choice,
            t = item.tally || {};
          return `<section class="vote-item"><h2>${i + 1}. <a href="#bill/${esc(item.bill_id)}?version=${item.version}">${esc(item.title)}</a></h2><p class="small-text muted">Редакция № ${item.version} · ${esc(ruleNames[item.rule])}${item.threshold ? " · требуется голосов: " + item.threshold : ""}</p>${
            s.results_hidden
              ? '<p class="small-text muted">Результаты скрыты до закрытия голосования.</p>'
              : `${status(item.result)}<div class="tally">За: ${t.yes} · Против: ${t.no} · Воздержались: ${t.abstain} · Отсутствовали: ${t.absent}</div><details><summary>Поимённые голоса</summary><ul>${arr(
                  item.choices,
                )
                  .map(
                    (c) =>
                      `<li>${esc(c.name || c.voter_id)}: ${esc(choiceNames[c.choice])}</li>`,
                  )
                  .join("")}</ul></details>`
          }${
            s.can_vote && open
              ? `<p>Ваш голос: <b>${esc(choiceNames[my] || "Вы ещё не голосовали")}</b></p><div class="vote-actions">${Object.entries(
                  choiceNames,
                )
                  .map(([v, n]) =>
                    button(
                      n,
                      "cast-vote",
                      `data-session="${esc(id)}" data-bill="${esc(item.bill_id)}" data-choice="${v}" class="${my === v ? "selected" : "secondary"}" aria-pressed="${my === v}"`,
                    ),
                  )
                  .join("")}</div>`
              : ""
          }</section>`;
        })
        .join("") +
      (isAdmin() && arr(s.items).some((i) => i.kind !== "election")
        ? `<details class="panel"><summary>Административное исправление голоса по законопроекту</summary><form id="voteCorrectionForm" data-id="${esc(id)}">${select(
            "bill_id",
            "Предмет",
            arr(s.items)
              .filter((i) => i.kind !== "election")
              .map((i) => ({ id: i.bill_id, name: i.title })),
          )}${field("voter_id", "Идентификатор голосующего", "", "text", "required")}${select(
            "choice",
            "Исправленный голос",
            Object.entries(choiceNames).map(([id, name]) => ({ id, name })),
          )}${textArea("reason", "Основание исправления", "", "required")}<div class="form-error" role="alert"></div><button type="submit">Зафиксировать исправление</button></form></details>`
        : "") +
      (isAdmin() && s.status === "needs_review"
        ? `<form id="resolutionForm" data-id="${esc(id)}">${arr(s.items)
            .filter((i) => i.kind !== "election")
            .map((i) =>
              select("result:" + i.bill_id, i.title, [
                { id: "adopted", name: "Принят" },
                { id: "rejected", name: "Не принят" },
                { id: "no_quorum", name: "Нет кворума" },
              ]),
            )
            .join(
              "",
            )}${textArea("reason", "Основание итогового решения", "", "required")}<label><input type="checkbox" name="committee_actions_reviewed" required> Проверены последствия для состава комитетов</label><div class="form-error" role="alert"></div><button type="submit">Утвердить итоги</button></form>`
        : "") +
      (arr(s.previous_rounds).length
        ? `<section class="panel"><h2>Голосования до переноса</h2><p class="small-text muted">Эти голоса не учитываются в текущем раунде.</p>${s.previous_rounds.map((r) => `<details><summary>Раунд № ${r.round} · ${date(r.archived_at, true)}</summary><p>${esc(r.reason)}</p>${r.choices ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>Предмет</th><th>Сенатор</th><th>Голос</th></tr></thead><tbody>${r.choices.map((c) => `<tr><td>${esc(c.item_title)}</td><td>${esc(c.voter_name)}</td><td>${esc(c.choice_label)}</td></tr>`).join("")}</tbody></table></div>` : '<p class="muted">Голоса будут доступны после закрытия текущего голосования.</p>'}</details>`).join("")}</section>`
        : "") +
      `<section class="panel"><h2>Стенограмма заседания</h2>${s.transcript ? `<p>${esc(s.transcript).replace(/\n/g, "<br>")}</p>` : '<p class="muted">Стенограмма ещё не загружена.</p>'}${canManageSessions() && ["closed", "failed"].includes(s.status) ? `<form id="transcriptForm" data-id="${esc(id)}">${textArea("text", "Текст стенограммы", s.transcript || "", "required")}<div class="form-error" role="alert"></div><button type="submit">Сохранить стенограмму</button></form>` : ""}</section>`;
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
      }${chair ? `<details class="panel"><summary>Сформировать повестку комитета</summary><form id="committeeAgendaForm" data-id="${esc(id)}">${field("title", "Название совещания", "", "text", "required")}${field("starts_at", "Дата и время заседания", "", "datetime-local", "required")}${textArea("venue", "Место и адрес проведения *", "", "required")}<div class="check-list">${agendaReviews.map((r) => `<label><input type="checkbox" name="review_ids" value="${esc(r.id)}"><span>${esc(r.title || r.bill_id)} · цикл ${r.cycle}</span></label>`).join("")}</div><div class="form-error" role="alert"></div><div class="form-actions">${discussionFields()}<button type="submit">Сформировать повестку</button></div></form></details>` : ""}${
        arr(c.agendas).length
          ? `<section class="panel"><h2>Повестки комитета</h2>${c.agendas
              .map(
                (a) =>
                  `<details id="agenda-${esc(a.id)}" ${route().params.get("agenda") === a.id ? "open" : ""}><summary>${esc(a.title)} · ${date(a.starts_at || a.published_at, true)}</summary><ol>${arr(
                    a.items,
                  )
                    .map(
                      (i) =>
                        `<li><a href="#bill/${esc(i.bill_id)}?tab=reviews">${esc(i.title || i.bill_id)}</a> · редакция ${i.version || ""}</li>`,
                    )
                    .join(
                      "",
                    )}</ol>${button("Повестка", "open-agenda", `data-kind="committee" data-id="${esc(a.id)}"`)}<a href="#agenda/committee/${esc(a.id)}">Материалы и управление</a></details>`,
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
    content.innerHTML =
      breadcrumbs("Выборы про темпоре") +
      heading(
        "Выборы про темпоре",
        "Вопрос включается в общую повестку заседания",
      ) +
      (items.length
        ? `<ul class="history">${items.map((e) => `<li><a href="#election/${esc(e.id)}">${esc(e.title)}</a> ${status(e.status)}</li>`).join("")}</ul>`
        : empty("Вопросы о выборах ещё не внесены")) +
      (isFlag("lieutenant_governor")
        ? `<details class="panel"><summary>Инициировать выборы про темпоре</summary><form id="electionForm">${field("title", "Название вопроса", "Выборы про темпоре Сената", "text", "required")}${select("parent_election_id", "Предыдущая попытка, если это повторное голосование", [{ id: "", name: "Новый вопрос" }, ...items.filter((e) => ["closed", "failed"].includes(e.status) && !e.winner_id).map((e) => ({ id: e.id, name: e.title }))])}<p class="small-text muted">После создания вопрос будет доступен при формировании общей повестки. Кандидаты фиксируются при публикации.</p><div class="form-error" role="alert"></div><button type="submit">Создать предмет повестки</button></form></details>`
        : "");
  }
  async function electionDetail(id) {
    const e = await api("/senate/elections/" + encodeURIComponent(id));
    if (e.session_id) {
      go("session/" + e.session_id);
      return;
    }
    content.innerHTML =
      breadcrumbs("Выборы про темпоре") +
      heading(e.title, "Предмет готов к включению в повестку") +
      `<div class="notice">Выборы будут проводиться вместе с другими предметами общей повестки. Голосование откроется после её публикации.</div>${canManageSessions() ? '<a class="button" href="#sessions?new=1">Сформировать повестку</a>' : ""}`;
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
    if (action === "add-discussion") {
      el.closest("form")
        .querySelector("[data-discussion-list]")
        .insertAdjacentHTML("beforeend", discussionField());
      return;
    }
    if (action === "remove-discussion") {
      el.closest("[data-discussion]").remove();
      return;
    }
    if (action === "open-agenda") {
      await openAgenda(el.dataset.kind, el.dataset.id);
      return;
    }
    if (action === "close-agenda") {
      el.closest("dialog").close();
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
      await drawEditor();
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
      await drawEditor();
      return;
    }
    if (action === "insert-clause") {
      const box = document.querySelector("#" + el.dataset.target),
        paragraph = document.createElement("p");
      paragraph.textContent =
        el.dataset.clause === "whereas"
          ? "ПРИНИМАЯ ВО ВНИМАНИЕ, что\u00a0"
          : "РЕШЕНО,\u00a0";
      box.append(paragraph);
      box.focus();
      const range = document.createRange();
      range.selectNodeContents(paragraph);
      range.collapse(false);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      editorCapture();
      return;
    }
    if (action === "format") {
      document.querySelector("#" + (el.dataset.target || "richEditor")).focus();
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
        ? `<div class="list-results">${state.libraryResults.map((a, i) => button(`${esc(articleRef(a))} · ${esc(a.title)}<small>${esc(a.code_name || "")}</small>`, "pick-article", `data-index="${i}"`)).join("")}</div>`
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
        source: a,
        target: {
          code_id: a.code_id,
          code_name: a.code_name,
          code_abbreviation: a.code_abbreviation,
          ...(a.is_node
            ? { node_id: a.id }
            : { article_id: a.id, revision_id: a.revision_id }),
          number: a.number,
        },
        article: {
          id: a.id,
          number: a.number,
          title: a.title,
          text_rich: a.text_rich || "",
          categories: a.categories || [],
          criminal_class: a.criminal_class,
          custom_values: a.custom_values || {},
          sanctions: a.sanctions || {},
          relations: a.relations || [],
        },
      });
      await drawEditor();
      return;
    }
    if (action === "add-change") {
      editorCapture();
      state.editor.changes.push({
        operation: "add",
        target: { code_id: "", parent_id: null },
        article: {
          id: crypto.randomUUID(),
          number: "",
          title: "",
          text_rich: "",
          categories: [],
          custom_values: {},
          sanctions: {},
          relations: [],
        },
      });
      await drawEditor();
      return;
    }
    if (action === "add-relation" || action === "remove-relation") {
      editorCapture();
      const index = Number(el.dataset.index),
        ch = state.editor.changes[index];
      ch.article.relations ||= [];
      if (action === "remove-relation")
        ch.article.relations.splice(Number(el.dataset.relation), 1);
      else {
        const row = el.closest("[data-change-index]"),
          other = row.querySelector("[data-relation-target]").value;
        if (!other) throw Error("Выберите связанную статью библиотеки.");
        const incoming =
          row.querySelector("[data-relation-direction]").value === "incoming";
        ch.article.relations.push({
          type_id: row.querySelector("[data-relation-type]").value,
          source_id: incoming ? other : ch.article.id,
          target_id: incoming ? ch.article.id : other,
        });
      }
      await drawEditor();
      return;
    }
    if (action === "remove-change") {
      editorCapture();
      state.editor.changes.splice(Number(el.dataset.index), 1);
      await drawEditor();
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
    if (form.classList.contains("schedule-form")) {
      const data = Object.fromEntries(new FormData(form));
      if (
        data.action === "cancel" &&
        !(await confirmAction(
          "Отменить заседание?",
          "Голосование прекратится без принятия решений. Записи останутся в истории.",
        ))
      )
        return;
      const payload = {
        action: data.action,
        reason: data.reason,
        venue: data.venue,
      };
      if (data.action === "reschedule") {
        payload.starts_at = new Date(data.starts_at).toISOString();
        if (data.ends_at)
          payload.ends_at = new Date(data.ends_at).toISOString();
      }
      await api(
        `/senate/agendas/${form.dataset.kind}/${form.dataset.agendaId}/schedule`,
        { method: "POST", body: payload },
      );
      toast("Изменение заседания сохранено.");
      await refreshMe();
      await render();
      return;
    }
    if (form.getAttribute("id") === "agendaFilters") {
      const d = Object.fromEntries(new FormData(form)),
        p = new URLSearchParams();
      for (const k of ["date_from", "date_to", "committee"])
        if (d[k]) p.set(k, d[k]);
      if (d.date_from && d.date_to && d.date_from > d.date_to)
        throw Error("Начало периода не может быть позже окончания.");
      p.set("upcoming", form.elements.upcoming.checked);
      p.set("past", form.elements.past.checked);
      go("sessions?" + p);
      return;
    }

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
              starts_at: new Date(data.starts_at).toISOString(),
              venue: data.venue,
              discussions: discussionPayload(form),
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
          venue: data.venue,
          discussions: discussionPayload(form),
          starts_at: new Date(data.starts_at).toISOString(),
          ends_at: new Date(data.ends_at).toISOString(),
          bill_ids: new FormData(form).getAll("bill_ids"),
        };
        const result = await api(
          "/senate/sessions" +
            (form.dataset.id ? "/" + encodeURIComponent(form.dataset.id) : ""),
          {
            method: form.dataset.id ? "PUT" : "POST",
            body: payload,
          },
        );
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
    if (e.target.closest("[data-close-agenda]"))
      document.querySelector("#agendaViewer")?.close();
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
    if (e.target.closest("#confirmation")) return;
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
    if (e.target.name === "action" && e.target.closest(".schedule-form")) {
      for (const input of e.target
        .closest("form")
        .querySelectorAll(
          "[data-reschedule-fields] input,[data-reschedule-fields] textarea",
        ))
        input.required = e.target.value === "reschedule";
      e.target
        .closest("form")
        .querySelector("[data-reschedule-fields]").hidden =
        e.target.value === "cancel";
    }

    if (e.target.id === "demoRole") {
      editorCapture();
      state.role = e.target.value;
      sessionStorage.setItem("senate-demo-role", state.role);
      state.editor = null;
      await refreshMe();
      await render();
    }
    if (e.target.closest("#billEditor")) {
      const previousType = state.editor.document_type;
      editorCapture();
      if (e.target.name === "document_type") {
        const current = state.editor;
        if (isResolution(current)) {
          current.lawChanges ||= current.changes;
          current.changes = [];
          const prefix = resolutionPrefixes[previousType];
          if (
            !current.resolution_text_rich ||
            (prefix && stripped(current.resolution_text_rich).trim() === prefix)
          )
            current.resolution_text_rich =
              "<p>" + resolutionPrefixes[current.document_type] + " </p>";
          current.effective_rule = "standard";
        } else if (current.lawChanges) {
          current.changes = current.lawChanges;
          delete current.lawChanges;
        }
        await drawEditor();
        return;
      }
      if (e.target.name === "author_ids") {
        state.editor.coauthor_ids = state.editor.coauthor_ids.filter(
          (id) => !state.editor.author_ids.includes(id),
        );
        await drawEditor();
        return;
      }
      if (
        e.target.name === "effective_rule" ||
        e.target.hasAttribute("data-operation") ||
        e.target.hasAttribute("data-category") ||
        e.target.hasAttribute("data-target-field")
      ) {
        const row = e.target.closest("[data-change-index]"),
          ch = row
            ? state.editor.changes[Number(row.dataset.changeIndex)]
            : null;
        if (ch && e.target.dataset.targetField === "code_id") {
          ch.target.parent_id = null;
          ch.target.after_article_id = null;
          ch.article.categories = [];
          ch.article.custom_values = {};
        }
        if (ch && e.target.dataset.targetField === "parent_id")
          ch.target.after_article_id = null;
        await drawEditor();
      }
    }
    if (e.target.id === "revisionSelect")
      go("bill/" + e.target.dataset.id + "?version=" + e.target.value);
  });
  document.addEventListener("input", (event) => {
    if (event.target.dataset.personSearch) {
      const q = event.target.value.toLocaleLowerCase();
      for (const row of document.querySelectorAll(
        `[data-person-list="${event.target.dataset.personSearch}"]`,
      ))
        row.hidden = !row.dataset.personName.includes(q);
    }
    if (event.target.closest("#billEditor")) editorCapture();
  });
  window.addEventListener("pagehide", editorCapture);
  window.addEventListener("beforeunload", editorCapture);
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
