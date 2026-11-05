"use strict";
(() => {
  const state = { data: null, account: null, character: null };
  const $ = (s) => document.querySelector(s);
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
  const names = {
    user: "Читатель",
    lawyer: "Юрист библиотеки",
    library_editor: "Редактор библиотеки",
    portal_publisher: "Публикация материалов",
    superadmin: "Администратор портала",
    senator: "Сенатор",
    pro_tempore: "Pro Tempore",
    lieutenant_governor: "Лейтенант-Губернатор",
  };
  const role = () => sessionStorage.getItem("senate-demo-role") || "user";
  async function api(path, method = "GET", body) {
    const r = await fetch("/api/v1/test-accounts" + path, {
      method,
      headers: { "X-Demo-User": role(), "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    const d = await r.json();
    if (!r.ok) throw Error(d.error?.message || "Ошибка запроса");
    return d;
  }
  function message(text, error = false) {
    $("#message").textContent = text;
    $("#message").className = error ? "error" : "success";
  }
  function checks(values, selected, name) {
    return `<div class="permission-list">${values.map((v) => `<label><input type="checkbox" name="${name}" value="${esc(v)}" ${selected.includes(v) ? "checked" : ""}>${esc(names[v] || v)}</label>`).join("")}</div>`;
  }
  function field(name, title, value = "") {
    return `<label class="field">${title}<input name="${name}" value="${esc(value)}" ${name === "name" ? "required" : ""}></label>`;
  }
  function list() {
    const q = $("#search").value.toLowerCase();
    $("#accountList").innerHTML = state.data.accounts
      .filter((a) => (a.name + " " + a.id).toLowerCase().includes(q))
      .map(
        (a) =>
          `<button class="account-row ${state.account === a.id ? "active" : ""}" data-account="${a.id}">${esc(a.name)}<small>${a.id} · ${a.characters.length} персонажа · ${a.active ? "Включён" : "Отключён"}</small></button>`,
      )
      .join("");
  }
  function draw() {
    list();
    const a = state.data.accounts.find((a) => a.id === state.account);
    if (!a) return;
    let c =
      a.characters.find((c) => c.id === state.character) || a.characters[0];
    state.character = c?.id;
    $("#detail").innerHTML =
      `<h2>${esc(a.name)}</h2><p class="muted">${a.id}. Общие права аккаунта действуют для всех его персонажей.</p><form data-form="account">${field("name", "Название аккаунта", a.name)}<label><input type="checkbox" name="active" ${a.active ? "checked" : ""}> Аккаунт включён</label>${checks(state.data.account_roles, a.roles, "roles")}<button>Сохранить аккаунт</button></form><section class="panel"><h2>Профили персонажей</h2><label class="field">Персонаж<select id="character">${a.characters.map((ch) => `<option value="${ch.id}" ${ch.id === c?.id ? "selected" : ""}>${esc(ch.name)} · ${ch.id}</option>`).join("")}</select></label>${
        c
          ? `<div class="actions"><button type="button" data-open="senate" ${a.active ? "" : "disabled"}>Открыть Сенат этим персонажем</button><button type="button" data-open="library" ${a.active ? "" : "disabled"}>Открыть библиотеку этим персонажем</button></div><p class="small-text">Документы и голоса принадлежат выбранному персонажу. В аудите сохраняется и аккаунт владельца.</p><form data-form="profile"><div class="form-grid">${[
              ["name", "Имя персонажа"],
              ["capitol_office", "Офис в Капитолии"],
              ["district_office", "Офис в дистрикте"],
              ["phone", "Телефон"],
              ["email", "Почта"],
            ]
              .map(([k, n]) => field(k, n, c[k]))
              .join(
                "",
              )}</div><button>Сохранить профиль</button></form><form data-form="flags"><h3>Полномочия и группы персонажа</h3>${checks(state.data.character_flags, c.flags, "flags")}<button>Сохранить полномочия</button></form>`
          : ""
      }</section><details class="panel"><summary>Добавить персонажа</summary><form data-form="new-character">${field("name", "Имя нового персонажа")}<button>Создать профиль</button></form></details><details class="panel"><summary>Последние изменения аккаунтов</summary>${state.data.audit.map((r) => `<p>${esc(new Date(r.created_at).toLocaleString("ru-RU"))} · ${esc(r.user_id)} · ${esc(r.action)} · ${esc(r.object_id)}</p>`).join("") || "<p>Изменений пока нет.</p>"}</details>`;
  }
  async function load() {
    try {
      state.data = await api("");
      Object.assign(names, state.data.flag_labels);
      $("#message").textContent = "";
      $("#message").className = "";
      state.account ??= state.data.accounts[0]?.id;
      draw();
    } catch (e) {
      $("#detail").textContent = e.message;
      message(e.message, true);
    }
  }
  $("#bootstrapAdmin").addEventListener("click", () => {
    sessionStorage.setItem("senate-demo-role", "superadmin");
    load();
  });
  $("#search").addEventListener("input", () => state.data && list());
  document.addEventListener("change", (e) => {
    if (e.target.id === "character") {
      state.character = e.target.value;
      draw();
    }
  });
  document.addEventListener("click", (e) => {
    const a = e.target.closest("[data-account]");
    if (a) {
      state.account = a.dataset.account;
      state.character = null;
      draw();
    }
    const open = e.target.closest("[data-open]");
    if (open) {
      const identity = state.account + "@" + state.character;
      sessionStorage.setItem("senate-demo-role", identity);
      sessionStorage.setItem("sas-demo-role", identity);
      location.href = open.dataset.open === "senate" ? "/senate#cabinet" : "/";
    }
  });
  document.addEventListener("submit", async (e) => {
    const form = e.target;
    if (!form.dataset.form) return;
    e.preventDefault();
    const b = form.querySelector("button");
    b.disabled = true;
    const d = new FormData(form);
    try {
      switch (form.dataset.form) {
        case "account":
          await api("/" + state.account, "PUT", {
            name: d.get("name"),
            active: d.has("active"),
            roles: d.getAll("roles"),
          });
          break;
        case "profile":
          await api(
            "/characters/" + state.character + "/profile",
            "PUT",
            Object.fromEntries(d),
          );
          break;
        case "flags":
          await api("/characters/" + state.character + "/flags", "PUT", {
            flags: d.getAll("flags"),
          });
          break;
        case "new-character":
          state.character = (
            await api("/" + state.account + "/characters", "POST", {
              name: d.get("name"),
            })
          ).id;
          break;
      }
      await load();
      message("Изменения сохранены.");
    } catch (e) {
      message(e.message, true);
    } finally {
      b.disabled = false;
    }
  });
  load();
})();
