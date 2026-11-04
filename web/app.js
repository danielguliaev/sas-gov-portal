/* Browser client for the demonstrational legislative network. No live refresh of reading content. */
"use strict";
const main = document.getElementById("main");
const sidebar = document.getElementById("sidebar");
const layout = document.getElementById("layout");
const nav = document.getElementById("globalNav");
const codeNav = document.getElementById("codeNav");
const roleSelect = document.getElementById("roleSelect");
const dialog = document.getElementById("editorDialog");
const editorForm = document.getElementById("articleForm");
const toastElement = document.getElementById("toast");
const role = sessionStorage.getItem("sas-demo-role") || "";
roleSelect.value = role;
let currentUser = null;
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
    if (role) headers["X-Demo-User"] = role;
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
function storePosition() {
    if (!reading) return;
    sessionStorage.setItem("sas-scroll:" + location.pathname, String(window.scrollY));
    if (history.state) history.replaceState(Object.assign({}, history.state, {scrollY:window.scrollY}), "", location.href);
}
let scrollSavePending = false;
window.addEventListener("scroll", () => {
    if (scrollSavePending) return;
    scrollSavePending = true;
    requestAnimationFrame(() => { scrollSavePending = false; storePosition(); });
}, {passive:true});
function go(path, push) {
    if (push !== false) {
        storePosition();
        history.pushState({scrollY:0}, "", path);
    }
    layout.classList.remove("menu-open");
    renderRoute(path, {restore:push === false}).catch(err => showError(err));
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
document.getElementById("menuToggle").addEventListener("click", () => layout.classList.toggle("menu-open"));
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
        const p = el("p", "notice", "Для чтения полного текста требуется вход. В локальной сборке выберите тестовую роль вверху страницы.");
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
        navItem("Главная", "/", "⌂"),
        navItem("Поиск законодательства", "/search", "⌕"),
        navItem("Судебные прецеденты", "/precedents", "§")
    );
    if (isLawyer()) nav.append(navItem("Моё избранное", "/favorites", "☆"));
    if (hasRole("library_editor") || isAdmin()) nav.append(navItem("Редакционная проверка", "/reviews", "✓"));
    const div = el("div", "sidebar-separator");
    nav.append(div);
    allPortals.filter(p => p.id !== "library").forEach(p => nav.append(navItem(p.name, "/portals/" + p.id, "⌑")));
}
async function loadContext() {
    currentUser = await api("/me");
    const [codes, portals] = await Promise.all([api("/codes"), api("/portals")]);
    allCodes = codes; allPortals = portals;
    refreshGlobalNav();
    const prefs = await api("/me/preferences");
    setFont(prefs.font_scale);
}
async function home() {
    pageHeading("Государственная правовая информационная система", "Законодательство штата Сан-Андреас",
        "Консолидированные правовые тексты, законодательные документы и опубликованные судебные решения в единой сети государственных порталов.");
    const panel = el("section", "panel");
    panel.append(el("div", "eyebrow", "Законодательная библиотека"),
                 el("h2", "", "Выберите уровень законодательства"),
                 el("p", "muted", "Разделение источников по юрисдикциям. Содержимое примеров не является официальным правом."));
    const grid = el("div", "cards-grid");
    [
        ["state","Штат Сан-Андреас","Нормативные правовые акты и кодексы штата"],
        ["county","Округ","Правовые источники окружного уровня"],
        ["city","Город Лос-Сантос","Муниципальные постановления и кодексы"]
    ].forEach(item => {
        const a = el("a", "portal-card");
        a.href = "/jurisdictions/" + item[0]; a.dataset.link = "";
        a.append(el("span", "portal-icon", "§"), el("strong", "", item[1]), el("p", "", item[2]));
        grid.append(a);
    });
    panel.append(grid);
    main.append(panel);
    const heading = el("div", "section-heading");
    heading.append(el("h2", "", "Государственные порталы"));
    main.append(heading);
    const portalsGrid = el("div", "cards-grid");
    allPortals.filter(p => p.id !== "library").forEach(p => {
        const a = el("a", "portal-card");
        a.href = "/portals/" + p.id; a.dataset.link = "";
        a.append(el("span", "portal-icon", "SA"), el("strong", "", p.name),
                 el("p", "", "Официальные публикации и информационные документы."));
        portalsGrid.append(a);
    });
    main.append(portalsGrid);
}
async function jurisdiction(id) {
    const labels = {state:"Штат Сан-Андреас",county:"Округ",city:"Город Лос-Сантос"};
    const label = labels[id];
    if (!label) throw Error("Неизвестная юрисдикция.");
    main.append(breadcrumb([{label:"Главная",href:"/"},{label}]));
    pageHeading("Нормативные источники", label, "Выберите кодекс для просмотра иерархии и действующих статей.");
    const cards = el("div", "cards-grid");
    allCodes.filter(c => c.jurisdiction_id === id).forEach(code => {
        const a = el("a", "portal-card");
        a.href = "/codes/" + code.id; a.dataset.link = "";
        a.append(el("span", "portal-icon", "§"), el("strong", "", code.name),
                 el("p", "", code.is_demo ? "Демонстрационный кодекс" : "Действующие опубликованные тексты"));
        cards.append(a);
    });
    if (!cards.children.length) main.append(el("div", "empty", "В этой юрисдикции кодексы пока не опубликованы."));
    else main.append(cards);
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
function renderCodeNav(code, selectedNode, directTarget) {
    codeNav.replaceChildren();
    const heading = el("div", "tree-title", code.name);
    codeNav.append(heading);
    const root = el("div");
    const rootLink = navItem("Статьи корня кодекса", "/codes/" + code.id, "§");
    if (selectedNode === null) rootLink.classList.add("active");
    root.append(rootLink);
    const state = treeStorage(code.id);
    const ancestors = new Set();
    if (directTarget && selectedNode) {
        let parent = selectedNode;
        while (parent) {
            ancestors.add(parent);
            const n = treeData.nodes.find(x => x.id === parent);
            parent = n && n.parent_id;
        }
    }
    function entry(n, depth, parentContainer) {
        const wrap = el("div");
        const line = el("div", "tree-entry");
        const children = directChildren(treeData.nodes, n.id);
        const toggle = el("button", "tree-toggle", children.length ? "▸" : "·");
        toggle.type = "button";
        toggle.disabled = !children.length;
        toggle.setAttribute("aria-label", "Развернуть " + n.title);
        const dest = "/codes/" + code.id + "/nodes/" + n.id;
        const link = el("a", "nav-item", n.number + " · " + n.title);
        link.href = dest; link.dataset.link = "";
        link.dataset.nodeId = n.id;
        if (selectedNode === n.id) link.classList.add("active");
        line.append(toggle, link); wrap.append(line);
        const branch = el("div", "tree-branch");
        let opened = state.expanded.includes(n.id) ||
                     (ancestors.has(n.id) && (directTarget || !state.suppressed.includes(n.id)));
        function update() {
            branch.hidden = !opened;
            toggle.textContent = opened ? "▾" : "▸";
            toggle.setAttribute("aria-expanded", String(opened));
        }
        update();
        toggle.onclick = () => {
            opened = !opened;
            state.expanded = state.expanded.filter(x => x !== n.id);
            state.suppressed = state.suppressed.filter(x => x !== n.id);
            (opened ? state.expanded : state.suppressed).push(n.id);
            saveTreeStorage(code.id, state);
            update();
        };
        children.forEach(child => entry(child, depth + 1, branch));
        if (children.length) wrap.append(branch);
        parentContainer.append(wrap);
    }
    directChildren(treeData.nodes, null).forEach(n => entry(n, 0, root));
    codeNav.append(root);
}
function highlightSidebar(articleId) {
    if (!reading) return;
    const card = document.getElementById("card-" + articleId);
    if (!card) return;
    document.querySelectorAll(".tree-entry .nav-item").forEach(e => e.classList.remove("reading-active"));
    const link = document.querySelector('[data-node-id="' + (reading.nodeId || "") + '"]');
    if (link) {
        link.classList.add("active");
        link.scrollIntoView({block:"nearest"});
    }
}
function createArticleCard(a) {
    const card = el("article", "article-card");
    card.id = "card-" + a.id;
    card.dataset.articleId = a.id;
    const top = el("div", "article-top");
    const titles = el("div");
    titles.append(el("div", "article-number", "СТАТЬЯ " + a.number), el("h2", "", a.title));
    top.append(titles);
    if (a.is_demo) top.append(el("span", "tag", "ДЕМО"));
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
    const extra = isLawyer() && a.categories && a.categories.length;
    if (extra) {
        const details = el("details", "detail-block");
        details.append(el("summary", "", "Дополнительные юридические сведения"));
        const dl = el("dl");
        const fields = [
            ["Категории", a.categories.join(", ")],
            ["Уголовно-правовой класс", text(a.criminal_class)],
            ["Лишение свободы (месяцы)", text(a.sanctions.prison_min_months) + " — " + text(a.sanctions.prison_max_months)],
            ["Штраф (USD)", text(a.sanctions.fine_min_usd) + " — " + text(a.sanctions.fine_max_usd)]
        ];
        fields.forEach(pair => dl.append(el("dt", "", pair[0]),el("dd", "", pair[1])));
        details.append(dl);
        card.append(details);
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
    const actions = el("div", "article-actions");
    const open = el("a", "button ghost", "Постоянная ссылка");
    open.href = "/articles/" + a.id; open.dataset.link = "";
    actions.append(open);
    const copy = el("button", "button subtle", "Копировать цитату");
    copy.onclick = () => {
        navigator.clipboard.writeText("Статья " + a.number + " «" + a.title + "». " +
            location.origin + "/articles/" + a.id).then(() => notify("Юридическая ссылка скопирована."))
            .catch(() => notify("Не удалось скопировать ссылку."));
    };
    actions.append(copy);
    if (isLawyer()) {
        const favorite = el("button", "button subtle", "☆ В избранное");
        favorite.onclick = async () => {
            try {
                await api("/me/favorites/" + a.id, {method:"PUT"});
                favorite.textContent = "★ В избранном";
                notify("Статья добавлена в избранное.");
            } catch (error) { notify(error.message); }
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
    }
    card.append(actions);
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
        state.loading = false;
        state.status.replaceChildren();
        if (!state.hasMore && !state.cards.children.length) state.status.append(el("p", "empty", "Статей пока нет."));
        if (state.targetId) {
            const target = document.getElementById("card-" + state.targetId);
            if (target) {
                target.classList.add("target");
                target.scrollIntoView({block:"start"});
                setTimeout(() => target.classList.remove("target"), 2800);
                state.targetId = null;
            } else if (state.hasMore) {
                await loadNext();
            } else notify("Целевая статья отсутствует в текущем разделе.");
        } else if (state.restoreScroll !== null) {
            if (document.body.scrollHeight >= state.restoreScroll + window.innerHeight ||
                !state.hasMore) {
                window.scrollTo(0, state.restoreScroll);
                state.restoreScroll = null;
            } else {
                await loadNext();
            }
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
async function openSection(codeId, nodeId, targetId, options, serial) {
    const code = allCodes.find(c => c.id === codeId);
    if (!code) throw Error("Кодекс не найден.");
    treeData = await api("/codes/" + codeId + "/tree");
    if (serial !== lastNavSerial) return;
    renderCodeNav(code, nodeId, !!targetId);
    main.append(breadcrumb([{label:"Главная",href:"/"},{label:code.name,href:"/codes/"+code.id},
        {label:nodeId ? (treeData.nodes.find(n=>n.id===nodeId) || {}).title || "Раздел" : "Статьи кодекса"}]));
    pageHeading("Законодательная библиотека", code.name,
        nodeId ? (treeData.nodes.find(n=>n.id===nodeId) || {}).title : "Статьи в корне кодекса");
    const heading = el("div", "section-heading");
    heading.append(el("h2", "", "Текст законодательства"));
    if (isAdmin()) {
        const create = el("button", "button primary", "Создать статью");
        create.onclick = () => openEditor(null, codeId, nodeId);
        heading.append(create);
    }
    main.append(heading);
    const cards = el("section");
    const status = el("div", "load-status");
    main.append(cards, status);
    reading = {codeId,nodeId,cards,status,cursor:null,hasMore:true,loading:false,
               targetId:targetId || null,restoreScroll: targetId ? null : (
                   options.restore ? (options.scrollY ?? Number(sessionStorage.getItem("sas-scroll:" + location.pathname) || 0)) : null)};
    activeObserver = new IntersectionObserver(entries => {
        const visible = entries.filter(e => e.isIntersecting).sort((a,b) => b.intersectionRatio - a.intersectionRatio);
        if (visible.length) {
            document.querySelectorAll(".article-card.reading").forEach(c => c.classList.remove("reading"));
            visible[0].target.classList.add("reading");
            highlightSidebar(visible[0].target.dataset.articleId);
        }
    }, {rootMargin:"-120px 0px -65% 0px",threshold:[0,.3,.6]});
    loadObserver = new IntersectionObserver(entries => {
        if (entries[0].isIntersecting && reading && reading.hasMore) loadNext();
    }, {rootMargin:"400px 0px"});
    loadObserver.observe(status);
    await loadNext();
}
async function searchPage() {
    pageHeading("Поиск законодательных текстов", "Поиск по законодательству",
        "Найдите статью по номеру, названию или фрагменту текста.");
    const form = el("form", "search-bar");
    const q = el("input");
    q.name = "query"; q.placeholder = "Название, номер или фрагмент статьи"; q.required = true;
    q.setAttribute("aria-label", "Текст поиска");
    const submit = el("button", "button primary", "Найти"); submit.type = "submit";
    form.append(q,submit); main.append(form);
    const controls = el("div", "search-controls");
    if (isLawyer()) {
        const category = el("select");
        category.innerHTML = '<option value="">Все категории</option>';
        const categories = await api("/categories");
        categories.forEach(item => category.append(new Option(item.name,item.id)));
        category.id = "categoryFilter";
        const clazz = el("select");
        clazz.innerHTML = '<option value="">Любой уголовный класс</option><option value="felony">Felony</option><option value="misdemeanor">Misdemeanor</option><option value="infraction">Infraction</option>';
        clazz.id = "classFilter";
        const jurisdiction = el("select");
        jurisdiction.innerHTML = '<option value="">Любая юрисдикция</option><option value="state">Штат</option><option value="county">Округ</option><option value="city">Лос-Сантос</option>';
        jurisdiction.id = "jurisdictionFilter";
        controls.append(category,clazz,jurisdiction);
        main.append(controls);
    }
    const results = el("div");
    main.append(results);
    form.onsubmit = async ev => {
        ev.preventDefault(); results.replaceChildren(el("p", "muted", "Выполняется поиск…"));
        const params = new URLSearchParams({query:q.value});
        if (isLawyer()) {
            [["category","categoryFilter"],["criminal_class","classFilter"],["jurisdiction","jurisdictionFilter"]]
                .forEach(pair => {const value = document.getElementById(pair[1]).value;if(value)params.set(pair[0],value);});
        }
        try {
            const found = await api("/search?" + params);
            results.replaceChildren();
            if (!found.items.length) results.append(el("p", "empty", "Совпадений не найдено."));
            found.items.forEach(a => {
                const item = el("a", "search-result");
                item.href = "/articles/" + a.id; item.dataset.link = "";
                item.append(el("span", "", "Статья " + a.number), el("strong", "", a.title),
                            el("span", "", a.code_id));
                results.append(item);
            });
        } catch(e) { results.replaceChildren(el("p", "notice", e.message)); }
    };
}
async function favoritesPage() {
    pageHeading("Персональные инструменты", "Избранные статьи",
        "Ваш единый список избранных норм законодательства.");
    const items = await api("/me/favorites");
    if (!items.length) main.append(el("p", "empty", "Вы пока не добавили статьи в избранное."));
    for (const a of items) {
        const item = el("div", "search-result");
        const link = el("a", "", "Статья " + a.number + " — " + a.title);
        link.href = "/articles/" + a.id; link.dataset.link = "";
        item.append(link);
        const remove = el("button", "button subtle", "Удалить из избранного");
        remove.onclick = async () => {
            try { await api("/me/favorites/" + a.id, {method:"DELETE"}); item.remove(); }
            catch(e) { notify(e.message); }
        };
        item.append(remove); main.append(item);
    }
}
async function notificationsPage() {
    const activePortal = new URLSearchParams(location.search).get("portal") || "library";
    pageHeading("Личный кабинет", "Центр уведомлений", "Уведомления конкретного государственного портала.");
    const select = el("select");
    allPortals.forEach(p => select.append(new Option(p.name,p.id)));
    select.value = activePortal;
    select.onchange = () => { history.replaceState({}, "", "/notifications?portal=" + select.value); notificationsPageRerender(select.value); };
    main.append(select);
    await notificationsPageRerender(activePortal);
}
async function notificationsPageRerender(portalId) {
    let section = document.getElementById("notificationsList");
    if (!section) {section = el("section");section.id="notificationsList";main.append(section);}
    section.replaceChildren();
    try {
        const list = await api("/me/notifications?portal_id=" + encodeURIComponent(portalId));
        if (!list.length) section.append(el("p", "empty", "Уведомлений пока нет."));
        list.forEach(n => {
            const item = el("div", "search-result");
            item.append(el("strong", "", n.title),
                        el("span", "", friendlyDate(n.created_at) + (n.is_read ? " · Прочитано" : " · Новое")));
            const link = el("a", "button subtle", "Открыть объект");
            link.href = portalId === "library" ? "/articles/" + n.object_id : "/publications/" + n.object_id;
            link.dataset.link = "";
            item.append(link);
            if (!n.is_read) {
                const button = el("button", "button subtle", "Отметить прочитанным");
                button.onclick = async () => {
                    try {await api("/me/notifications/" + n.id + "/read",{method:"POST"});button.remove();}
                    catch(e) {notify(e.message);}
                };
                item.append(button);
            }
            section.append(item);
        });
    } catch(e) { section.append(el("p", "notice", e.message)); }
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
    const a=await api("/articles/"+id);
    pageHeading("Архив редакций", "История статьи "+a.number, a.title);
    const list=await api("/articles/"+id+"/revisions");
    list.forEach(r => {
        const box=el("section","panel");
        box.append(el("h2","","Редакция "+r.sequence+" · "+friendlyDate(r.published_at)),
                   el("p","muted",r.change_reason || "Основание не указано"));
        const show=el("button","button ghost","Показать текст редакции");
        show.onclick=async()=>{
            try {const data=await api("/articles/"+id+"/revisions/"+r.id);
                const t=el("div","article-text");t.innerHTML=data.text_rich;box.append(t);show.remove();}
            catch(e){notify(e.message);}
        };
        box.append(show);main.append(box);
    });
}
async function billListPage() {
    pageHeading("Законодательный орган", "Законопроекты",
        "Обсуждения, голосования и последовательность опубликованных результатов. Переходы статусов используют демонстрационный регламент.");
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
    const serial=++lastNavSerial;
    clearReading();
    main.replaceChildren();
    codeNav.replaceChildren();
    if (!currentUser) {
        if (!role) {
            pageHeading("Требуется вход", "Законодательная библиотека",
                "Для чтения правовых текстов выберите тестовую роль в верхней панели.");
            return;
        }
        await loadContext();
    }
    if(serial!==lastNavSerial)return;
    refreshGlobalNav();
    const parts=path.split("/").filter(Boolean);
    if(!parts.length)await home();
    else if(parts[0]==="jurisdictions"&&parts[1])await jurisdiction(parts[1]);
    else if(parts[0]==="codes"&&parts[1])
        await openSection(parts[1],parts[2]==="nodes"?parts[3]:null,null,options,serial);
    else if(parts[0]==="articles"&&parts[1]){
        const article=await api("/articles/"+parts[1]);
        await openSection(article.code_id,article.parent_id,article.id,options,serial);
    }
    else if(parts[0]==="search")await searchPage();
    else if(parts[0]==="favorites")await favoritesPage();
    else if(parts[0]==="notifications")await notificationsPage();
    else if(parts[0]==="precedents")await precedentPage();
    else if(parts[0]==="portals"&&parts[1])await portalPage(parts[1]);
    else if(parts[0]==="publications"&&parts[1])await publicationPage(parts[1]);
    else if(parts[0]==="bills"&&parts[1])await billPage(parts[1]);
    else if(parts[0]==="bills")await billListPage();
    else if(parts[0]==="reviews")await reviewsPage();
    else if(parts[0]==="revisions"&&parts[1])await revisionsPage(parts[1]);
    else throw Error("Страница не найдена.");
    if(options.restore && !reading) window.scrollTo(0,options.scrollY ?? Number(sessionStorage.getItem("sas-scroll:"+path)||0));
    else if(!reading)window.scrollTo(0,0);
}
function collectEditorBody() {
    const values=Object.fromEntries(new FormData(editorForm));
    const categories=Array.from(document.querySelectorAll('#editorCategories input:checked')).map(x=>x.value);
    const sanctions={};
    ["prison_min_months","prison_max_months","fine_min_usd","fine_max_usd"].forEach(key=>{
        sanctions[key]=values[key]===""?null:Number(values[key]);
    });
    return {number:values.number,title:values.title,text_rich:values.text_rich,categories,
        criminal_class:values.criminal_class||null,sanctions,reason:values.reason||"Редакторская правка"};
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
        editorForm.elements.text_rich.value=article?article.text_rich:"";
        editorForm.elements.criminal_class.value=article?article.criminal_class||"":"";
        const data=article&&article.sanctions||{};
        Object.keys(data).forEach(key=>{editorForm.elements[key].value=data[key]===null?"":data[key];});
        document.getElementById("editorTitle").textContent=article?"Редактирование статьи "+article.number:"Создание статьи";
        document.getElementById("editorStatus").textContent="";
        document.getElementById("saveEditor").disabled=false;
        document.getElementById("reacquireLock").hidden=true;
        const categories=await api("/categories?code_id="+encodeURIComponent(codeId));
        const box=document.getElementById("editorCategories");box.replaceChildren();
        categories.forEach(cat=>{
            const label=el("label");
            const check=el("input");check.type="checkbox";check.name="category";check.value=cat.id;
            check.checked=Boolean(article&&article.categories&&article.categories.includes(cat.id));
            label.append(check,document.createTextNode(cat.name));box.append(label);
        });
        dialog.showModal();
        if(state.token)heartbeatTimer=setInterval(()=>heartbeatEditor(state),60000);
    }catch(e){notify(e.message);}
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
    const body=collectEditorBody();
    try{
        let saved;
        if(state.article) {
            saved=await api("/articles/"+state.article.id+"/revisions",{method:"POST",
                body:Object.assign(body,{base_revision_id:state.baseRevision,lock_token:state.token,
                     editor_session_id:state.sessionId})});
            state.baseRevision=saved.revision_id;
            state.article=saved;
        } else {
            saved=await api("/articles",{method:"POST",body:Object.assign(body,{code_id:state.codeId,parent_id:state.nodeId})});
            state.article=saved;state.baseRevision=saved.revision_id;
            const lock=await api("/articles/"+saved.id+"/edit-lock",{method:"POST",body:{editor_session_id:state.sessionId}});
            state.token=lock.token;
            heartbeatTimer=setInterval(()=>heartbeatEditor(state),60000);
        }
        editorDirty=false;
        const previous=document.getElementById("card-"+saved.id);
        if(previous)previous.replaceWith(createArticleCard(saved));
        else if(reading&&reading.cards)reading.cards.append(createArticleCard(saved));
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
renderRoute(location.pathname,{restore:true}).catch(showError);
