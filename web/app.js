/* Client for the San Andreas legal library. Reading content is not refreshed automatically. */
"use strict";
const main = document.getElementById("main");
const sidebar = document.getElementById("sidebar");
const layout = document.getElementById("layout");
const nav = document.getElementById("globalNav");
const otherResourcesNav = document.getElementById("otherResourcesNav");
const codeNav = document.getElementById("codeNav");
const roleSelect = document.getElementById("roleSelect");
const dialog = document.getElementById("editorDialog");
const editorForm = document.getElementById("articleForm");
const toastElement = document.getElementById("toast");
const role = sessionStorage.getItem("sas-demo-role") || "";
let authMode="demo";
let oidcToken=sessionStorage.getItem("sas-oidc-token")||"";
let oidcConfig=null;
let authButton=null;
const CATEGORY_NAMES = {accusation:"Обвинение",punishment_measure:"Мера наказания",
  aggravating:"Отягчающие обстоятельства",mitigating:"Смягчающие обстоятельства",
  auxiliary:"Вспомогательная мера",definition:"Определение",
  "pen-i-unclassified-accusation":"Обвинение — требуется указать уголовно-правовой класс",
  "pen-i-auxiliary-norm":"Вспомогательная норма"};
roleSelect.value = role;
let currentUser = null;
let favoriteIds = new Set();
let allCodes = [];
let allPortals = [];
let treeData = null;
let reading = null;
let loadObserver = null;
let activeObserver = null;
let heartbeatTimer = null;
let editorState = null;
let editorDirty = false;
let lastNavSerial = 0;
let toastTimer = null;

function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
}
function escaped(value) {
    return String(value ?? "").replace(/[&<>"']/g, function(x) {
        return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[x];
    });
}
function text(value) { return value === null || value === undefined || value === "" ? "Не указано" : String(value); }
function criminalClassLabel(value) {
    const labels={felony:"Тяжкое преступление",misdemeanor:"Проступок",
        infraction:"Нарушение"};
    return labels[String(value||"").toLowerCase()] || text(value);
}
function displayLegalTitle(value) {
    // Presentation-only cleanup for the exact sample labels shipped with the local seed.
    // Persisted legal text and revision history are never rewritten.
    const title = String(value ?? "");
    return title.replace(/\s*\(демо\)$/iu, "")
                .replace(/^Демонстрационная норма$/iu, "Пример статьи");
}
function friendlyDate(s) {
    if (!s) return "Не указано";
    const d = new Date(s);
    return Number.isNaN(d.valueOf()) ? s : d.toLocaleDateString("ru-RU", {day:"numeric",month:"long",year:"numeric"});
}
function notify(message) {
    clearTimeout(toastTimer);
    toastElement.textContent = message;
    toastElement.classList.add("visible");
    toastTimer = setTimeout(() => toastElement.classList.remove("visible"), 3500);
}
async function api(path, options) {
    const opts = Object.assign({method:"GET"}, options || {});
    const headers = Object.assign({"Accept":"application/json"}, opts.headers || {});
    if (authMode==="demo" && role) headers["X-Demo-User"] = role;
    if (authMode==="oidc" && oidcToken) headers["Authorization"]="Bearer "+oidcToken;
    if (opts.body && typeof opts.body !== "string") {
        headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(opts.body);
    }
    opts.headers = headers;
    let response;
    try { response = await fetch("/api/v1" + path, opts); }
    catch (_) { throw new Error("Сеть недоступна. Проверьте подключение."); }
    let result;
    try { result = await response.json(); }
    catch (_) { throw new Error("Ответ сервера не распознан."); }
    if (!response.ok) {
        if(response.status===401 && authMode==="oidc" && oidcToken){
            oidcToken="";
            sessionStorage.removeItem("sas-oidc-token");
            if(authButton)authButton.textContent="Войти";
        }
        const message = result.error && result.error.message || "Ошибка запроса";
        const error = new Error(message);
        error.code = result.error && result.error.code;
        error.details = result.error && result.error.details;
        throw error;
    }
    return result;
}
function isLawyer() { return currentUser && currentUser.roles.some(x => ["lawyer","superadmin"].includes(x)); }
function isAdmin() { return currentUser && currentUser.roles.includes("superadmin"); }
function hasRole(roleName) { return currentUser && currentUser.roles.includes(roleName); }
function breadcrumb(items) {
    const node = el("div", "breadcrumbs");
    items.forEach((item, index) => {
        if (index) node.append(el("span", "", "/"));
        if (item.href) {
            const a = el("a", "", item.label);
            a.href = item.href;
            a.dataset.link = "";
            node.append(a);
        } else node.append(el("span", "", item.label));
    });
    return node;
}
function pageHeading(kicker, title, description) {
    main.append(el("div", "eyebrow", kicker), el("h1", "", title));
    if (description) main.append(el("p", "lede", description));
    main.append(el("div", "gold-rule"));
}
function clearReading() {
    if (loadObserver) { loadObserver.disconnect(); loadObserver = null; }
    if (activeObserver) { activeObserver.disconnect(); activeObserver = null; }
    reading = null;
}
history.scrollRestoration = "manual";
function storePosition() {
    // Never overwrite the current history entry with the previous route's scroll.
    if (!reading || reading.path !== location.pathname || reading.restoring) return;
    const position=window.scrollY;
    sessionStorage.setItem("sas-scroll:" + location.pathname, String(position));
    history.replaceState(Object.assign({}, history.state || {},
        {scrollY:position,hasReadingPosition:true}), "", location.href);
}
let scrollSavePending = false;
window.addEventListener("scroll", () => {
    if (scrollSavePending) return;
    scrollSavePending = true;
    requestAnimationFrame(() => { scrollSavePending = false; storePosition(); });
}, {passive:true});
function closeHeaderDropdowns() {
    document.querySelectorAll(".header-dropdown[open]").forEach(menu => { menu.open = false; });
}
function closeNavigation() {
    layout.classList.remove("menu-open");
    document.getElementById("menuToggle").setAttribute("aria-expanded", "false");
    closeHeaderDropdowns();
}
document.querySelectorAll(".header-dropdown").forEach(dropdown => {
    dropdown.addEventListener("toggle", () => {
        if (dropdown.open) document.querySelectorAll(".header-dropdown").forEach(other => {
            if (other !== dropdown) other.open = false;
        });
    });
});
document.addEventListener("click", event => {
    if (!event.target.closest(".header-dropdown")) closeHeaderDropdowns();
});
// Keep the mobile code drawer directly below the sticky two-row header.
function updateMobileNavigationOffset() {
    if (window.innerWidth > 700) return;
    const bottom = Math.max(0, Math.ceil(document.querySelector(".site-header").getBoundingClientRect().bottom));
    document.documentElement.style.setProperty("--mobile-navigation-top", bottom + "px");
}
window.addEventListener("scroll", updateMobileNavigationOffset, {passive:true});
window.addEventListener("resize", updateMobileNavigationOffset);
updateMobileNavigationOffset();
const sidebarToggle=document.getElementById("sidebarToggle");
function setSidebarCollapsed(collapsed) {
    layout.classList.toggle("sidebar-collapsed",Boolean(collapsed));
    sidebarToggle.setAttribute("aria-expanded",String(!collapsed));
    sidebarToggle.setAttribute("aria-label",
        collapsed ? "Развернуть боковую панель" : "Свернуть боковую панель");
    sidebarToggle.title=collapsed ? "Развернуть боковую панель" : "Свернуть боковую панель";
    sidebarToggle.textContent=collapsed ? "❯" : "❮";
    sessionStorage.setItem("sas-sidebar-collapsed",collapsed ? "1" : "0");
}
setSidebarCollapsed(sessionStorage.getItem("sas-sidebar-collapsed")==="1");
sidebarToggle.onclick=()=>setSidebarCollapsed(!layout.classList.contains("sidebar-collapsed"));
function go(path, push) {
    const resolved = new URL(path, location.origin);
    if (push !== false) {
        storePosition();
        history.pushState({scrollY:0}, "", resolved.pathname + resolved.search);
    }
    closeNavigation();
    renderRoute(resolved.pathname, {restore:push === false}).catch(err => showError(err));
}
document.addEventListener("click", event => {
    const link = event.target.closest("a[data-link]");
    if (!link || event.defaultPrevented || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    go(link.getAttribute("href"));
});
window.addEventListener("popstate", event => {
    renderRoute(location.pathname, {restore:true, scrollY:event.state && event.state.scrollY})
        .catch(err => showError(err));
});
document.getElementById("menuToggle").addEventListener("click", () => {
    layout.classList.toggle("menu-open");
    document.getElementById("menuToggle").setAttribute("aria-expanded",
        String(layout.classList.contains("menu-open")));
});
document.getElementById("navBackdrop").addEventListener("click", closeNavigation);
document.addEventListener("keydown", event => {
    const openHeaderDropdown = document.querySelector(".header-dropdown[open]");
    if (event.key === "Escape" && openHeaderDropdown) {
        openHeaderDropdown.open = false;
        openHeaderDropdown.querySelector("summary").focus();
        return;
    }
    if (event.key === "Escape" && layout.classList.contains("menu-open")) {
        closeNavigation();
        document.getElementById("menuToggle").focus();
        return;
    }
    const focused = document.activeElement;
    const writing = focused && (["INPUT","TEXTAREA","SELECT"].includes(focused.tagName) ||
        focused.isContentEditable);
    if (!writing && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        if (location.pathname === "/search") document.getElementById("searchQuery")?.focus();
        else go("/search");
    }
});
roleSelect.addEventListener("change", () => {
    if (editorState) { notify("Завершите редактирование перед сменой роли."); roleSelect.value = role; return; }
    sessionStorage.setItem("sas-demo-role", roleSelect.value);
    location.reload();
});
document.getElementById("notifyButton").addEventListener("click", () => go("/notifications"));
document.getElementById("fontScale").addEventListener("input", e => {
    setFont(e.target.value);
});
document.getElementById("fontScale").addEventListener("change", async e => {
    if (!currentUser) return;
    try { await api("/me/preferences", {method:"PUT",body:{font_scale:Number(e.target.value)}}); }
    catch (err) { notify(err.message); }
});
function setFont(scale) {
    document.documentElement.style.setProperty("--article-scale", (Number(scale) / 100).toString());
    document.getElementById("fontScale").value = scale;
    document.getElementById("fontOutput").textContent = scale + "%";
}
function showError(err) {
    clearReading();
    main.replaceChildren();
    pageHeading("Ошибка", "Не удалось открыть ресурс", err.message || "Ошибка загрузки");
    if (err.code === "AUTH_REQUIRED") {
        const p = el("p", "notice", authMode==="oidc" ?
            "Для чтения полного текста войдите через внешний сервис авторизации." :
            "Для чтения полного текста требуется вход. Выберите роль в верхней панели.");
        main.append(p);
    }
    const back = el("button", "button", "На главную");
    back.onclick = () => go("/");
    main.append(back);
}
function navItem(label, path, symbol) {
    const a = el("a", "nav-item", "");
    a.href = path; a.dataset.link = "";
    const i = el("span", "nav-icon", symbol);
    a.append(i, el("span", "", label));
    if (location.pathname === path) a.classList.add("active");
    return a;
}
function refreshGlobalNav() {
    nav.replaceChildren();
    nav.append(
        navItem("Обзор", "/", "⌂"),
        navItem("Каталог законов", "/laws", "§"),
        navItem("Нормативные акты", "/acts", "▣"),
        navItem("Поиск законодательства", "/search", "⌕"),
        navItem("Судебные прецеденты", "/precedents", "◈")
    );
    if (isLawyer()) nav.append(navItem("Моё избранное", "/favorites", "☆"));
    if (isAdmin()) { nav.append(navItem("Управление библиотекой", "/admin", "⚙"));
        nav.append(navItem("Редакционная проверка", "/reviews", "✓")); }
    otherResourcesNav.replaceChildren();
    const otherPortals = allPortals.length ? allPortals.filter(p => p.id !== "library") : [
        {name:"Правительство штата"}, {name:"Судебная система"},
        {name:"Законодательный орган"}, {name:"Город Лос-Сантос"},
        {name:"Секретарь штата"}
    ];
    otherPortals.forEach(p => {
        otherResourcesNav.append(el("div", "nav-static-resource", p.name));
    });
    document.querySelectorAll(".header-nav a[data-link]").forEach(link => {
        if (link.getAttribute("href") === location.pathname) link.setAttribute("aria-current", "page");
        else link.removeAttribute("aria-current");
    });
}
async function loadContext() {
    currentUser = await api("/me");
    const [codes, portals] = await Promise.all([api("/codes"), api("/portals")]);
    allCodes = codes; allPortals = portals;
    refreshGlobalNav();
    const prefs = await api("/me/preferences");
    setFont(prefs.font_scale);
    if (isLawyer()) {
        const saved = await api("/me/favorites");
        favoriteIds = new Set(saved.map(article => article.id));
    }
}
function homeSectionHeading(kicker, title, linkLabel, href) {
    const head = el("div", "home-section-title");
    const text = el("div");
    text.append(el("div", "eyebrow", kicker), el("h2", "", title));
    head.append(text);
    if (href) {
        const a = el("a", "section-link", linkLabel || "Все материалы");
        a.href = href; a.dataset.link = "";
        head.append(a);
    }
    return head;
}
function makeJurisdictionCard(id, title, description, glyph, count) {
    const card = el("a", "jurisdiction-card");
    card.href = "/jurisdictions/" + id; card.dataset.link = "";
    const top = el("div", "jurisdiction-card-top");
    top.append(el("span", "jurisdiction-glyph", glyph));
    top.append(el("span", "jurisdiction-count",
        count === null ? "Данные после входа" : count + " " + pluralCode(count)));
    card.append(top, el("h3", "", title), el("p", "", description),
                el("span", "card-forward", "Открыть раздел →"));
    return card;
}
function pluralCode(count) {
    const n = Math.abs(Number(count)) % 100;
    const last = n % 10;
    return n >= 11 && n <= 14 ? "кодексов" : last === 1 ? "кодекс" :
        last >= 2 && last <= 4 ? "кодекса" : "кодексов";
}
function makeLawCodeCard(code) {
    const card = el("a", "law-code-card");
    card.href = "/codes/" + code.id; card.dataset.link = "";
    const top = el("div", "code-card-top");
    top.append(el("span", "code-acronym", code.abbreviation || "КОДЕКС"));

    const meta = el("div", "code-card-meta");
    meta.append(el("span", "", (code.section_count ?? 0) + " разделов"),
                el("span", "", (code.article_count ?? 0) + " статей"));
    card.append(top, el("h3", "", code.name), meta,
                el("span", "card-forward", "Перейти к содержанию →"));
    return card;
}
function makePortalLink(portal) {
    const card = el("div", "portal-card static-portal-card");
    card.append(el("strong", "", portal.name),
        el("p", "", "Самостоятельный ресурс, ссылка будет добавлена позднее."));
    return card;
}
async function home(guest = false) {
    const overview = guest ? null : await api("/library/overview");
    const hero = el("section", "law-hero");
    const inner = el("div", "law-hero-copy");
    inner.append(el("div", "hero-eyebrow", "Единая правовая информационная сеть"),
                 el("h1", "", "Правовая библиотека штата Сан Андреас"),
                 el("p", "", "Кодексы, статьи и официальные публикации в одном пространстве. Изучайте структуру законов, находите нормы и отслеживайте редакции."));
    const searchForm = el("form", "hero-search");
    searchForm.setAttribute("role", "search");
    const input = el("input");
    input.type = "search";
    input.name = "q"; input.maxLength = 200;
    input.placeholder = "Название, статья или фрагмент закона";
    input.setAttribute("aria-label", "Поиск по законодательству");
    const submit = el("button", "", "Найти закон"); submit.type = "submit";
    searchForm.append(input, submit);
    searchForm.onsubmit = event => {
        event.preventDefault();
        if (input.value.trim()) go("/search?q=" + encodeURIComponent(input.value.trim()));
        else go("/search");
    };
    inner.append(searchForm);
    const quick = el("div", "hero-quicklinks");
    quick.append(el("span", "", "Быстрый переход:"));
    const highlighted = allCodes.slice(0, 3);
    if (highlighted.length) {
        highlighted.forEach(code => {
            const a = el("a", "", code.name);
            a.href = "/codes/" + code.id; a.dataset.link = "";
            quick.append(a);
        });
    } else {
        const a = el("a", "", "Каталог законов");
        a.href = "/laws"; a.dataset.link = "";
        quick.append(a);
    }
    inner.append(quick);
    if (guest) {
        const login = el("div", "hero-guest",
            authMode === "oidc" ? "Для просмотра правовых текстов войдите с помощью внешнего сервиса авторизации." :
            "Для просмотра текстов выберите роль в верхней панели. Сведения о законах доступны после входа.");
        inner.append(login);
    }
    hero.append(inner);
    main.append(hero);
    const metrics = el("div", "portal-metrics");
    [
        ["code_count", "Кодексов"],
        ["section_count", "Структурных разделов"],
        ["article_count", "Статей"],
        ["publication_count", "Публикаций органов"]
    ].forEach(pair => {
        const tile = el("div", "portal-metric");
        const total = overview ? overview.totals[pair[0]] : "—";
        tile.append(el("strong", "", Number.isInteger(total) ? total.toLocaleString("ru-RU") : total),
                    el("span", "", pair[1]));
        metrics.append(tile);
    });
    main.append(metrics);

    const jurisdictions = el("section", "home-section");
    jurisdictions.append(homeSectionHeading("Уровни права", "Выберите юрисдикцию", "Открыть весь реестр", "/laws"));
    const jurisdictionGrid = el("div", "jurisdiction-grid");
    [
        ["state", "Штат Сан-Андреас", "Кодексы и законодательные акты уровня штата.", "§"],
        ["county", "Окружное право", "Региональные правовые нормы и решения округа.", "◈"],
        ["city", "Город Лос-Сантос", "Муниципальные акты и правовые документы города.", "▥"]
    ].forEach(item => {
        const count = guest ? null : allCodes.filter(code => code.jurisdiction_id === item[0]).length;
        jurisdictionGrid.append(makeJurisdictionCard(item[0], item[1], item[2], item[3], count));
    });
    jurisdictions.append(jurisdictionGrid);
    main.append(jurisdictions);

    const featured = el("section", "home-section");
    featured.append(homeSectionHeading("Кодифицированное право", "Кодексы и собрания законов",
        "Смотреть все кодексы", "/laws"));
    if (allCodes.length) {
        const grid = el("div", "catalog-grid");
        allCodes.slice(0, 4).forEach(code => grid.append(makeLawCodeCard(code)));
        featured.append(grid);
    } else {
        featured.append(el("div", "empty", guest ?
            "Каталог кодексов откроется после выбора тестовой роли." :
            "Кодексы пока не добавлены. Реестр будет дополняться по мере публикации."));
    }
    main.append(featured);

    if (overview && overview.recent_revisions.length) {
        const updates = el("section", "home-section");
        updates.append(homeSectionHeading("История правовых текстов", "Последние редакции", "Поиск статей", "/search"));
        const list = el("div", "portal-activity");
        overview.recent_revisions.forEach(revision => {
            const a = el("a", "activity-item");
            a.href = "/articles/" + revision.id; a.dataset.link = "";
            const copy = el("div");
            copy.append(el("strong", "", "Статья " + revision.number + " — " + displayLegalTitle(revision.title)),
                el("small", "", revision.code_name + " · " + friendlyDate(revision.published_at)));
            a.append(copy, el("span", "activity-arrow", "↗"));
            list.append(a);
        });
        updates.append(list);
        main.append(updates);
    }

    if (allPortals.length) {
        const agencies = el("section", "home-section home-foot-panel");
        agencies.append(homeSectionHeading("Государственная сеть", "Прочие ресурсы"));
        const grid = el("div", "cards-grid");
        allPortals.filter(p => p.id !== "library").slice(0, 6).forEach(p => grid.append(makePortalLink(p)));
        agencies.append(grid);
        main.append(agencies);
    }
}
async function lawCatalog(preselected = "all") {
    const names = {state:"Штат Сан-Андреас", county:"Округ",city:"Город Лос-Сантос"};
    if (preselected !== "all" && !Object.hasOwn(names, preselected)) {
        throw Error("Неизвестная юрисдикция.");
    }
    main.append(breadcrumb([{label:"Главная",href:"/"},{label:"Реестр кодексов"}]));
    pageHeading("Правовая библиотека", "Каталог законов и кодексов",
        "Собрание кодексов по юрисдикциям. Выберите источник, чтобы открыть структуру и тексты статей.");
    if (isAdmin()) {
        const adminActions=el("div","catalog-admin-actions");
        const create=el("a","button primary","＋ Создать кодекс");
        create.href="/admin/codes?new=1";create.dataset.link="";
        adminActions.append(create);
        main.append(adminActions);
    }
    const bar = el("div", "catalog-toolbar");
    const tabs = el("div", "catalog-filters");
    const buttons = {};
    const searchInput = el("input", "catalog-filter-search");
    searchInput.type = "search";searchInput.maxLength = 150;
    searchInput.placeholder = "Найти кодекс";
    searchInput.setAttribute("aria-label", "Поиск по названиям кодексов");
    bar.append(tabs, searchInput);
    main.append(bar);
    const summary = el("p", "catalog-summary");
    const grid = el("div", "catalog-grid");
    main.append(summary, grid);
    let selected = preselected;
    function draw() {
        Object.entries(buttons).forEach(([id, button]) => {
            const isActive = selected === id;
            button.classList.toggle("active", isActive);
            button.setAttribute("aria-pressed", String(isActive));
        });
        const query = searchInput.value.trim().toLocaleLowerCase("ru-RU");
        const codes = allCodes.filter(code => (selected === "all" || code.jurisdiction_id === selected) &&
            (code.name + " " + code.abbreviation).toLocaleLowerCase("ru-RU").includes(query));
        summary.textContent = "Найдено кодексов: " + codes.length;
        grid.replaceChildren();
        if (!codes.length) {
            grid.append(el("div", "empty", "Подходящих кодексов нет. Измените юрисдикцию или поисковый запрос."));
        }
        codes.forEach(code => grid.append(makeLawCodeCard(code)));
    }
    [["all","Все"],["state","Штат"],["county","Округ"],["city","Лос-Сантос"]].forEach(item => {
        const button = el("button", "filter-pill", item[1]);
        button.type = "button";buttons[item[0]] = button;
        button.onclick = () => {
            selected = item[0];
            const url = selected === "all" ? "/laws" : "/laws?jurisdiction=" + selected;
            history.replaceState(history.state, "", url);
            draw();
        };
        tabs.append(button);
    });
    searchInput.addEventListener("input", draw);
    draw();

}
async function jurisdiction(id) {
    await lawCatalog(id);
}
function directChildren(nodes, parent) {
    return nodes.filter(n => n.parent_id === parent);
}
function treeStorage(codeId) {
    try { return JSON.parse(sessionStorage.getItem("sas-tree:" + codeId) || '{"expanded":[],"suppressed":[]}'); }
    catch (_) { return {expanded:[],suppressed:[]}; }
}
function saveTreeStorage(codeId, value) {
    sessionStorage.setItem("sas-tree:" + codeId, JSON.stringify(value));
}
function appendArticleIndex(container, endpoint) {
    let cursor = null;
    let hasMore = true;
    let loading = false;
    const more = el("button", "nav-index-more", "Показать ещё статьи");
    more.type = "button";
    async function fetchNext() {
        if (loading || !hasMore || !container.isConnected) return;
        loading = true;
        more.disabled = true;
        more.textContent = "Загрузка…";
        try {
            const params = new URLSearchParams({limit:"100"});
            if (cursor) params.set("cursor", cursor);
            const result = await api(endpoint + "?" + params);
            if (!container.isConnected) return;
            result.items.forEach(article => {
                const item = el("a", "nav-item article-index-link",
                    article.number + " · " + displayLegalTitle(article.title));
                item.href = "/articles/" + article.id;
                item.dataset.link = "";
                item.dataset.articleId = article.id;
                if (location.pathname === item.getAttribute("href")) item.classList.add("active");
                container.insertBefore(item, more);
            });
            cursor = result.next_cursor;
            hasMore = result.has_more;
            more.hidden = !hasMore;
        } catch(error) {
            if (!container.isConnected) return;
            more.textContent = "Повторить загрузку";
            more.hidden = false;
            return;
        } finally {
            loading = false;
            more.disabled = false;
            if (more.textContent !== "Повторить загрузку") more.textContent = "Показать ещё статьи";
        }
    }
    more.onclick = fetchNext;
    container.append(more);
    fetchNext();
}
function renderCodeNav(code, selectedNode, directTarget) {
    // Each expansion fetches a bounded page of direct child nodes. The sidebar
    // never needs the entire hierarchy or full legal article bodies.
    codeNav.replaceChildren();
    const header=el("a","tree-code-name",code.name);
    header.href="/codes/"+code.id;
    header.dataset.link="";
    if(!selectedNode && location.pathname===header.getAttribute("href"))header.classList.add("active");
    const root=el("div","code-tree-root");
    codeNav.append(header,root);
    const state=treeStorage(code.id);
    const path=(treeData && treeData.ancestors || []);
    const pathIds=path.map(n=>n.id);
    const pathSet=new Set(pathIds);
    const topLevelArticles=el("div","article-index root-article-index");
    if(!selectedNode)topLevelArticles.id="articleIndex";
    root.append(topLevelArticles);

    function loadChildren(parentId,container,requiredId) {
        let cursor=null,finished=false,busy=false;
        const seen=new Set();
        const more=el("button","nav-index-more","Показать следующие разделы");
        more.type="button";
        const destination=container.firstElementChild;
        if(destination)container.insertBefore(more,destination);
        else container.append(more);
        async function page() {
            if(finished||busy||!root.isConnected)return;
            busy=true;
            more.disabled=true;more.textContent="Загрузка разделов…";
            try{
                const params=new URLSearchParams({limit:"50"});
                if(parentId)params.set("parent_id",parentId);
                if(cursor)params.set("cursor",cursor);
                const result=await api("/codes/"+code.id+"/structure-index?"+params);
                if(!root.isConnected)return;
                result.items.forEach(node=>{
                    seen.add(node.id);
                    container.insertBefore(entry(node),more);
                });
                cursor=result.next_cursor;
                finished=!result.has_more;
                more.hidden=finished;
                if(!finished)more.textContent="Показать следующие разделы";
                if(!result.items.length && finished)more.remove();
            }catch(error){
                more.hidden=false;
                more.textContent="Повторить загрузку разделов";
                more.title=error.message;
            }finally{
                busy=false;
                more.disabled=false;
            }
        }
        more.onclick=page;
        async function ensure() {
            await page();
            // The path to a deep-linked article may lie beyond the first 50
            // siblings. Load only as far as necessary to reach that branch.
            while(requiredId && !seen.has(requiredId) && !finished &&
                  more.textContent!=="Повторить загрузку разделов" &&
                  root.isConnected) {
                await page();
            }
        }
        return {ensure};
    }
    function entry(node) {
        const wrap=el("div","code-tree-node");
        const line=el("div","tree-entry");
        const selected=node.id===selectedNode;
        const type=treeData.types.find(t=>t.id===node.type_id);
        const caption=(node.number?(type?type.name+" ":node.type_name?node.type_name+" ":"")+node.number+" · ":"")+
            displayLegalTitle(node.title);
        const link=el("a","nav-item",caption);
        link.href="/codes/"+code.id+"/nodes/"+node.id;
        link.dataset.link="";link.dataset.nodeId=node.id;
        if(selected)link.classList.add("active");
        const hasBranches=Number(node.child_count||0)>0||pathSet.has(node.id)||selected;
        const toggle=el("button","tree-toggle","▸");
        toggle.type="button";
        toggle.disabled=!hasBranches;
        toggle.setAttribute("aria-label","Развернуть "+displayLegalTitle(node.title));
        line.append(toggle,link);
        wrap.append(line);
        if(hasBranches){
            const branch=el("div","tree-branch");
            wrap.append(branch);
            const index=selected?el("div","article-index"):null;
            const childOnPath=pathIds[pathIds.indexOf(node.id)+1]||null;
            const children=Number(node.child_count||0)>0?
                loadChildren(node.id,branch,childOnPath):null;
            if(index){
                index.id="articleIndex";
                branch.append(index);
            }
            let expanded=state.expanded.includes(node.id)||selected||
                (pathSet.has(node.id) && (directTarget||!state.suppressed.includes(node.id)));
            let launched=false;
            function paint(){
                branch.hidden=!expanded;
                toggle.textContent=expanded?"▾":"▸";
                toggle.setAttribute("aria-expanded",String(expanded));
            }
            function loadExpanded() {
                if(!expanded||launched)return;
                launched=true;
                Promise.resolve().then(async()=>{
                    if(!root.isConnected)return;
                    if(children)await children.ensure();
                    if(index && index.isConnected)
                        appendArticleIndex(index,"/nodes/"+node.id+"/article-index");
                }).catch(error=>notify(error.message));
            }
            toggle.onclick=()=>{
                expanded=!expanded;
                state.expanded=state.expanded.filter(x=>x!==node.id);
                state.suppressed=state.suppressed.filter(x=>x!==node.id);
                (expanded?state.expanded:state.suppressed).push(node.id);
                saveTreeStorage(code.id,state);
                paint();
                loadExpanded();
            };
            paint();
            loadExpanded();
        }
        return wrap;
    }
    const initial=loadChildren(null,root,pathIds[0]||null);
    initial.ensure().catch(error=>notify(error.message));
    appendArticleIndex(topLevelArticles,"/codes/"+code.id+"/root/article-index");
}
function highlightSidebar(articleId) {
    if (!reading) return;
    document.querySelectorAll(".article-index-link.active").forEach(a => a.classList.remove("active"));
    const link = document.querySelector('.article-index-link[data-article-id="' + articleId + '"]');
    if (link) {
        link.classList.add("active");
        link.scrollIntoView({block:"nearest"});
    }
}
async function attachInlineRelationCreator(host,article) {
    const add=el("button","button ghost relation-inline-toggle","＋ Добавить юридическую связь");
    add.type="button";add.setAttribute("aria-expanded","false");
    const panel=el("div","relation-inline-form");
    panel.hidden=true;
    add.onclick=async()=>{
        panel.hidden=!panel.hidden;
        add.setAttribute("aria-expanded",String(!panel.hidden));
        if(panel.hidden||panel.dataset.ready)return;
        panel.dataset.ready="true";
        panel.append(el("p","muted","Выберите вид связи и найдите статью, с которой хотите связать текущую норму."));
        try{
            const types=await api("/relation-types");
            if(!types.length){
                const help=el("p","muted","Виды связей ещё не настроены.");
                const link=el("a","","Настроить виды связей");
                link.href="/admin/relations?view=rules";link.dataset.link="";
                panel.append(help,link);return;
            }
            const picker=await adminArticleFinder("Связать со статьёй","");
            const type=adminSelect(types.map(t=>[t.name,t.id]));
            const form=adminForm(async()=>{
                if(!picker.select.value)throw Error("Выберите статью из результатов поиска.");
                if(picker.select.value===article.id)throw Error("Статья не может ссылаться на саму себя.");
                await api("/relations",{method:"POST",body:{
                    type_id:type.value,source_id:article.id,target_id:picker.select.value}});
                notify("Юридическая связь добавлена.");
                go(location.pathname+location.search);
            });
            form.save.textContent="Создать связь";
            form.form.prepend(adminField("Как связаны статьи",type),picker.wrap);
            panel.append(form.form);
        }catch(error){panel.append(el("p","notice",error.message));}
    };
    host.append(add,panel);
}
function createArticleCard(a) {
    const card = el("article", "article-card");
    card.id = "card-" + a.id;
    card.dataset.articleId = a.id;
    const top = el("div", "article-top");
    const titles = el("div");
    titles.append(el("div", "article-number", "§ " + a.number), el("h2", "", displayLegalTitle(a.title)));
    top.append(titles);

    card.append(top);
    const textBlock = el("div", "article-text");
    textBlock.innerHTML = a.text_rich || "<p>Текст не указан.</p>";
    card.append(textBlock);
    if (a.provenance && a.provenance.publication_id) {
        const p = el("p", "small muted");
        p.append("Источник изменения: ");
        const link = el("a", "", a.provenance.issuer || "Официальный источник");
        link.href = "/publications/" + a.provenance.publication_id; link.dataset.link = "";
        p.append(link, " · " + friendlyDate(a.provenance.published_at));
        card.append(p);
    }
    const extra = isLawyer();
    let legalDetails = null;
    if (extra) {
        const details = el("details", "detail-block");
        details.append(el("summary", "", "Дополнительные юридические сведения"));
        const dl = el("dl");
        const fields = [
            ["Категории", a.categories.map(c => CATEGORY_NAMES[c] || c).join(", ")],
            ["Уголовно-правовой класс", criminalClassLabel(a.criminal_class)],
            ["Лишение свободы (месяцы)", text(a.sanctions.prison_min_months) + " — " + text(a.sanctions.prison_max_months)],
            ["Штраф (USD)", text(a.sanctions.fine_min_usd) + " — " + text(a.sanctions.fine_max_usd)]
        ];
        fields.forEach(pair => dl.append(el("dt", "", pair[0]),el("dd", "", pair[1])));
        details.append(dl);
        card.append(details);
        legalDetails = details;
    }
    const relationBox = el("div", "small");
    api("/articles/" + a.id + "/relations").then(related => {
        if (!card.isConnected) return;
        if (related.unassigned_punishment) relationBox.append(el("p", "muted", "Не привязана к обвинению"));
        if (related.items.length) {
            relationBox.append(el("strong", "", "Связанные статьи: "));
            related.items.forEach(rel => {
                const otherId = rel.source_id === a.id ? rel.target_id : rel.source_id;
                const otherNumber = rel.source_id === a.id ? rel.target_number : rel.source_number;
                const otherTitle = rel.source_id === a.id ? rel.target_title : rel.source_title;
                const link = el("a", "", "Ст. " + otherNumber + " — " + otherTitle);
                link.href = "/articles/" + otherId; link.dataset.link = "";
                relationBox.append(link, " · ");
            });
        }
    }).catch(() => {});
    card.append(relationBox);
    if(isAdmin())attachInlineRelationCreator(relationBox,a);
    if(isLawyer())attachArticleAnnotations(card,a,textBlock,legalDetails);
    const actions = el("div", "article-actions");
    const open = el("button", "button ghost", "Постоянная ссылка");
    open.type = "button";
    open.onclick = () => navigator.clipboard.writeText(location.origin + "/articles/" + a.id)
        .then(() => notify("Постоянная ссылка скопирована."))
        .catch(() => notify("Не удалось скопировать ссылку."));
    actions.append(open);
    const copy = el("button", "button subtle", "Копировать цитату");
    copy.type = "button";
    copy.onclick = () => {
        const codeName = allCodes.find(code => code.id === a.code_id)?.name || "Кодекс";
        const path = [codeName, "Статья " + a.number + ". " + displayLegalTitle(a.title)].join(" → ");
        navigator.clipboard.writeText(path + "\n\n" + (textBlock.innerText || textBlock.textContent || "").trim())
            .then(() => notify("Название и текст статьи скопированы."))
            .catch(() => notify("Не удалось скопировать цитату."));
    };
    actions.append(copy);
    if (isLawyer()) {
        const favorite = el("button", "button subtle");
        favorite.type = "button";
        const syncFavorite = () => {
            const saved = favoriteIds.has(a.id);
            favorite.textContent = saved ? "★ В избранном" : "☆ В избранное";
            favorite.setAttribute("aria-pressed", String(saved));
        };
        syncFavorite();
        favorite.onclick = async () => {
            favorite.disabled = true;
            try {
                const wasSaved = favoriteIds.has(a.id);
                await api("/me/favorites/" + a.id, {method:wasSaved ? "DELETE" : "PUT"});
                if (wasSaved) favoriteIds.delete(a.id);
                else favoriteIds.add(a.id);
                syncFavorite();
                notify(wasSaved ? "Статья удалена из избранного." : "Статья добавлена в избранное.");
            } catch (error) { notify(error.message); }
            finally { favorite.disabled = false; }
        };
        actions.append(favorite);
        const historyLink = el("a", "button subtle", "Редакции");
        historyLink.href = "/revisions/" + a.id; historyLink.dataset.link = "";
        actions.append(historyLink);
    }
    if (isAdmin()) {
        const edit = el("button", "button primary", "Редактировать");
        edit.onclick = () => openEditor(a, reading && reading.codeId, reading && reading.nodeId);
        actions.append(edit);
        const relationAdmin=el("a","button ghost","Управление связями");
        relationAdmin.href="/admin/relations?article="+encodeURIComponent(a.id);relationAdmin.dataset.link="";
        actions.append(relationAdmin);
        const archiveAdmin=el("a","button ghost article-archive-action","♲");
        archiveAdmin.textContent="🗑";
        archiveAdmin.title="Архивировать статью";
        archiveAdmin.setAttribute("aria-label","Архивировать статью");
        archiveAdmin.href="/admin/archive?article="+encodeURIComponent(a.id);archiveAdmin.dataset.link="";
        actions.append(archiveAdmin);
    }
    card.append(actions);
    if (location.pathname === "/articles/" + a.id) {
        const neighbors = el("div", "article-actions");
        api("/articles/" + a.id + "/adjacent").then(items => {
            if (!card.isConnected) return;
            [["previous","← Предыдущая"],["next","Следующая →"]].forEach(pair => {
                const target = items[pair[0]];
                if (!target) return;
                const aLink = el("a", "button ghost", pair[1] + " · " + target.number);
                aLink.href = "/articles/" + target.id;
                aLink.dataset.link = "";
                aLink.title = target.title;
                neighbors.append(aLink);
            });
        }).catch(() => {});
        card.append(neighbors);
    }
    return card;
}
async function loadNext() {
    if (!reading || reading.loading || !reading.hasMore) return;
    const state = reading;
    state.loading = true;
    state.status.replaceChildren(el("span", "", "Загрузка статей…"));
    try {
        const endpoint = state.nodeId ? "/nodes/" + state.nodeId + "/articles" :
            "/codes/" + state.codeId + "/root/articles";
        const params = new URLSearchParams({limit:"12"});
        if (state.cursor) params.set("cursor", state.cursor);
        const result = await api(endpoint + "?" + params);
        if (reading !== state) return;
        result.items.forEach(a => {
            const card = createArticleCard(a);
            state.cards.append(card);
            if (activeObserver) activeObserver.observe(card);
        });
        state.cursor = result.next_cursor;
        state.hasMore = result.has_more;
        state.loaded += result.items.length;
        if (state.count) state.count.textContent = "Загружено статей: " + state.loaded;
        state.loading = false;
        state.status.replaceChildren();
        if (!state.hasMore && !state.cards.children.length) {
            state.status.append(el("p", "reader-empty", state.emptyMessage || "Статей пока нет."));
        } else if (state.hasMore) {
            const more = el("button", "button ghost", "Загрузить ещё статьи");
            more.type = "button";
            more.onclick = () => loadNext();
            state.status.append(more);
        }
        if (state.targetId) {
            const target = document.getElementById("card-" + state.targetId);
            if (target) {
                // Browser history restores its own precise position; a newly opened
                // permalink instead highlights and scrolls to the selected article.
                if (state.restoreScroll === null) {
                    target.classList.add("target");
                    target.scrollIntoView({block:"start"});
                    setTimeout(() => target.classList.remove("target"), 2800);
                }
                state.targetId = null;
            } else if (state.hasMore) {
                await loadNext();
                return;
            } else {
                notify("Целевая статья отсутствует в текущем разделе.");
                state.targetId = null;
            }
        }
        if (state.restoreScroll !== null) {
            if (document.body.scrollHeight >= state.restoreScroll + window.innerHeight ||
                !state.hasMore) {
                window.scrollTo(0, state.restoreScroll);
                state.restoreScroll = null;
                state.restoring = false;
                storePosition();
            } else {
                await loadNext();
                return;
            }
        } else {
            state.restoring = false;
            storePosition();
        }
    } catch(error) {
        if (reading !== state) return;
        state.loading = false;
        state.status.replaceChildren(el("span", "", "Не удалось загрузить статьи"));
        const retry = el("button", "button ghost", "Повторить");
        retry.onclick = () => loadNext();
        state.status.append(retry);
    }
}
let structureState = null;
const structureDialog = document.getElementById("structureDialog");
const structureForm = document.getElementById("structureForm");
function allowedParentTypes(type) {
    try {
        const parents = typeof type.allowed_parents === "string" ?
            JSON.parse(type.allowed_parents) : type.allowed_parents;
        return Array.isArray(parents) ? parents : [];
    } catch (_) { return []; }
}
async function openStructureDialog(mode, codeId, parentId = null) {
    if (!isAdmin()) return;
    const full=await api("/codes/"+codeId+"/tree");
    const nodes=full.nodes||[];
    const types=full.types||[];
    const parent = nodes.find(n => n.id === parentId) || null;
    if (mode === "node" && !types.length) mode = "type";
    if (mode === "edit" && !parent) return notify("Раздел не найден.");
    structureState = {mode,codeId,parentId};
    structureForm.reset();
    document.getElementById("structureStatus").textContent = "";
    document.getElementById("saveStructure").disabled = false;
    const nodeFields = document.getElementById("structureNodeFields");
    const typeFields = document.getElementById("structureTypeFields");
    nodeFields.hidden = mode === "type";
    typeFields.hidden = mode !== "type";
    document.getElementById("structureTitle").textContent = mode === "type" ?
        "Новый тип структурного уровня" :
        mode === "edit" ? "Редактирование раздела" : "Новый элемент структуры";
    if (mode === "type") {
        const highest = types.reduce((n, t) => Math.max(n, Number(t.level_order) || 0), 0);
        structureForm.elements.level_order.value = highest + 1;
        const choices = document.getElementById("structureAllowedParents");
        choices.replaceChildren();
        [["ROOT","Корень кодекса"],...types.map(t => [t.id,t.name])].forEach(([id,name]) => {
            const label = el("label", "structure-checkbox");
            const checkbox = el("input");
            checkbox.type = "checkbox";
            checkbox.name = "parent_type";
            checkbox.value = id;
            checkbox.checked = id === "ROOT" && types.length === 0;
            label.append(checkbox, document.createTextNode(name));
            choices.append(label);
        });
    } else {
        const typeSelect = document.getElementById("structureTypeSelect");
        const parentSelect = document.getElementById("structureParentSelect");
        typeSelect.replaceChildren();
        types.forEach(t => typeSelect.append(new Option(t.name, t.id)));
        const compatible = types.find(t => allowedParentTypes(t)
            .includes(parent ? parent.type_id : "ROOT"));
        if (mode === "edit") typeSelect.value = parent.type_id;
        else if (compatible) typeSelect.value = compatible.id;
        function updateParents() {
            const chosen = types.find(t => t.id === typeSelect.value);
            const allowed = chosen ? allowedParentTypes(chosen) : [];
            parentSelect.replaceChildren();
            if (allowed.includes("ROOT")) {
                parentSelect.append(new Option("Корень кодекса", ""));
            }
            nodes.filter(n => allowed.includes(n.type_id) && (mode !== "edit" || n.id !== parentId))
                 .forEach(n => parentSelect.append(new Option(
                     (n.number?n.number+" · ":"") + displayLegalTitle(n.title), n.id)));
            if (parentId && [...parentSelect.options].some(o => o.value === parentId)) {
                parentSelect.value = parentId;
            }
            parentSelect.disabled = mode === "edit" || !parentSelect.options.length;
            if (!parentSelect.options.length && mode !== "edit") {
                document.getElementById("structureStatus").textContent =
                    "Для выбранного типа нет допустимого родителя. Настройте тип уровня.";
            } else {
                document.getElementById("structureStatus").textContent = "";
            }
        }
        const numberInput=structureForm.elements.number;
        function updateNumberField(){
            const chosen=types.find(t=>t.id===typeSelect.value);
            const unnumbered=chosen?.number_format==="none";
            numberInput.required=!unnumbered;
            numberInput.disabled=unnumbered;
            if(unnumbered)numberInput.value="";
            numberInput.closest("label").hidden=unnumbered;
        }
        typeSelect.onchange=()=>{updateParents();updateNumberField();};
        typeSelect.disabled = mode === "edit";
        updateNumberField();
        updateParents();
        if (mode === "edit") {
            structureForm.elements.number.value = parent.number;
            structureForm.elements.title.value = parent.title;
        }
    }
    structureDialog.showModal();
}
function closeStructureDialog() {
    if (structureDialog.open) structureDialog.close();
    structureState = null;
}
document.getElementById("closeStructure").onclick = closeStructureDialog;
document.getElementById("cancelStructure").onclick = closeStructureDialog;
structureDialog.addEventListener("close", () => { structureState = null; });
structureForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (!structureState) return;
    const state = {...structureState};
    const status = document.getElementById("structureStatus");
    const save = document.getElementById("saveStructure");
    const fields = structureForm.elements;
    save.disabled = true;
    status.textContent = "Сохранение структуры…";
    try {
        if (state.mode === "type") {
            const allowed = [...structureForm.querySelectorAll('input[name="parent_type"]:checked')]
                .map(input => input.value);
            if (!allowed.length) throw Error("Выберите хотя бы один разрешённый тип родителя.");
            const name = fields.type_name.value.trim();
            if (!name) throw Error("Введите название типа.");
            await api("/codes/" + state.codeId + "/hierarchy-types", {
                method:"POST",
                body:{name, level_order:Number(fields.level_order.value),
                      number_format:fields.number_format.value,allowed_parents:allowed}
            });
            closeStructureDialog();
            notify("Тип уровня создан. Теперь можно добавить элемент структуры.");
            go("/codes/" + state.codeId);
        } else if (state.mode === "edit") {
            const number = fields.number.value.trim();
            const title = fields.title.value.trim();
            if ((!fields.number.disabled && !number) || !title) throw Error("Заполните название и необходимый номер.");
            await api("/nodes/" + state.parentId, {
                method:"PATCH",body:{number,title}
            });
            closeStructureDialog();
            notify("Раздел обновлён.");
            go("/codes/" + state.codeId + "/nodes/" + state.parentId);
        } else {
            if (fields.parent_id.disabled) throw Error("Для выбранного типа нет допустимого родителя.");
            const number = fields.number.value.trim();
            const title = fields.title.value.trim();
            if ((!fields.number.disabled && !number) || !title) throw Error("Заполните название и необходимый номер.");
            const result = await api("/codes/" + state.codeId + "/nodes",{
                method:"POST",
                body:{type_id:fields.type_id.value, parent_id:fields.parent_id.value || null,
                      number,title}
            });
            closeStructureDialog();
            notify("Раздел создан.");
            go("/codes/" + state.codeId + "/nodes/" + result.id);
        }
    } catch(error) {
        status.textContent = error.message || "Не удалось сохранить структуру.";
        save.disabled = false;
    }
});

async function openSection(codeId, nodeId, targetId, options, serial) {
    const code = allCodes.find(c => c.id === codeId);
    if (!code) throw Error("Кодекс не найден.");
    // Reader fetches metadata and the selected ancestor chain, not the full code.
    const [skeleton,context] = await Promise.all([
        api("/codes/" + codeId + "/tree?include_nodes=false"),
        nodeId?api("/codes/" + codeId + "/node-context?node_id=" +
            encodeURIComponent(nodeId)):Promise.resolve(null)
    ]);
    if (serial !== lastNavSerial) return;
    treeData={...skeleton,nodes:context?context.ancestors:[],
        ancestors:context?context.ancestors:[]};
    const selected=context?context.node:null;
    renderCodeNav(code, nodeId, !!targetId);
    const trail = [{label:"Реестр законов",href:"/laws"},
                   {label:code.name,href:"/codes/" + code.id}];
    if (selected) {
        const ancestors = [];
        let cursor = selected;
        while (cursor) {
            ancestors.unshift(cursor);
            cursor = cursor.parent_id && treeData.nodes.find(n => n.id === cursor.parent_id);
        }
        ancestors.forEach((n,index) => trail.push({
            label: (n.number?n.number+" · ":"") + displayLegalTitle(n.title),
            href: index < ancestors.length - 1 ? "/codes/" + codeId + "/nodes/" + n.id : null
        }));
    } else {
        trail[trail.length - 1].href = null;
    }
    main.append(breadcrumb(trail));
    const title = selected ? (selected.number?selected.number+" · ":"")+displayLegalTitle(selected.title) : code.name;
    pageHeading(code.abbreviation ? "Кодекс " + code.abbreviation : "Законодательный кодекс",
                title, selected ? "Структурный раздел кодекса «" + code.name + "»." :
                "Содержание кодекса. Выберите раздел для изучения норм права.");
    if (code.publication_issuer || code.publication_reference || code.published_at) {
        const provenance=el("p","code-provenance");
        const fields=[];
        if(code.publication_issuer)fields.push("Издавший орган: "+code.publication_issuer);
        if(code.publication_reference)fields.push("Реквизиты: "+code.publication_reference);
        if(code.published_at)fields.push("Дата публикации: "+friendlyDate(code.published_at));
        provenance.textContent=fields.join(" · ");
        main.append(provenance);
    }

    const toolbar = el("div", "reader-subnav");
    const catalogLink = el("a", "button ghost", "← Все кодексы");
    catalogLink.href = "/laws"; catalogLink.dataset.link = "";
    const findInCode = el("a", "button ghost", "Поиск по кодексу");
    findInCode.href = isLawyer() ? "/search?code_id=" + encodeURIComponent(code.id) : "/search";
    findInCode.dataset.link = "";
    const count = el("span", "reader-count", "Загружено статей: 0");
    toolbar.append(catalogLink, findInCode);
    if (isAdmin()) {
        const addNode = el("button", "button primary", "Добавить часть / раздел");
        addNode.type = "button";
        addNode.onclick = () => openStructureDialog("node", codeId, nodeId);
        const addType = el("button", "button ghost", "Добавить тип уровня");
        addType.type = "button";
        addType.onclick = () => openStructureDialog("type", codeId);
        toolbar.append(addNode, addType);
        if(!nodeId){
            const intro=el("button","button ghost","＋ Вводный раздел");
            intro.type="button";
            intro.title="Ненумерованный раздел непосредственно в корне кодекса";
            intro.onclick=async()=>{
                const title=prompt("Название вводного раздела (например, Предварительные положения):");
                if(title===null)return;
                if(!title.trim())return notify("Введите название раздела.");
                intro.disabled=true;
                try{
                    const tree=await api("/codes/"+codeId+"/tree?include_nodes=false");
                    let type=tree.types.find(t=>t.name==="Вводный раздел" && t.number_format==="none" &&
                        allowedParentTypes(t).includes("ROOT"));
                    if(!type){
                        const created=await api("/codes/"+codeId+"/hierarchy-types",{method:"POST",body:{
                            name:"Вводный раздел",number_format:"none",level_order:0,
                            allowed_parents:["ROOT"]}});
                        type={id:created.id};
                    }
                    const node=await api("/codes/"+codeId+"/nodes",{method:"POST",body:{
                        type_id:type.id,parent_id:null,number:"",title:title.trim()}});
                    notify("Вводный раздел создан в корне кодекса.");
                    go("/codes/"+codeId+"/nodes/"+node.id);
                }catch(error){notify(error.message);}
                finally{intro.disabled=false;}
            };
            toolbar.append(intro);
        }
        const hierarchy=el("a","button ghost","Настроить иерархию");
        hierarchy.href="/admin/hierarchy?code="+encodeURIComponent(codeId);hierarchy.dataset.link="";
        toolbar.append(hierarchy);
        if (selected) {
            const editNode = el("button", "button ghost", "Изменить раздел");
            editNode.type = "button";
            editNode.onclick = () => openStructureDialog("edit", codeId, nodeId);
            toolbar.append(editNode);
            const archiveNode=el("a","button ghost","Архивировать раздел");
            archiveNode.href="/admin/archive?node="+encodeURIComponent(nodeId);
            archiveNode.dataset.link="";
            toolbar.append(archiveNode);
        }
    }
    toolbar.append(count);
    main.append(toolbar);

    const query=new URLSearchParams({limit:"24"});
    if(nodeId)query.set("parent_id",nodeId);
    const subnodePage=await api("/codes/"+codeId+"/structure-index?"+query);
    if(serial!==lastNavSerial)return;
    const subnodes=subnodePage.items;
    if(subnodes.length){
        const sections=el("section","section-catalog");
        sections.append(homeSectionHeading("Структура документа",
            selected?"Подразделы":"Содержание кодекса"));
        const sectionGrid=el("div","section-grid");
        const renderSubnode=node=>{
            const type=treeData.types.find(t=>t.id===node.type_id);
            const a=el("a","section-tile");
            a.href="/codes/"+codeId+"/nodes/"+node.id;
            a.dataset.link="";
            const title=el("span","section-tile-text");
            title.append(el("strong","",displayLegalTitle(node.title)),
                el("small","",type?type.name:node.type_name||"Раздел кодекса"));
            if(node.number)a.append(el("span","section-tile-number",node.number));
            a.append(title,el("span","section-tile-arrow","↗"));
            return a;
        };
        subnodes.forEach(node=>sectionGrid.append(renderSubnode(node)));
        sections.append(sectionGrid);
        if(subnodePage.has_more){
            const more=el("button","button ghost","Показать следующие разделы");
            let cursor=subnodePage.next_cursor;
            more.type="button";
            more.onclick=async()=>{
                more.disabled=true;more.textContent="Загрузка разделов…";
                try{
                    const params=new URLSearchParams({limit:"24",cursor});
                    if(nodeId)params.set("parent_id",nodeId);
                    const page=await api("/codes/"+codeId+"/structure-index?"+params);
                    if(!sections.isConnected)return;
                    page.items.forEach(node=>sectionGrid.append(renderSubnode(node)));
                    cursor=page.next_cursor;
                    if(!page.has_more)more.remove();
                }catch(error){
                    notify(error.message);
                }finally{
                    more.disabled=false;
                    more.textContent="Показать следующие разделы";
                }
            };
            sections.append(more);
        }
        main.append(sections);
    }
    const heading = el("div", "section-heading");
    heading.append(el("h2", "", selected ? "Статьи раздела" : "Статьи кодекса"));
    if (isAdmin()) {
        const create = el("button", "button primary", "Создать статью");
        create.onclick = () => openEditor(null, codeId, nodeId);
        heading.append(create);
    }
    main.append(heading);
    const cards = el("section", "reader-articles");
    cards.setAttribute("aria-label", "Текст статей");
    const loadStatus = el("div", "load-status");
    loadStatus.setAttribute("role", "status");
    main.append(cards, loadStatus);
    // Explicit article permalinks take precedence over an unrelated saved scroll.
    // Back/Forward entries which were actually read carry their own scroll position.
    const hasHistoryPosition = Boolean(options.restore &&
        history.state && history.state.hasReadingPosition &&
        Number.isFinite(Number(options.scrollY)));
    const rememberedPosition = options.restore ?
        (hasHistoryPosition ? Number(options.scrollY) :
         (!targetId ? Number(sessionStorage.getItem("sas-scroll:" + location.pathname) || 0) : null)) : null;
    reading = {
        path:location.pathname, restoring:rememberedPosition!==null || Boolean(targetId),
        codeId, nodeId, cards, status:loadStatus, cursor:null, hasMore:true, loading:false,
        loaded:0, count, emptyMessage: subnodes.length ?
            "В этом разделе пока нет отдельных статей. Выберите подраздел выше или в боковом содержании." :
            "В данном разделе ещё нет опубликованных статей.",
        targetId: targetId || null,
        restoreScroll: rememberedPosition
    };
    activeObserver = new IntersectionObserver(entries => {
        const visible = entries.filter(e => e.isIntersecting)
            .sort((a,b) => b.intersectionRatio - a.intersectionRatio);
        if (visible.length) {
            document.querySelectorAll(".article-card.reading").forEach(card => card.classList.remove("reading"));
            visible[0].target.classList.add("reading");
            highlightSidebar(visible[0].target.dataset.articleId);
        }
    }, {rootMargin:"-120px 0px -65% 0px",threshold:[0,.3,.6]});
    loadObserver = new IntersectionObserver(entries => {
        if (entries[0].isIntersecting && reading && reading.hasMore) loadNext();
    }, {rootMargin:"400px 0px"});
    loadObserver.observe(loadStatus);
    if (targetId) {
        const position = await api("/articles/" + targetId + "/position");
        if (reading && reading.targetId === targetId) reading.cursor = position.cursor_before;
    }
    await loadNext();
}
async function searchPage() { return advancedSearchPage(); }
async function favoritesPage() {
    pageHeading("Персональные инструменты", "Избранные статьи",
        "Ваш единый список избранных норм законодательства.");
    const items = await api("/me/favorites");
    if (!items.length) main.append(el("p", "empty", "Вы пока не добавили статьи в избранное."));
    for (const a of items) {
        const item = el("div", "search-result");
        const link = el("a", "", "Статья " + a.number + " — " + displayLegalTitle(a.title));
        link.href = "/articles/" + a.id; link.dataset.link = "";
        item.append(link);
        const remove = el("button", "button subtle", "Удалить из избранного");
        remove.onclick = async () => {
            try {
                await api("/me/favorites/" + a.id, {method:"DELETE"});
                favoriteIds.delete(a.id);
                item.remove();
            }
            catch(e) { notify(e.message); }
        };
        item.append(remove); main.append(item);
    }
}
async function notificationsPage() {
    pageHeading("Правовая библиотека", "Центр уведомлений",
        "События, связанные только с вашей работой в Правовой библиотеке штата Сан Андреас.");
    const section = el("section", "notifications-list");
    main.append(section);
    try {
        const list = await api("/me/notifications");
        if (!list.length) section.append(el("p", "empty", "Уведомлений пока нет."));
        list.forEach(n => {
            const item = el("div", "search-result");
            item.append(el("strong", "", n.title),
                el("span", "", friendlyDate(n.created_at) + (n.is_read ? " · Прочитано" : " · Новое")));
            if (n.object_id && /^[A-Za-z0-9-]+$/.test(n.object_id)) {
                const link = el("a", "button subtle", "Открыть статью");
                link.href = "/articles/" + n.object_id;
                link.dataset.link = "";
                item.append(link);
            }
            if (!n.is_read) {
                const button = el("button", "button subtle", "Отметить прочитанным");
                button.type = "button";
                button.onclick = async () => {
                    try {
                        await api("/me/notifications/" + n.id + "/read", {method:"POST"});
                        button.remove();
                    } catch(e) { notify(e.message); }
                };
                item.append(button);
            }
            section.append(item);
        });
    } catch(e) {
        section.replaceChildren(el("p", "notice", e.message));
    }
}
async function portalPage(portalId) {
    const portal = allPortals.find(p => p.id === portalId);
    if (!portal) throw Error("Портал не найден.");
    main.append(breadcrumb([{label:"Главная",href:"/"},{label:portal.name}]));
    pageHeading("Государственный портал", portal.name,
        "Опубликованные документы, решения и официальные реквизиты соответствующего органа.");
    if (portal.id === "legislature") {
        const a = el("a", "button primary", "Законопроекты");
        a.href = "/bills"; a.dataset.link = ""; main.append(a);
    }
    if (hasRole("portal_publisher")) {
        const create = el("button", "button ghost", "Подготовить документ");
        create.onclick = () => publicationComposer(portalId);
        main.append(create);
    }
    const items = await api("/portal-publications?portal_id=" + encodeURIComponent(portalId));
    if (!items.length) main.append(el("p", "empty", "Официальные документы пока не опубликованы."));
    items.forEach(doc => {
        const a = el("a", "search-result");
        a.href = "/publications/" + doc.id;a.dataset.link = "";
        a.append(el("span", "", doc.document_type + " · " + friendlyDate(doc.published_at)),
                 el("strong", "", doc.title), el("span", "", doc.issuer));
        main.append(a);
    });
}
async function publicationPage(id) {
    const doc = await api("/portal-publications/" + id);
    main.append(breadcrumb([{label:"Главная",href:"/"},{label:"Портал",href:"/portals/"+doc.portal_id},
        {label:doc.title}]));
    pageHeading("Опубликованный документ", doc.title, doc.issuer + " · " + friendlyDate(doc.published_at));
    const card = el("article", "article-card");
    const t = el("div", "article-text");t.innerHTML=doc.text_rich;card.append(t);
    const meta = el("p", "small muted", "Реквизиты: " + text(doc.external_reference) + " · ID: " + doc.id);
    card.append(meta);
    if (doc.related_article_id) {
        const a = el("a", "button ghost", "Связанная статья");
        a.href="/articles/"+doc.related_article_id;a.dataset.link="";card.append(a);
    }
    main.append(card);
}
async function precedentPage() {
    pageHeading("Судебная система", "Судебные прецеденты",
        "Отдельный каталог квалифицированных редактором судебных решений. Новые публикации отображаются первыми.");
    const docs=await api("/precedents");
    if(!docs.length)main.append(el("p","empty","Каталог прецедентов пока пуст."));
    docs.forEach(d => {
        const a=el("a","search-result");a.href="/publications/"+d.id;a.dataset.link="";
        a.append(el("span","",friendlyDate(d.published_at)),el("strong","",d.title),el("span","",d.issuer));
        main.append(a);
    });
}
async function revisionsPage(id) {
    const article=await api("/articles/"+id);
    const requested=new URLSearchParams(location.search).get("revision");
    main.append(breadcrumb([{label:"Каталог",href:"/laws"},
        {label:"Статья "+article.number,href:"/articles/"+id},
        {label:"История редакций"}]));
    pageHeading("Архив редакций","История статьи "+article.number,displayLegalTitle(article.title));
    const versions=await api("/articles/"+id+"/revisions");
    for(const version of versions){
        const section=el("section","panel revision-panel");
        section.id="revision-"+version.id;
        section.append(el("h2","","Редакция "+version.sequence+" · "+
                friendlyDate(version.published_at)),
            el("p","muted",version.change_reason||"Основание не указано"));
        try {
            const source=JSON.parse(version.source_json||"{}");
            if(source.publication_id){
                section.append(el("p","muted","Официальный источник: "+
                    (source.issuer||"Не указан")+" · "+
                    friendlyDate(source.published_at)));
                section.append(adminLink("Открыть публикацию",
                    "/publications/"+source.publication_id));
            }else{
                section.append(el("p","muted","Внутренняя редакционная операция."));
            }
        }catch(_) {}
        const content=el("div","revision-content");
        const show=adminButton("Показать текст и сведения редакции",async()=>{
            show.disabled=true;
            try{
                const data=await api("/articles/"+id+"/revisions/"+version.id);
                const rich=el("div","article-text");
                rich.innerHTML=data.text_rich;
                content.append(rich);
                const details=el("details","detail-block");
                details.append(el("summary","","Юридическое состояние этой редакции"));
                const pre=el("pre","admin-json");
                pre.textContent=JSON.stringify({
                    historical_labels:data.display_snapshot||{},
                    categories:data.categories_json,
                    custom_values:data.custom_values,
                    criminal_class:data.criminal_class,
                    sanctions:data.sanctions_json,
                    relations:data.relations_json,
                    provenance:data.source_json
                },null,2);
                details.append(pre);
                content.append(details);
                if(isLawyer()){
                    const notes=el("div","annotation-list");
                    content.append(el("h3","","Научные примечания"),notes);
                    await renderRevisionAnnotations(id,version.id,notes);
                }
                show.remove();
            }catch(error){notify(error.message);show.disabled=false;}
        });
        section.append(show,content);
        main.append(section);
        if(requested===version.id){
            show.click();
            requestAnimationFrame(()=>section.scrollIntoView({block:"start"}));
        }
    }
}
async function billListPage() {
    pageHeading("Законодательный орган", "Законопроекты",
        "Обсуждения, голосования и последовательность опубликованных результатов. Порядок обработки определяется регламентом.");
    if(hasRole("legislator")){
        const create=el("button","button primary","Новый законопроект");
        create.onclick=async()=>{
            const title=prompt("Заголовок законопроекта");
            if(!title)return;
            const text=prompt("Текст проекта (HTML)")||"";
            try{const b=await api("/bills",{method:"POST",body:{title,text_rich:text}});go("/bills/"+b.id);}
            catch(e){notify(e.message);}
        };
        main.append(create);
    }
    const bills=await api("/bills");
    if(!bills.length)main.append(el("p","empty","Законопроектов пока нет."));
    bills.forEach(b=>{
        const a=el("a","search-result");a.href="/bills/"+b.id;a.dataset.link="";
        a.append(el("span","",b.status+" · "+friendlyDate(b.created_at)),
                 el("strong","",b.title));main.append(a);
    });
}
async function billPage(id) {
    const bill=await api("/bills/"+id);
    main.append(breadcrumb([{label:"Законопроекты",href:"/bills"},{label:bill.title}]));
    pageHeading("Законодательный процесс",bill.title,"Текущее состояние: "+bill.status);
    const body=el("article","article-card");
    const html=el("div","article-text");html.innerHTML=bill.text_rich;body.append(html);main.append(body);
    if (hasRole("legislator")) {
        const opts={project:"discussion",discussion:"voting",voting:"decision",decision:"published"};
        if(opts[bill.status]){
            const btn=el("button","button primary","Следующий этап: "+opts[bill.status]);
            btn.onclick=async()=>{
                const extra={status:opts[bill.status],reason:prompt("Основание перехода")||""};
                if(extra.status==="published")extra.publication_id=prompt("ID опубликованного акта законодательного органа");
                try{await api("/bills/"+id+"/advance",{method:"POST",body:extra});go("/bills/"+id);}
                catch(e){notify(e.message);}
            };
            main.append(btn);
        }
        if(bill.status==="discussion"){
            const btn=el("button","button ghost","Добавить обсуждение");
            btn.onclick=async()=>{
                const value=prompt("Комментарий");
                if(!value)return;
                try{await api("/bills/"+id+"/discussions",{method:"POST",body:{body:value}});go("/bills/"+id);}
                catch(e){notify(e.message);}
            };main.append(btn);
        }
        if(bill.status==="voting"){
            const btn=el("button","button ghost","Открыть голосование");
            btn.onclick=async()=>{
                try{await api("/bills/"+id+"/votes",{method:"POST",body:{}});go("/bills/"+id);}
                catch(e){notify(e.message);}
            };main.append(btn);
        }
    }
    const section=el("section","panel");section.append(el("h2","","Хронология"));
    const ul=el("ul","simple-list");
    bill.events.forEach(event=>ul.append(el("li","",friendlyDate(event.created_at)+" — "+event.event_type+" · "+event.body)));
    section.append(ul);main.append(section);
    if(bill.votes.length){
        const votes=el("section","panel");votes.append(el("h2","","Голосования"));
        bill.votes.forEach(v=>{
            const item=el("div","simple-list");
            item.append(el("p","",friendlyDate(v.created_at)+" · "+v.result));
            if(hasRole("legislator") && v.result==="pending"){
                ["yes","no","abstain"].forEach(choice=>{
                    const btn=el("button","button ghost",choice);
                    btn.onclick=async()=>{try{await api("/votes/"+v.id+"/choice",{method:"POST",body:{choice}});notify("Голос учтён.");}catch(e){notify(e.message);}};
                    item.append(btn);
                });
                const close=el("button","button ghost","Зафиксировать итог");
                close.onclick=async()=>{
                    const result=prompt("Итог: adopted или rejected");
                    try{await api("/votes/"+v.id+"/close",{method:"POST",body:{result}});go("/bills/"+id);}catch(e){notify(e.message);}
                };item.append(close);
            }
            votes.append(item);
        });
        main.append(votes);
    }
}
async function reviewsPage() {
    pageHeading("Административная проверка", "Официальные изменения",
        "Внешняя публикация не меняет действующую статью до подтверждения редактором.");
    const pending=await api("/editorial-reviews");
    if(!pending.length)main.append(el("p","empty","Новых публикаций для проверки нет."));
    pending.forEach(r=>{
        const card=el("section","panel");
        card.append(el("h2","",r.publication_title),el("p","muted",r.issuer+" · "+friendlyDate(r.published_at)));
        const a=el("a","button ghost","Открыть источник");a.href="/publications/"+r.publication_id;a.dataset.link="";
        card.append(a);
        const button=el("button","button primary","Проверить статью");
        button.onclick=()=>reviewComposer(r);
        card.append(button);main.append(card);
    });
}
async function reviewComposer(r) {
    try {
        const a=await api("/articles/"+r.article_id);
        const session=crypto.randomUUID();
        const lock=await api("/articles/"+a.id+"/edit-lock",{method:"POST",body:{editor_session_id:session}});
        const newText=prompt("Проверенный консолидированный текст статьи (HTML)",a.text_rich);
        if(newText===null){
            await api("/articles/"+a.id+"/edit-lock",{method:"DELETE",body:{editor_session_id:session,token:lock.token}});
            return;
        }
        await api("/editorial-reviews/"+r.id+"/confirm",{method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()},
          body:{confirm:true,base_revision_id:a.revision_id,lock_token:lock.token,editor_session_id:session,
                consolidated_text_rich:newText,reason:"Редакционная проверка опубликованного акта"}});
        await api("/articles/"+a.id+"/edit-lock",{method:"DELETE",body:{editor_session_id:session,token:lock.token}});
        notify("Официальное изменение подтверждено.");go("/reviews");
    } catch(e){notify(e.message);}
}
async function publicationComposer(portalId) {
    const title=prompt("Название документа");
    if(!title)return;
    const issuer=prompt("Наименование органа");
    const type=prompt("Тип акта");
    const text_rich=prompt("Текст публикации (HTML)")||"";
    const jurisdiction_id=portalId==="los-santos"?"city":"state";
    const related_article_id=prompt("ID статьи для последующей проверки (необязательно)")||null;
    try {
        const d=await api("/portal-publications",{method:"POST",body:{portal_id:portalId,jurisdiction_id,
            title,issuer,document_type:type,text_rich,related_article_id}});
        const okay=confirm("Опубликовать подготовленный документ? Публикация неизменяема без отдельной процедуры.");
        if(!okay){notify("Сохранён черновик документа.");return;}
        await api("/portal-publications/"+d.id+"/publish",{method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()},
             body:{confirm:true}});
        notify("Документ опубликован.");go("/portals/"+portalId);
    }catch(e){notify(e.message);}
}
async function renderRoute(path, options) {
    const serial = ++lastNavSerial;
    const pathname = new URL(path, location.origin).pathname;
    // Push navigation starts at the new page heading. Popstate restores the saved position.
    if (!options.restore) window.scrollTo(0, 0);
    clearReading();
    main.replaceChildren();
    codeNav.replaceChildren();
    refreshGlobalNav();
    if (!currentUser) {
        if (authMode==="oidc" ? !oidcToken : !role) {
            if (pathname === "/" || pathname === "/laws") {
                await home(true);
            } else {
                pageHeading("Доступ к правовым ресурсам", "Требуется вход",
                    authMode==="oidc" ? "Войдите с помощью внешнего сервиса авторизации." :
                    "Для поиска и просмотра правовых текстов выберите роль в верхней панели.");
                const button = el("button", "button primary", authMode==="oidc" ? "Войти" : "Выбрать роль");
                button.onclick = () => authMode==="oidc" && authButton ?
                    authButton.click() : roleSelect.focus();
                main.append(button);
            }
            return;
        }
        await loadContext();
    }
    if (serial !== lastNavSerial) return;
    refreshGlobalNav();
    const parts = pathname.split("/").filter(Boolean);
    if (!parts.length) await home();
    else if (parts[0] === "laws") {
        const jurisdiction = new URLSearchParams(location.search).get("jurisdiction") || "all";
        await lawCatalog(jurisdiction);
    }
    else if (parts[0] === "jurisdictions" && parts[1]) await jurisdiction(parts[1]);
    else if (parts[0] === "acts" && parts[1]) await libraryActPage(parts[1]);
    else if (parts[0] === "acts") await libraryActsPage();
    else if (parts[0] === "codes" && parts[1]) {
        await openSection(parts[1], parts[2] === "nodes" ? parts[3] : null, null, options, serial);
    }
    else if (parts[0] === "articles" && parts[1]) {
        const article = await api("/articles/" + parts[1]);
        if(article.status!=="active") await archivedArticlePage(article);
        else await openSection(article.code_id, article.parent_id, article.id, options, serial);
    }
    else if (parts[0] === "search") await searchPage();
    else if (parts[0] === "favorites") await favoritesPage();
    else if (parts[0] === "notifications") await notificationsPage();
    else if (parts[0] === "precedents") await precedentPage();
    else if (parts[0] === "portals" && parts[1]) await portalPage(parts[1]);
    else if (parts[0] === "publications" && parts[1]) await publicationPage(parts[1]);
    else if (parts[0] === "bills" && parts[1]) await billPage(parts[1]);
    else if (parts[0] === "bills") await billListPage();
    else if (parts[0] === "reviews") await reviewsPage();
    else if (parts[0] === "admin") await adminPage(parts[1] || "overview");
    else if (parts[0] === "revisions" && parts[1]) await revisionsPage(parts[1]);
    else throw Error("Страница не найдена.");
    if (options.restore && !reading) {
        window.scrollTo(0, options.scrollY ??
            Number(sessionStorage.getItem("sas-scroll:" + pathname) || 0));
    } else if (!reading) window.scrollTo(0, 0);
}
let editorCategoryDefinitions = [];
let editorCustomValues = {};
let visualEditorActive = false;
const visualEditor = document.getElementById("richEditor");
const rawEditor = editorForm.elements.text_rich;
function switchEditorMode(toVisual) {
    if(toVisual === visualEditorActive)return;
    if(toVisual) visualEditor.innerHTML = rawEditor.value;
    else rawEditor.value = visualEditor.innerHTML;
    visualEditorActive = toVisual;
    document.getElementById("visualEditorRegion").hidden = !toVisual;
    document.getElementById("rawTextLabel").hidden = toVisual;
    document.getElementById("toggleRichEditor").textContent =
        toVisual ? "Редактировать HTML" : "Визуальный редактор";
    document.getElementById("editorModeStatus").textContent =
        toVisual ? "Визуальное форматирование" : "Режим исходной разметки HTML";
}
document.getElementById("toggleRichEditor").onclick = () => switchEditorMode(!visualEditorActive);
document.querySelectorAll("[data-format]").forEach(button => {
    button.addEventListener("mousedown", event => event.preventDefault());
    button.addEventListener("click", () => {
        visualEditor.focus();
        document.execCommand(button.dataset.format, false, button.dataset.value || null);
        editorDirty = true;
    });
});
function captureEditorCustomValues() {
    const values={};
    document.querySelectorAll("#editorCustomFields [data-custom-field]").forEach(row=>{
        const id=row.dataset.customField, kind=row.dataset.kind;
        let value=null;
        if(kind==="range"){
            const [lo,hi]=[row.querySelector("[data-bound=lo]"),row.querySelector("[data-bound=hi]")];
            if(lo.value.trim()!=="" && hi.value.trim()!=="")value=[Number(lo.value),Number(hi.value)];
        }else{
            const control=row.querySelector("[data-custom-control]");
            if(kind==="boolean") value=control.value===""?null:control.value==="true";
            else if(kind==="number") value=control.value.trim()===""?null:Number(control.value);
            else if(kind==="choice" && control.multiple) {
                value=Array.from(control.selectedOptions).map(option=>option.value);
                if(!value.length)value=null;
            }else value=control.value.trim()===""?null:control.value;
        }
        if(value!==null)values[id]=value;
    });
    return values;
}
function renderEditorCustomFields() {
    const area=document.getElementById("editorCustomFields");
    const saved={...editorCustomValues,...captureEditorCustomValues()};
    editorCustomValues=saved;
    area.replaceChildren();
    const selected=new Set([...document.querySelectorAll("#editorCategories input:checked")].map(x=>x.value));
    editorCategoryDefinitions.filter(c=>selected.has(c.id)).forEach(cat=>{
        (cat.fields||[]).forEach(field=>{
            const value=saved[field.id];
            const row=el("div","custom-field");
            row.dataset.customField=field.id;
            row.dataset.kind=field.field_type;
            row.append(el("label","custom-field-title",cat.name+" · "+field.name));
            let control;
            if(field.field_type==="range"){
                const range=el("div","form-grid");
                ["lo","hi"].forEach((bound,i)=>{
                    const input=el("input");input.type="number";input.step="any";
                    input.dataset.bound=bound;
                    input.placeholder=i?"Максимум":"Минимум";
                    if(Array.isArray(value) && value[i]!==undefined)input.value=value[i];
                    range.append(input);
                });
                row.append(range);
            }else if(field.field_type==="choice"){
                const options=(field.options||[]).filter(o=>o.active ||
                    (Array.isArray(value)?value.includes(o.id):value===o.id));
                control=el("select");control.multiple=field.selection_mode==="multiple";
                if(!control.multiple)control.append(new Option("Не указано",""));
                options.forEach(o=>{
                    const option=new Option(o.name,o.id);
                    option.selected=Array.isArray(value)?value.includes(o.id):value===o.id;
                    control.append(option);
                });
                if(control.multiple)control.size=Math.min(Math.max(options.length,2),5);
            }else if(field.field_type==="boolean"){
                control=adminSelect([["Да","true"],["Нет","false"]],
                    value===true?"true":value===false?"false":"","Не указано");
            }else if(field.field_type==="rich_text"){
                control=el("textarea");control.rows=3;control.value=value||"";
            }else{
                control=el("input");control.type=field.field_type==="number"?"number":"text";
                if(field.field_type==="number")control.step="any";
                control.value=value??"";
            }
            if(control){
                control.dataset.customControl="";
                control.setAttribute("aria-label",cat.name+" · "+field.name);
                row.append(control);
            }
            area.append(row);
        });
    });
    if(!area.children.length)area.append(el("p","muted",
        "Выберите категорию с дополнительными полями; все такие поля необязательны."));
}

function collectEditorBody() {
    const values=Object.fromEntries(new FormData(editorForm));
    const categories=Array.from(document.querySelectorAll('#editorCategories input:checked')).map(x=>x.value);
    const sanctions={};
    ["prison_min_months","prison_max_months","fine_min_usd","fine_max_usd"].forEach(key=>{
        sanctions[key]=values[key]===""?null:Number(values[key]);
    });
    const custom_values=captureEditorCustomValues();
    const rich=visualEditorActive ? visualEditor.innerHTML : values.text_rich;
    return {number:values.number,title:values.title,text_rich:rich,categories,custom_values,
        parent_id:values.parent_id || null,criminal_class:values.criminal_class||null,
        sanctions,reason:values.reason||"Редакторская правка"};
}
async function openEditor(article, codeId, nodeId) {
    if(!isAdmin())return;
    const state={article,codeId,nodeId,sessionId:crypto.randomUUID(),token:null,baseRevision:article&&article.revision_id};
    try{
        if(article){
            const lock=await api("/articles/"+article.id+"/edit-lock",{method:"POST",
                 body:{editor_session_id:state.sessionId}});
            state.token=lock.token;
        }
        editorState=state;editorDirty=false;
        editorForm.reset();
        editorForm.elements.number.value=article?article.number:"";
        editorForm.elements.title.value=article?article.title:"";
        switchEditorMode(false);
        editorForm.elements.text_rich.value=article?article.text_rich:"";
        visualEditor.innerHTML=article?article.text_rich:"";
        editorCustomValues={...(article&&article.custom_values||{})};
        editorForm.elements.criminal_class.value=article?article.criminal_class||"":"";
        // Admin placement needs all currently valid parents; the normal
        // reader uses paginated metadata and never downloads this full list.
        const hierarchy=await api("/codes/"+codeId+"/tree");
        const locationSelect=document.getElementById("articleParentSelect");
        locationSelect.replaceChildren(new Option("Непосредственно в кодексе (без раздела)", ""));
        const labels=new Map();
        function nodeLabel(n) {
            if(labels.has(n.id))return labels.get(n.id);
            const parent=n.parent_id&&hierarchy.nodes.find(node=>node.id===n.parent_id);
            const type=hierarchy.types.find(t=>t.id===n.type_id);
            const own=(n.number?(type?type.name+" ":"")+n.number+" · ":"")+displayLegalTitle(n.title);
            const label=parent?nodeLabel(parent)+" / "+own:own;
            labels.set(n.id,label);
            return label;
        }
        hierarchy.nodes.forEach(n=>locationSelect.append(new Option(nodeLabel(n),n.id)));
        locationSelect.value=article?article.parent_id||"":nodeId||"";
        const data=article&&article.sanctions||{};
        Object.keys(data).forEach(key=>{editorForm.elements[key].value=data[key]===null?"":data[key];});
        document.getElementById("editorTitle").textContent=article?"Редактирование статьи "+article.number:"Создание статьи";
        document.getElementById("editorStatus").textContent="";
        document.getElementById("saveEditor").disabled=false;
        document.getElementById("reacquireLock").hidden=true;
        const categories=await api("/categories?code_id="+encodeURIComponent(codeId));
        const box=document.getElementById("editorCategories");box.replaceChildren();
        editorCategoryDefinitions=categories;
        categories.forEach(cat=>{
            const label=el("label");
            const check=el("input");check.type="checkbox";check.name="category";check.value=cat.id;
            check.checked=Boolean(article&&article.categories&&article.categories.includes(cat.id));
            check.addEventListener("change",renderEditorCustomFields);
            label.append(check,document.createTextNode(cat.name));box.append(label);
        });
        document.getElementById("editorCustomFields").replaceChildren();
        renderEditorCustomFields();
        dialog.showModal();
        if(state.token)heartbeatTimer=setInterval(()=>heartbeatEditor(state),60000);
    }catch(e){
        if(article && e.code==="EDIT_LOCK_CONFLICT" &&
           confirm("Статья уже заблокирована для редактирования. Попробовать принудительно снять свою блокировку с другого устройства? Чужую блокировку снять нельзя.")){
            try{
                await api("/articles/"+article.id+"/edit-lock/revoke",
                    {method:"POST",body:{confirm:true}});
                notify("Собственная блокировка отозвана.");
                await openEditor(article,codeId,nodeId);
                return;
            }catch(error){notify(error.message);}
        }else notify(e.message);
    }
}
async function heartbeatEditor(state) {
    if(editorState!==state||!state.token)return;
    try{await api("/articles/"+state.article.id+"/edit-lock/heartbeat",
        {method:"POST",body:{editor_session_id:state.sessionId,token:state.token}});}
    catch(e){document.getElementById("editorStatus").textContent="Блокировка утрачена. Несохранённые изменения сохранены в форме.";
        document.getElementById("saveEditor").disabled=true;document.getElementById("reacquireLock").hidden=false;
        clearInterval(heartbeatTimer);}
}
document.getElementById("reacquireLock").onclick=async()=>{
    if(!editorState||!editorState.article)return;
    try{const lock=await api("/articles/"+editorState.article.id+"/edit-lock",{method:"POST",body:{editor_session_id:editorState.sessionId}});
        editorState.token=lock.token;document.getElementById("saveEditor").disabled=false;
        document.getElementById("reacquireLock").hidden=true;document.getElementById("editorStatus").textContent="Блокировка восстановлена.";
        clearInterval(heartbeatTimer);heartbeatTimer=setInterval(()=>heartbeatEditor(editorState),60000);
    }catch(e){notify(e.message);}
};
editorForm.addEventListener("input",()=>{editorDirty=true;});
editorForm.addEventListener("submit",async event=>{
    event.preventDefault();
    if(!editorState)return;
    const state=editorState;
    let body;
    try{
        body=collectEditorBody();
        for(const [min,max] of [["prison_min_months","prison_max_months"],
                                ["fine_min_usd","fine_max_usd"]]){
            const a=body.sanctions[min],b=body.sanctions[max];
            if(a!==null && (!Number.isInteger(a)||a<0) || b!==null && (!Number.isInteger(b)||b<0))
                throw Error("Санкции должны быть целыми неотрицательными числами.");
            if(a!==null && b!==null && a>b)
                throw Error("Минимальное значение санкции не может превышать максимальное.");
        }
        if(body.categories.includes("accusation")&&!body.criminal_class)
            throw Error("Для категории «Обвинение» требуется уголовно-правовой класс.");
    }catch(error){
        document.getElementById("editorStatus").textContent=error.message;
        return;
    }
    const isNew=!state.article;
    const oldParent=state.article ? state.article.parent_id : state.nodeId;
    try{
        let saved;
        if(state.article) {
            saved=await api("/articles/"+state.article.id+"/revisions",{method:"POST",
                body:Object.assign(body,{base_revision_id:state.baseRevision,lock_token:state.token,
                     editor_session_id:state.sessionId})});
            state.baseRevision=saved.revision_id;
            state.article=saved;
        } else {
            saved=await api("/articles",{method:"POST",body:Object.assign(body,{code_id:state.codeId})});
            state.article=saved;state.baseRevision=saved.revision_id;
        }
        editorDirty=false;
        if(isNew || oldParent !== saved.parent_id) {
            await exitEditor();
            notify(isNew ? "Статья создана." : "Статья перемещена в выбранный раздел.");
            go("/articles/"+saved.id);
            return;
        }
        const previous=document.getElementById("card-"+saved.id);
        const updated=createArticleCard(saved);
        if(previous) {
            if(activeObserver)activeObserver.unobserve(previous);
            previous.replaceWith(updated);
            if(activeObserver)activeObserver.observe(updated);
        }
        if(reading && treeData) {
            const code=allCodes.find(c=>c.id===reading.codeId);
            if(code)renderCodeNav(code,reading.nodeId,false);
        }
        document.getElementById("editorStatus").textContent="Редакция сохранена.";
        notify("Редакция успешно опубликована.");
    }catch(e){document.getElementById("editorStatus").textContent=e.message;
        if(e.code==="EDIT_LOCK_CONFLICT"){document.getElementById("saveEditor").disabled=true;
            document.getElementById("reacquireLock").hidden=false;}}
});
async function exitEditor() {
    if(!editorState)return;
    if(editorDirty && !confirm("Есть несохранённые изменения. Выйти без сохранения?"))return;
    const state=editorState;editorState=null;editorDirty=false;clearInterval(heartbeatTimer);
    if(state.token&&state.article){
        try{await api("/articles/"+state.article.id+"/edit-lock",{method:"DELETE",
           body:{editor_session_id:state.sessionId,token:state.token}});}
        catch(e){notify("Не удалось освободить блокировку; она истечёт автоматически.");}
    }
    dialog.close();
}
document.getElementById("closeEditor").onclick=()=>exitEditor();
dialog.addEventListener("cancel",event=>{event.preventDefault();exitEditor();});
window.addEventListener("beforeunload",event=>{if(editorDirty){event.preventDefault();event.returnValue="";}});
function toBase64Url(bytes) {
    let binary="";
    bytes.forEach(byte=>binary+=String.fromCharCode(byte));
    return btoa(binary).replace(/\+/g,"-").replace(/\//g,"_").replace(/=+$/,"");
}
async function beginExternalLogin() {
    if(!oidcConfig || oidcConfig.mode!=="oidc")return;
    const verifier=toBase64Url(crypto.getRandomValues(new Uint8Array(32)));
    const state=toBase64Url(crypto.getRandomValues(new Uint8Array(24)));
    const hashed=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(verifier));
    const challenge=toBase64Url(new Uint8Array(hashed));
    sessionStorage.setItem("sas-oidc-verifier",verifier);
    sessionStorage.setItem("sas-oidc-state",state);
    sessionStorage.setItem("sas-oidc-return",location.pathname+location.search);
    const destination=new URL(oidcConfig.authorization_url);
    destination.searchParams.set("response_type","code");
    destination.searchParams.set("client_id",oidcConfig.client_id);
    destination.searchParams.set("redirect_uri",oidcConfig.redirect_uri);
    destination.searchParams.set("scope",oidcConfig.scope);
    destination.searchParams.set("state",state);
    destination.searchParams.set("code_challenge",challenge);
    destination.searchParams.set("code_challenge_method","S256");
    location.assign(destination.toString());
}
async function setupExternalLogin(config) {
    oidcConfig=config;
    authMode=config.mode;
    if(config.mode!=="oidc")return;
    roleSelect.hidden=true;
    const label=document.querySelector('label[for="roleSelect"]');
    if(label)label.hidden=true;
    authButton=el("button","button ghost",oidcToken?"Выйти":"Войти");
    authButton.type="button";authButton.id="externalAuthButton";
    authButton.onclick=()=>{
        if(oidcToken){
            sessionStorage.removeItem("sas-oidc-token");
            oidcToken="";
            currentUser=null;
            location.assign("/");
        }else beginExternalLogin().catch(error=>notify(error.message));
    };
    document.querySelector(".header-actions").insertBefore(authButton,
        document.getElementById("notifyButton"));
    const url=new URL(location.href);
    if(url.searchParams.has("error")){
        const error=url.searchParams.get("error_description")||"Внешний провайдер отклонил вход.";
        history.replaceState({scrollY:0},"",url.pathname);
        throw Error(error);
    }
    if(url.searchParams.has("code")){
        const expected=sessionStorage.getItem("sas-oidc-state");
        const verifier=sessionStorage.getItem("sas-oidc-verifier");
        const code=url.searchParams.get("code");
        const state=url.searchParams.get("state");
        const destination=sessionStorage.getItem("sas-oidc-return")||"/";
        sessionStorage.removeItem("sas-oidc-state");
        sessionStorage.removeItem("sas-oidc-verifier");
        sessionStorage.removeItem("sas-oidc-return");
        history.replaceState({scrollY:0},"",url.pathname);
        if(!state || !expected || state!==expected || !verifier){
            throw Error("Проверка безопасности внешнего входа не пройдена.");
        }
        const result=await api("/auth/token",{method:"POST",
            body:{code,code_verifier:verifier}});
        oidcToken=result.access_token;
        sessionStorage.setItem("sas-oidc-token",oidcToken);
        authButton.textContent="Выйти";
        history.replaceState({scrollY:0},"",destination.startsWith("/")?destination:"/");
    }
}
async function bootstrapLibrary() {
    try {
        const response=await fetch("/api/v1/auth/config",{headers:{Accept:"application/json"}});
        const data=await response.json();
        if(!response.ok)throw Error(data.error&&data.error.message||"Ошибка настройки авторизации.");
        await setupExternalLogin(data);
        await renderRoute(location.pathname,{restore:true});
    }catch(error){showError(error);}
}
bootstrapLibrary();

