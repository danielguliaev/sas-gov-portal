/* Administrative workspace for the legal library. All writes are confirmed by the API. */
"use strict";
function adminButton(label, action, variant) {
    const button = el("button", "button " + (variant || "ghost"), label);
    button.type = "button";
    button.onclick = action;
    return button;
}
function adminLink(label, href) {
    const a = el("a", "button ghost", label);
    a.href = href; a.dataset.link = "";
    return a;
}
function adminField(label, control) {
    const wrapper = el("label", "admin-field");
    wrapper.append(el("span", "", label),control);
    return wrapper;
}
function adminInput(value, placeholder) {
    const input = el("input");
    input.value = value == null ? "" : String(value);
    if (placeholder) input.placeholder = placeholder;
    return input;
}
function adminSelect(choices, value, blankLabel) {
    const select = el("select");
    if (blankLabel !== undefined) select.append(new Option(blankLabel,""));
    choices.forEach(item => select.append(new Option(item[0],item[1])));
    if (value !== undefined && value !== null) select.value = value;
    return select;
}
function adminSection(title, description) {
    const section = el("section", "admin-panel");
    section.append(el("h2", "", title));
    if (description) section.append(el("p", "muted", description));
    return section;
}
function adminActions() { return el("div","admin-actions"); }
function adminForm(action) {
    const form = el("form","admin-form");
    const status = el("p","admin-status");status.setAttribute("role","status");
    const footer = el("div","admin-actions");
    const save = el("button","button primary","Сохранить");save.type = "submit";
    footer.append(save);
    form.onsubmit = async event => {
        event.preventDefault();
        save.disabled=true; status.textContent="Проверка и сохранение…";
        try { await action(); status.textContent="Изменения сохранены."; }
        catch(error) { status.textContent=error.message; adminConflicts(form,error); }
        finally { save.disabled=false; }
    };
    form.append(footer,status);
    return {form,footer,status,save};
}
function adminConflicts(container,error) {
    const list = error.details && (error.details.conflicts || error.details.node_ids);
    if (!Array.isArray(list) || !list.length) return;
    const box=el("div","admin-conflicts");
    box.append(el("strong","","Конфликтующие элементы:"));
    list.forEach(item => {
        const articleId=typeof item==="string"?null:(item.source_id || item.target_id || item.article_id);
        const nodeId=typeof item==="string"?item:item.node_id;
        const row=el("p");
        row.append(document.createTextNode((item.reason || item.relation_id || nodeId || articleId || "Конфликт")+" "));
        if(articleId) row.append(adminLink("Открыть статью","/articles/"+articleId));
        if(nodeId) row.append(el("code","",nodeId));
        box.append(row);
    });
    container.append(box);
}
function adminInfo(message) {return el("p","admin-help",message);}
async function adminSafe(task) {
    try {await task();}
    catch(error) {notify(error.message);}
}
async function adminPage(section) {
    if(!isAdmin()) throw Error("Управление библиотекой доступно только суперадминистратору.");
    main.append(breadcrumb([{label:"Главная",href:"/"},{label:"Управление библиотекой"}]));
    pageHeading("Административная область","Управление правовой библиотекой",
        "Структура кодексов, классификация, юридические связи, архив и аудит операций.");
    const tabs=el("nav","admin-tabs");tabs.setAttribute("aria-label","Администрирование");
    const pages=[
        ["overview","Обзор"],["codes","Кодексы"],["hierarchy","Иерархия"],
        ["categories","Категории и поля"],["relations","Юридические связи"],
        ["archive","Архив"],["audit","Журнал"]
    ];
    pages.forEach(entry=>{
        const link=el("a",entry[0]===section?"active":"",entry[1]);
        link.href="/admin"+(entry[0]==="overview"?"":"/"+entry[0]);
        link.dataset.link="";
        if(entry[0]===section)link.setAttribute("aria-current","page");
        tabs.append(link);
    });
    main.append(tabs);
    const area=el("div","admin-workspace");main.append(area);
    if(section==="overview"){
        const grid=el("div","admin-feature-grid");
        pages.slice(1).forEach(([id,name])=>{
            const card=el("a","admin-feature");
            card.href="/admin/"+id;card.dataset.link="";
            card.append(el("strong","",name),el("span","","Перейти к настройкам →"));
            grid.append(card);
        });
        area.append(grid,adminInfo("Критические операции сохраняются только после серверной проверки и отдельного подтверждения."));
    }
    else if(section==="codes") await adminCodes(area);
    else if(section==="hierarchy") await adminHierarchy(area);
    else if(section==="categories") await adminCategories(area);
    else if(section==="relations") await adminRelations(area);
    else if(section==="archive") await adminArchive(area);
    else if(section==="audit") await adminAudit(area);
    else throw Error("Неизвестный раздел администрирования.");
}
async function adminCodes(area) {
    const [jurisdictions,codes]=await Promise.all([api("/jurisdictions"),api("/codes")]);
    const section=adminSection("Кодексы и нормативные сборники",
        "Создавайте кодексы штата, округа и города. Каждый кодекс получает собственную иерархию и постоянную ссылку.");
    section.classList.add("admin-codes-page");
    const header=el("div","admin-section-toolbar");
    const description=el("div","admin-section-caption");
    description.append(el("span","admin-eyebrow","РЕЕСТР И СТРУКТУРА"),
        el("strong","","Управление кодексами"),
        el("span","muted",codes.length+" "+pluralCode(codes.length)+" в библиотеке"));
    const open=adminButton("＋ Создать кодекс",()=>{
        wizard.hidden=!wizard.hidden;
        open.setAttribute("aria-expanded",String(!wizard.hidden));
        if(!wizard.hidden) nameField.focus();
    },"primary");
    open.id="openCodeWizard";
    open.setAttribute("aria-controls","newCodeWizard");
    header.append(description,open);
    section.append(header);

    const wizard=el("section","code-create-wizard");
    wizard.id="newCodeWizard";
    const showForm=new URLSearchParams(location.search).get("new")==="1" || !codes.length;
    wizard.hidden=!showForm;
    open.setAttribute("aria-expanded",String(showForm));
    const wizardTitle=el("div","code-create-heading");
    wizardTitle.append(el("div","code-create-symbol","§"));
    const titleText=el("div");
    titleText.append(el("h3","","Новый кодекс"),
        el("p","muted","Укажите основные сведения и решите, с какой структуры начать."));
    wizardTitle.append(titleText);
    wizard.append(wizardTitle);

    const form=adminForm(async()=>{
        const name=nameField.value.trim();
        const abbreviation=abbr.value.trim();
        if(!name)throw Error("Введите название кодекса.");
        if(!jurisdiction.value)throw Error("Выберите юрисдикцию.");
        const newCode=await api("/codes",{method:"POST",body:{
            name,abbreviation,jurisdiction_id:jurisdiction.value,
            hierarchy_template:hierarchy.value,
            publication_issuer:publicationIssuer.value.trim(),
            publication_reference:publicationReference.value.trim(),
            published_at:publishedAt.value}});
        allCodes=await api("/codes");
        notify("Кодекс «"+name+"» создан. Теперь можно добавлять части, разделы и статьи.");
        go("/codes/"+newCode.id);
    });
    form.form.classList.add("code-create-form");
    form.form.id="newCodeForm";
    form.save.textContent="Создать кодекс";
    const nameField=adminInput("","Например: Civil Code или Уголовный кодекс");
    nameField.required=true;
    nameField.maxLength=200;
    nameField.name="name";
    nameField.id="newCodeName";
    const abbr=adminInput("","Например: CC, PC");
    abbr.name="abbreviation"; abbr.maxLength=40;
    const jurisdiction=adminSelect(jurisdictions.map(j=>[j.name,j.id]),
        new URLSearchParams(location.search).get("jurisdiction") || "state");
    jurisdiction.name="jurisdiction_id";
    const hierarchy=adminSelect([
        ["Начать со стандартных уровней (Часть → Раздел → Глава → Подраздел)","standard"],
        ["Начать с пустой структуры и настроить самостоятельно","empty"]
    ],"standard");
    hierarchy.name="hierarchy_template";
    const topFields=el("div","admin-create-grid");
    topFields.append(adminField("Название кодекса *",nameField),
        adminField("Краткое обозначение",abbr),
        adminField("Уровень законодательства *",jurisdiction));
    form.form.prepend(topFields,adminField("Начальная структура",hierarchy));
    const publication=el("details","code-publication-fields");
    publication.append(el("summary","","Необязательно: сведения о публикации кодекса"));
    const publicationGrid=el("div","admin-create-grid");
    const publicationIssuer=adminInput("","Издавший орган");
    publicationIssuer.name="publication_issuer";
    publicationIssuer.maxLength=200;
    const publicationReference=adminInput("","Регистрационный номер, источник");
    publicationReference.name="publication_reference";
    publicationReference.maxLength=250;
    const publishedAt=adminInput("");
    publishedAt.type="date";
    publishedAt.name="published_at";
    publishedAt.setAttribute("aria-label","Дата официальной публикации");
    publicationGrid.append(adminField("Издавший орган",publicationIssuer),
        adminField("Реквизиты публикации",publicationReference),
        adminField("Дата публикации",publishedAt));
    publication.append(publicationGrid);
    form.form.insertBefore(publication,form.footer);
    const hint=el("p","admin-inline-hint",
        "Выбранные типы уровней можно изменять позже. Статьи разрешено размещать прямо в корне кодекса.");
    form.form.insertBefore(hint,form.footer);
    const cancel=adminButton("Отмена",()=>{
        wizard.hidden=true;
        open.setAttribute("aria-expanded","false");
    });
    form.footer.prepend(cancel);
    wizard.append(form.form);
    section.append(wizard);

    const listing=el("section","admin-code-register");
    listing.append(el("h3","","Существующие кодексы"));
    const codeGrid=el("div","admin-code-grid");
    if(!codes.length){
        const blank=el("div","admin-empty-state");
        blank.append(el("strong","","Пока нет созданных кодексов"),
            el("p","","Начните с названия и выберите начальную структуру."));
        codeGrid.append(blank);
    }
    codes.forEach(c=>{
        const item=el("article","admin-code-card");
        const top=el("div","admin-code-card-header");
        top.append(el("span","code-acronym",c.abbreviation||"§"),
            el("span","admin-code-jurisdiction",
                (jurisdictions.find(j=>j.id===c.jurisdiction_id)||{}).name||c.jurisdiction_id));
        item.append(top,el("h4","",c.name));
        const counts=el("p","muted",
            (c.section_count||0)+" структурных элементов · "+(c.article_count||0)+" статей");
        item.append(counts);
        const actions=el("div","admin-actions");
        actions.append(adminLink("Открыть кодекс","/codes/"+c.id),
            adminLink("Настроить иерархию","/admin/hierarchy?code="+c.id));
        item.append(actions);
        const meta=el("details","code-metadata-editor");
        meta.append(el("summary","","Изменить название и сведения о публикации"));
        const edit=adminForm(async()=>{
            const nextName=rename.value.trim();
            if(!nextName)throw Error("Введите название кодекса.");
            await api("/codes/"+c.id,{method:"PATCH",body:{
                name:nextName,abbreviation:shortName.value.trim(),
                publication_issuer:issuer.value.trim(),
                publication_reference:reference.value.trim(),
                published_at:date.value
            }});
            allCodes=await api("/codes");
            notify("Сведения кодекса сохранены.");
            go("/admin/codes");
        });
        edit.save.textContent="Сохранить сведения";
        const rename=adminInput(c.name,"Название кодекса");
        rename.required=true;
        rename.maxLength=200;
        const shortName=adminInput(c.abbreviation||"","Краткое обозначение");
        shortName.maxLength=40;
        const issuer=adminInput(c.publication_issuer||"","Издавший орган");
        issuer.name="publication_issuer";
        issuer.maxLength=200;
        const reference=adminInput(c.publication_reference||"","Номер официального документа");
        reference.name="publication_reference";
        reference.maxLength=250;
        const date=adminInput(c.published_at||"");
        date.type="date";
        date.name="published_at";
        const metadata=el("div","code-metadata-grid");
        metadata.append(adminField("Название",rename),
            adminField("Сокращение",shortName),
            adminField("Издавший орган",issuer),
            adminField("Реквизиты публикации",reference),
            adminField("Дата публикации",date));
        edit.form.prepend(metadata);
        meta.append(edit.form);
        item.append(meta);
        codeGrid.append(item);
    });
    listing.append(codeGrid);
    section.append(listing);
    area.append(section);
}

async function adminCategories(area) {
    const section=adminSection("Категории и пользовательские поля",
        "Настраивайте классификацию законов без работы с длинными техническими формами. Изменения сохраняются отдельно для каждой категории и каждого поля.");
    section.classList.add("admin-categories-page");
    const kinds=[
        ["Текст","string"],["Форматируемый текст","rich_text"],["Число","number"],
        ["Диапазон","range"],["Да / нет","boolean"],["Выбор из справочника","choice"]
    ];
    const names=Object.fromEntries(kinds.map(k=>[k[1],k[0]]));
    let categories=[];
    let selectedId=new URLSearchParams(location.search).get("category")||null;

    const toolbar=el("div","category-toolbar");
    const scope=adminSelect(allCodes.map(c=>[c.name,c.id]),
        new URLSearchParams(location.search).get("code")||"",
        "Только глобальные категории");
    scope.id="categoryCodeScope";
    scope.setAttribute("aria-label","Контекст кодекса");
    const scopeLabel=adminField("Контекст кодекса",scope);
    const addCategory=adminButton("＋ Новая категория",()=>{
        creator.hidden=!creator.hidden;
        addCategory.setAttribute("aria-expanded",String(!creator.hidden));
        if(!creator.hidden)newCategory.focus();
    },"primary");
    addCategory.id="addCategoryButton";
    addCategory.setAttribute("aria-controls","categoryCreator");
    addCategory.setAttribute("aria-expanded","false");
    toolbar.append(scopeLabel,addCategory);
    section.append(toolbar);

    const creator=el("div","category-new-panel");
    creator.id="categoryCreator";
    creator.hidden=true;
    const create=adminForm(async()=>{
        const name=newCategory.value.trim();
        if(!name)throw Error("Введите название категории.");
        const added=await api("/categories",{method:"POST",body:{
            name,code_id:scope.value||null}});
        newCategory.value="";
        creator.hidden=true;
        addCategory.setAttribute("aria-expanded","false");
        await refresh(added.id);
        notify("Категория создана.");
    });
    create.form.id="newCategoryForm";
    create.save.textContent="Создать категорию";
    const newCategory=adminInput("","Например: Особые условия");
    newCategory.id="newCategoryName";
    newCategory.required=true;
    newCategory.maxLength=150;
    const destinationHint=adminInfo("");
    function updateDestinationHint(){
        destinationHint.textContent="Категория будет добавлена "+
            (scope.value?"в выбранный кодекс.":"во все кодексы как глобальная.");
    }
    updateDestinationHint();
    create.form.prepend(el("h3","","Создание категории"),
        adminField("Название новой категории *",newCategory),destinationHint);
    create.footer.prepend(adminButton("Отмена",()=>{
        creator.hidden=true;
        addCategory.setAttribute("aria-expanded","false");
    }));
    creator.append(create.form);
    section.append(creator);

    const workbench=el("div","category-workbench");
    const sidebar=el("aside","category-sidebar");
    sidebar.setAttribute("aria-label","Категории законодательства");
    const sidebarTop=el("div","category-sidebar-top");
    sidebarTop.append(el("h3","","Категории"));
    const search=adminInput("","Поиск по названиям");
    search.id="categorySearch";
    search.type="search";
    search.setAttribute("aria-label","Найти категорию");
    const summary=el("p","category-result-count");
    sidebarTop.append(search,summary);
    sidebar.append(sidebarTop);
    const categoryList=el("div","category-picker");
    categoryList.id="categoryPicker";
    sidebar.append(categoryList);
    const detail=el("section","category-detail");
    detail.setAttribute("aria-label","Настройки выбранной категории");
    detail.id="categoryDetail";
    workbench.append(sidebar,detail);
    section.append(workbench);
    area.append(section);

    function filtered() {
        const phrase=search.value.trim().toLocaleLowerCase("ru-RU");
        return categories.filter(cat=>cat.name.toLocaleLowerCase("ru-RU").includes(phrase));
    }
    function drawList() {
        categoryList.replaceChildren();
        const visible=filtered();
        summary.textContent=visible.length+" из "+categories.length;
        if(!visible.length){
            categoryList.append(el("p","category-list-empty",
                categories.length?"Совпадений нет. Попробуйте другое название.":"Категорий пока нет. Создайте первую."));
            return;
        }
        visible.forEach(cat=>{
            const button=el("button","category-picker-item");
            button.type="button";
            button.dataset.categoryId=cat.id;
            const selected=cat.id===selectedId;
            button.classList.toggle("active",selected);
            button.setAttribute("aria-pressed",String(selected));
            const label=el("span","category-picker-label",cat.name);
            const meta=el("span","category-picker-meta");
            meta.append(el("span","",cat.code_id?"Для кодекса":"Глобальная"),
                el("span","",String((cat.fields||[]).length)+" полей"));
            button.append(label,meta);
            button.onclick=()=>{
                selectedId=cat.id;
                syncUrl();
                drawList();
                drawDetail();
            };
            categoryList.append(button);
        });
    }
    function optionEditor(option,field) {
        const row=el("div","category-option-row");
        row.dataset.optionId=option.id;
        const name=adminInput(option.name,"Название значения");
        name.maxLength=200;
        name.setAttribute("aria-label","Название значения "+option.name);
        const active=el("input");
        active.type="checkbox";
        active.checked=!!option.active;
        active.setAttribute("aria-label","Доступно для новых выборов: "+option.name);
        const label=el("label","category-option-active");
        label.append(active,el("span","","Доступно"));
        const save=adminButton("Сохранить",()=>adminSafe(async()=>{
            if(!name.value.trim())throw Error("Укажите название значения справочника.");
            await api("/field-options/"+option.id,{method:"PATCH",
                body:{name:name.value.trim(),active:active.checked}});
            await refresh(catSelected(),field.id);
            notify("Значение справочника обновлено.");
        }));
        row.append(name,label,save);
        return row;
    }
    function catSelected() {return selectedId;}
    function fieldEditor(field) {
        const holder=el("details","category-field-item");
        holder.dataset.fieldId=field.id;
        const headline=el("summary","category-field-summary");
        const name=el("strong","",field.name);
        const type=el("span","category-field-type",names[field.field_type]||field.field_type);
        const state=el("span","category-field-state",
            field.used_ever?"Используется":"Можно настроить");
        headline.append(name,type,state);
        holder.append(headline);
        const body=el("div","category-field-body");
        const form=adminForm(async()=>{
            const title=fieldName.value.trim();
            if(!title)throw Error("Укажите название поля.");
            await api("/category-fields/"+field.id,{method:"PATCH",body:{
                name:title,field_type:fieldType.value,
                selection_mode:fieldType.value==="choice"?selection.value:null}});
            await refresh(selectedId,field.id);
            notify("Настройки поля сохранены.");
        });
        form.form.classList.add("category-compact-form");
        form.save.textContent="Сохранить настройки";
        const fieldName=adminInput(field.name);
        fieldName.required=true;
        fieldName.maxLength=150;
        const fieldType=adminSelect(kinds,field.field_type);
        const selection=adminSelect([["Одно значение","single"],["Несколько значений","multiple"]],
            field.selection_mode||"single");
        fieldType.disabled=!!field.used_ever;
        selection.disabled=!!field.used_ever || field.field_type!=="choice";
        fieldType.onchange=()=>{
            selection.disabled=!!field.used_ever || fieldType.value!=="choice";
        };
        const editorGrid=el("div","category-field-grid");
        editorGrid.append(adminField("Название поля *",fieldName),
            adminField("Тип данных",fieldType),adminField("Выбор значений",selection));
        form.form.prepend(editorGrid);
        if(field.used_ever){
            form.form.insertBefore(el("p","admin-inline-hint",
                "Это поле уже применялось в статьях. Его тип и режим выбора закреплены для сохранения истории; название можно изменить."),
                form.footer);
        } else {
            form.form.insertBefore(el("p","admin-inline-hint",
                "Пока поле не использовано, тип и режим выбора можно менять."),
                form.footer);
        }
        body.append(form.form);
        if(field.field_type==="choice"){
            const optSection=el("section","category-options-section");
            const title=el("div","category-options-heading");
            title.append(el("h4","","Значения справочника"),
                el("span","category-field-type",(field.options||[]).length+" вариантов"));
            optSection.append(title);
            const options=el("div","category-options-list");
            if(!(field.options||[]).length){
                options.append(el("p","category-list-empty",
                    "Вариантов ещё нет. Добавьте первое значение."));
            }
            (field.options||[]).forEach(option=>options.append(optionEditor(option,field)));
            optSection.append(options);
            const addOption=adminForm(async()=>{
                const label=optionName.value.trim();
                if(!label)throw Error("Введите значение.");
                await api("/category-fields/"+field.id+"/options",{method:"POST",
                    body:{name:label}});
                await refresh(selectedId,field.id);
                notify("Вариант добавлен.");
            });
            addOption.form.classList.add("category-option-form");
            addOption.save.textContent="Добавить вариант";
            const optionName=adminInput("","Новое значение");
            optionName.required=true;
            optionName.maxLength=200;
            optionName.setAttribute("aria-label","Новое значение справочника");
            addOption.form.prepend(optionName);
            optSection.append(addOption.form);
            body.append(optSection);
        }
        holder.append(body);
        return holder;
    }
    function drawDetail(expandedField) {
        detail.replaceChildren();
        const cat=categories.find(item=>item.id===selectedId);
        if(!cat){
            const empty=el("div","category-detail-empty");
            empty.append(el("div","category-detail-symbol","§"),
                el("h3","","Выберите категорию"),
                el("p","","Найдите категорию в списке слева или создайте новую, чтобы настроить её поля."));
            detail.append(empty);
            return;
        }
        const header=el("div","category-detail-heading");
        const text=el("div");
        text.append(el("span","admin-eyebrow",cat.code_id?"КАТЕГОРИЯ КОДЕКСА":"ГЛОБАЛЬНАЯ КАТЕГОРИЯ"),
            el("h3","",cat.name),
            el("p","muted",(cat.fields||[]).length+" полей · Все поля необязательные"));
        const nameToggle=adminButton("Переименовать",()=>{
            rename.open=!rename.open;
            if(rename.open)categoryName.focus();
        });
        header.append(text,nameToggle);
        detail.append(header);

        const rename=el("details","category-rename");
        const renameSummary=el("summary","","Изменить название категории");
        rename.append(renameSummary);
        const renameForm=adminForm(async()=>{
            const newName=categoryName.value.trim();
            if(!newName)throw Error("Укажите название категории.");
            await api("/categories/"+cat.id,{method:"PATCH",body:{name:newName}});
            await refresh(cat.id);
            notify("Категория переименована.");
        });
        renameForm.save.textContent="Сохранить название";
        const categoryName=adminInput(cat.name);
        categoryName.required=true;
        categoryName.maxLength=150;
        renameForm.form.prepend(adminField("Новое название категории",categoryName));
        rename.append(renameForm.form);
        detail.append(rename);

        const headerFields=el("div","category-fields-heading");
        headerFields.append(el("h4","","Поля категории"),
            el("span","category-field-count",(cat.fields||[]).length+" полей"));
        detail.append(headerFields);
        const fields=el("div","category-fields-list");
        if(!(cat.fields||[]).length){
            fields.append(el("div","category-empty-fields",
                "В категории пока нет пользовательских полей. Добавьте поле, если нужно хранить дополнительные сведения."));
        }
        (cat.fields||[]).forEach(field=>{
            const row=fieldEditor(field);
            if(expandedField===field.id)row.open=true;
            fields.append(row);
        });
        detail.append(fields);

        const make=el("details","category-add-field");
        const summary=el("summary","category-add-field-trigger","＋ Добавить поле");
        make.append(summary);
        const form=adminForm(async()=>{
            const name=fname.value.trim();
            if(!name)throw Error("Укажите название нового поля.");
            const result=await api("/categories/"+cat.id+"/fields",{method:"POST",
                body:{name,field_type:kind.value,
                      selection_mode:kind.value==="choice"?selectMode.value:null}});
            await refresh(cat.id,result.id);
            notify("Поле добавлено.");
        });
        form.save.textContent="Создать поле";
        const fname=adminInput("","Например: Основание применения");
        fname.required=true;
        fname.maxLength=150;
        const kind=adminSelect(kinds,"string");
        const selectMode=adminSelect([
            ["Одно значение","single"],["Несколько значений","multiple"]],"single");
        selectMode.disabled=true;
        kind.onchange=()=>{selectMode.disabled=kind.value!=="choice";};
        const fieldGrid=el("div","category-field-grid");
        fieldGrid.append(adminField("Название поля *",fname),
            adminField("Тип данных",kind),adminField("Режим выбора",selectMode));
        form.form.prepend(fieldGrid,
            adminInfo("Поля необязательны. Вы можете вернуться к их настройке позже."));
        make.append(form.form);
        detail.append(make);
    }
    function syncUrl() {
        const params=new URLSearchParams();
        if(scope.value)params.set("code",scope.value);
        if(selectedId)params.set("category",selectedId);
        history.replaceState(history.state,"","/admin/categories"+
            (params.toString()?"?"+params:""));
    }
    async function refresh(preferId,expandedField) {
        const route="/categories"+(scope.value?"?code_id="+encodeURIComponent(scope.value):"");
        categories=await api(route);
        if(preferId!==undefined)selectedId=preferId;
        if(!categories.some(cat=>cat.id===selectedId))selectedId=categories[0]?.id||null;
        syncUrl();
        drawList();
        drawDetail(expandedField);
    }
    search.oninput=drawList;
    scope.onchange=()=>{
        selectedId=null;
        creator.hidden=true;
        addCategory.setAttribute("aria-expanded","false");
        updateDestinationHint();
        adminSafe(async()=>await refresh());
    };
    await refresh();
}

async function adminArticleFinder(label,initial) {
    const wrap=el("div","admin-finder");
    const query=adminInput(initial||"","Номер, название или текст статьи");
    const select=adminSelect([],undefined,"Сначала выполните поиск");
    const search=adminButton("Найти статью",()=>adminSafe(async()=>{
        if(!query.value.trim())throw Error("Введите поисковый запрос.");
        const found=await api("/search?query="+encodeURIComponent(query.value.trim()));
        select.replaceChildren(new Option("Выберите статью",""));
        found.items.forEach(a=>select.append(new Option(
            a.number+" · "+displayLegalTitle(a.title)+" · "+a.code_id,a.id)));
        if(!found.items.length)notify("Совпадений нет.");
    }));
    wrap.append(adminField(label,query),search,select);
    return {wrap,query,select};
}
async function adminRelations(area) {
    const section=adminSection("Юридические связи",
        "Назначайте направления связей вручную. Сервер проверяет категории, уникальность и конкурирующие назначения.");
    const [types,cats]=await Promise.all([api("/relation-types"),api("/categories")]);
    const catOptions=cats.map(c=>[c.name,c.id]);
    const addType=adminForm(async()=>{
        await api("/relation-types",{method:"POST",body:{
            name:typeName.value.trim(),source_category:sourceCat.value||null,
            target_category:targetCat.value||null,exclusive_target:exclusive.checked}});
        go("/admin/relations");
    });
    const typeName=adminInput("","Например: Обвинение — мера наказания");
    const sourceCat=adminSelect(catOptions,null,"Любая");
    const targetCat=adminSelect(catOptions,null,"Любая");
    const exclusive=el("input");exclusive.type="checkbox";
    addType.form.prepend(adminField("Название вида связи",typeName),
        adminField("Категория источника",sourceCat),adminField("Категория цели",targetCat),
        adminField("Одна цель на одно исходящее отношение",exclusive));
    section.append(el("h3","","Создать вид связи"),addType.form);
    types.forEach(type=>{
        const panel=el("div","admin-subpanel");
        const save=adminForm(async()=>{
            await api("/relation-types/"+type.id,{method:"PATCH",body:{
                name:name.value.trim(),source_category:source.value||null,
                target_category:target.value||null,exclusive_target:ex.checked}});
            go("/admin/relations");
        });
        const name=adminInput(type.name);
        const source=adminSelect(catOptions,type.source_category,"Любая");
        const target=adminSelect(catOptions,type.target_category,"Любая");
        const ex=el("input");ex.type="checkbox";ex.checked=!!type.exclusive_target;
        save.save.textContent="Обновить правило";
        save.form.prepend(adminField("Вид связи",name),
            adminField("Категория источника",source),
            adminField("Категория цели",target),
            adminField("Запретить дублирование цели",ex));
        panel.append(save.form);
        section.append(panel);
    });
    const relationForm=adminForm(async()=>{
        if(!from.select.value || !to.select.value)throw Error("Выберите обе статьи из результатов поиска.");
        await api("/relations",{method:"POST",body:{
            type_id:kind.value,source_id:from.select.value,target_id:to.select.value}});
        notify("Юридическая связь добавлена.");
        await drawActiveRelations();
    });
    relationForm.save.textContent="Создать связь";
    const kind=adminSelect(types.map(t=>[t.name,t.id]));
    const from=await adminArticleFinder("Исходная статья","");
    const to=await adminArticleFinder("Целевая статья","");
    relationForm.form.prepend(adminField("Тип связи",kind),from.wrap,to.wrap);
    section.append(el("h3","","Установить связь"),relationForm.form);
    const active=el("div","admin-list");
    const candidates=el("div","admin-list");
    section.append(el("h3","","Связи выбранной статьи"),active,
        el("h3","","Ранее отключённые связи · выборочное восстановление"),candidates);
    const currentQuery=new URLSearchParams(location.search).get("article")||"";
    const id=adminInput(currentQuery,"ID статьи");
    const btn=adminButton("Показать связи",drawActiveRelations);
    section.append(adminField("ID статьи для просмотра и снятия связей",id),btn);
    async function drawActiveRelations(){
        active.replaceChildren();
        candidates.replaceChildren();
        if(!id.value.trim())return;
        const suspended=await api("/articles/"+encodeURIComponent(id.value.trim())+
            "/relations/restore-candidates");
        if(!suspended.length)candidates.append(adminInfo("Отключённых связей у статьи нет."));
        suspended.forEach(link=>{
            const card=el("div","admin-subpanel");
            card.append(el("strong","",link.type_name),
                el("p","",displayLegalTitle(link.source_label)+" → "+displayLegalTitle(link.target_label)));
            const references=el("div","admin-actions");
            references.append(adminLink("Исходная статья","/articles/"+link.source_id),
                adminLink("Целевая статья","/articles/"+link.target_id));
            card.append(references);
            if(link.conflicts.length){
                card.append(el("p","notice",
                    "Восстановление заблокировано: "+link.conflicts.join(" ")));
            }else{
                const action=adminButton("Восстановить эту связь",()=>adminSafe(async()=>{
                    if(!confirm("Восстановить только выбранную связь? Будут созданы новые редакции обеих статей."))return;
                    await api("/relations/"+link.relation_id+"/restore",{method:"POST",
                        headers:{"Idempotency-Key":crypto.randomUUID()},
                        body:{confirm:true,preview_hash:link.preview_hash}});
                    notify("Выбранная юридическая связь восстановлена.");
                    await drawActiveRelations();
                }),"primary");
                card.append(action);
            }
            candidates.append(card);
        });
        const r=await api("/articles/"+encodeURIComponent(id.value.trim())+"/relations");
        if(!r.items.length)active.append(adminInfo("У этой статьи нет действующих связей."));
        r.items.forEach(rel=>{
            const row=el("div","admin-list-row");
            const targetId=rel.source_id===id.value?rel.target_id:rel.source_id;
            const title=rel.source_id===id.value?rel.target_title:rel.source_title;
            row.append(adminLink("Статья: "+displayLegalTitle(title),"/articles/"+targetId));
            row.append(adminButton("Отключить",()=>adminSafe(async()=>{
                if(!confirm("Отключить выбранную юридическую связь? Будет создана новая редакция."))return;
                await api("/relations/"+rel.id+"/deactivate",{method:"POST",
                    body:{reason:"manual"}});
                await drawActiveRelations();
            }),"danger"));
            active.append(row);
        });
    }
    if(id.value) await drawActiveRelations();
    area.append(section);
}
async function adminArchive(area) {
    const section=adminSection("Архивирование и восстановление",
        "Публикация не удаляется физически. Операции применяются атомарно после предварительного просмотра и явного подтверждения.");
    const source=adminSelect([["Статья","article_id"],["Раздел и вложенная структура","node_id"]],
        new URLSearchParams(location.search).has("node")?"node_id":"article_id");
    const target=adminInput(new URLSearchParams(location.search).get("article")||
        new URLSearchParams(location.search).get("node")||"","ID статьи или раздела");
    const form=adminForm(async()=>{
        const body={[source.value]:target.value.trim()};
        if(!body[source.value])throw Error("Укажите ID объекта для архивирования.");
        const plan=await api("/archive/preview",{method:"POST",body});
        const panel=el("div","admin-preview");
        panel.append(el("h3","","Предварительный просмотр архивирования"),
            el("p","","Разделов: "+plan.node_count+"; статей: "+plan.article_count));
        const ids=el("div","admin-id-list");
        plan.node_ids.forEach(id=>ids.append(el("p","","Раздел: "+id)));
        plan.article_ids.forEach(id=>ids.append(adminLink("Статья "+id,"/articles/"+id)));
        panel.append(ids);
        const confirmButton=adminButton("Подтвердить архивирование",()=>adminSafe(async()=>{
            if(!confirm("Архивировать указанные элементы? Подтвердите действие."))return;
            await api("/archive-batches",{method:"POST",headers:{
                "Idempotency-Key":crypto.randomUUID()},
                body:{...body,preview_hash:plan.preview_hash,confirm:true}});
            notify("Архивирование подтверждено сервером.");
            go("/admin/archive");
        }),"danger");
        panel.append(confirmButton);
        preview.replaceChildren(panel);
    });
    form.save.textContent="Проверить состав архива";
    form.form.prepend(adminField("Тип объекта",source),adminField("Постоянный ID",target));
    const preview=el("div");
    section.append(form.form,preview);
    const inventory=el("div","admin-list");
    section.append(el("h3","","Архивные операции"),inventory);
    const records=await api("/admin/archives");
    if(!records.length)inventory.append(adminInfo("Архив пока пуст."));
    records.forEach(item=>{
        const card=el("div","admin-subpanel");
        card.append(el("h3","","Операция "+item.id.slice(0,8)),
            el("p","muted",friendlyDate(item.created_at)+" · "+item.article_count+
                " статей · "+item.node_count+" разделов"));
        const details=el("details");
        details.append(el("summary","","Содержимое архивной операции"));
        item.archived_articles.forEach(a=>details.append(adminLink(
            "Статья "+a.number+" · "+displayLegalTitle(a.title),"/articles/"+a.id)));
        item.archived_nodes.forEach(n=>details.append(el("p","",n.number+" · "+displayLegalTitle(n.title))));
        card.append(details);
        const previewBtn=adminButton("Просмотреть восстановление",()=>adminSafe(async()=>{
            const result=await api("/archive-batches/"+item.id+"/preview");
            const summary=el("div","admin-preview");
            summary.append(el("strong","","Восстановление: "+result.article_ids.length+
                    " статей и "+result.node_ids.length+" разделов"));
            if(result.conflicts.length){
                summary.append(el("p","notice",
                    "Обнаружены конфликтующие действующие статьи: "+result.conflicts.length));
                result.conflicts.forEach(x=>{
                    const row=el("p");
                    row.append("Номер "+x.number+" · ",
                        adminLink("Действующая статья","/articles/"+x.active_id),
                        adminLink("Архивная статья","/articles/"+x.restoring_id));
                    summary.append(row);
                });
            }
            const ownerCode=item.archived_articles[0]?.code_id||
                item.archived_nodes[0]?.code_id||"";
            const destination=adminSelect([], "__unchanged");
            destination.append(new Option("Оставить исходное расположение","__unchanged"),
                new Option("Переместить в корень кодекса","__root"));
            if(ownerCode) {
                const hierarchy=await api("/codes/"+ownerCode+"/tree");
                const ownArchived=new Set(item.archived_nodes.map(n=>n.id));
                hierarchy.nodes.filter(n=>!ownArchived.has(n.id)).forEach(n=>
                    destination.append(new Option(n.number+" · "+displayLegalTitle(n.title),n.id)));
            }
            if(result.node_ids.length||result.article_ids.length===1)
                summary.append(adminField("Новое расположение восстанавливаемого объекта",destination));
            const agree=el("input");agree.type="checkbox";
            if(result.conflicts.length)summary.append(adminField(
                "Отдельно подтверждаю архивирование конфликтующих действующих статей",agree));
            const confirmRestore=adminButton("Восстановить",()=>adminSafe(async()=>{
                if(result.conflicts.length && !agree.checked){
                    throw Error("Подтвердите замену конфликтующих действующих статей.");
                }
                if(!confirm("Восстановить указанные архивные объекты?"))return;
                const body={batch_id:item.id,preview_hash:result.preview_hash,
                    confirm:true,confirm_swap:!!agree.checked};
                if(destination.value!=="__unchanged")
                    body.new_parent_id=destination.value==="__root"?null:destination.value;
                await api("/restore-batches",{method:"POST",headers:{
                    "Idempotency-Key":crypto.randomUUID()},body});
                notify("Объекты восстановлены.");
                go("/admin/archive");
            }),"primary");
            summary.append(confirmRestore);
            card.append(summary);
            previewBtn.disabled=true;
        }));
        card.append(previewBtn);inventory.append(card);
    });
    area.append(section);
}
async function adminHierarchy(area) {
    const section=adminSection("Настройка иерархии кодексов",
        "Переименование типов, правила вложенности, изменение порядка, перенос и исключение уровней.");
    const selected=adminSelect(allCodes.map(c=>[c.name,c.id]),
        new URLSearchParams(location.search).get("code")||allCodes[0]?.id);
    selected.onchange=()=>go("/admin/hierarchy?code="+encodeURIComponent(selected.value));
    section.append(adminField("Кодекс",selected));
    if(!selected.value){area.append(section);return;}
    const codeId=selected.value;
    const tree=await api("/codes/"+encodeURIComponent(codeId)+"/tree");
    const types=tree.types,nodes=tree.nodes;
    const allowedChoices=[["Корень кодекса","ROOT"],...types.map(t=>[t.name,t.id])];
    section.append(el("h3","","Типы структурных уровней"));
    types.forEach(type=>{
        const edit=adminForm(async()=>{
            await api("/hierarchy-types/"+type.id,{method:"PATCH",
                body:{name:title.value.trim(),level_order:Number(order.value),
                      allowed_parents:[...parents.querySelectorAll("input:checked")].map(i=>i.value)}});
            go("/admin/hierarchy?code="+encodeURIComponent(codeId));
        });
        const title=adminInput(type.name);
        const order=adminInput(type.level_order);order.type="number";order.step="1";
        const parents=el("div","admin-checks");
        const current=allowedParentTypes(type);
        allowedChoices.forEach(([name,id])=>{
            const input=el("input");input.type="checkbox";input.value=id;
            input.checked=current.includes(id);
            parents.append(adminField(name,input));
        });
        const head=el("div","admin-subpanel");
        edit.form.prepend(adminField("Название типа",title),
            adminField("Порядок уровня",order),
            el("strong","","Допустимые родительские типы"),parents);
        head.append(edit.form);
        head.append(adminButton("Исключить тип и поднять потомков",()=>adminSafe(async()=>{
            const p=await api("/hierarchy-types/"+type.id+"/retire/preview");
            const box=el("div","admin-preview");
            box.append(el("h3","","Изменение структуры после исключения типа"),
                el("p","","Будут подняты "+p.moved_nodes.length+" подразделов и "+
                       p.moved_articles.length+" статей."));
            if(p.conflicts.length)adminConflicts(box,{details:{conflicts:p.conflicts}});
            const confirmButton=adminButton("Подтвердить исключение типа",()=>adminSafe(async()=>{
                if(p.conflicts.length)throw Error("Сначала исправьте конфликты.");
                if(!confirm("Исключить тип? Все его экземпляры будут архивированы."))return;
                await api("/hierarchy-types/"+type.id+"/retire",{method:"POST",
                    body:{confirm:true,preview_hash:p.preview_hash}});
                go("/admin/hierarchy?code="+encodeURIComponent(codeId));
            }),"danger");
            if(p.conflicts.length)confirmButton.disabled=true;
            box.append(confirmButton);
            head.append(box);
        }),"danger"));
        section.append(head);
    });
    section.append(el("h3","","Перемещение разделов"));
    const moving=adminSelect(nodes.map(n=>[n.number+" · "+displayLegalTitle(n.title),n.id]));
    const destination=adminSelect(nodes.map(n=>[n.number+" · "+displayLegalTitle(n.title),n.id]),
        null,"Корень кодекса");
    const move=adminButton("Предварительный просмотр переноса",()=>adminSafe(async()=>{
        if(!moving.value)throw Error("Выберите раздел.");
        const body={new_parent_id:destination.value||null};
        const plan=await api("/nodes/"+moving.value+"/move/preview",{method:"POST",body});
        movePreview.replaceChildren(el("p","",
            "В переносе: "+(plan.nodes?.length||plan.node_count||0)+" подразделов; "+
            (plan.articles?.length||plan.article_count||0)+" статей."));
        const pos=adminInput(0);pos.type="number";pos.min="0";pos.step="1";
        movePreview.append(adminField("Позиция вставки, если целевая группа имеет ручной порядок",pos));
        const execute=adminButton("Подтвердить перемещение",()=>adminSafe(async()=>{
            if(!confirm("Переместить выбранный раздел и все дочерние объекты?"))return;
            await api("/nodes/"+moving.value+"/move",{method:"POST",
                body:{...body,preview_hash:plan.preview_hash,insert_at:Number(pos.value),confirm:true}});
            go("/admin/hierarchy?code="+encodeURIComponent(codeId));
        }),"primary");
        movePreview.append(execute);
    }));
    const movePreview=el("div","admin-preview");
    section.append(adminField("Переместить",moving),
        adminField("Новый родитель",destination),move,movePreview);
    section.append(el("h3","","Порядок элементов в группе"));
    const groups=new Map();
    nodes.forEach(n=>{
        const key=(n.parent_id||"ROOT")+"|"+n.type_id;
        if(!groups.has(key))groups.set(key,[]);
        groups.get(key).push(n);
    });
    const groupSelect=adminSelect([...groups].map(([key,items])=>{
        const node=nodes.find(n=>n.id===items[0].parent_id);
        const type=types.find(t=>t.id===items[0].type_id);
        return [(node?node.title:"Корень")+" / "+(type?type.name:"Раздел"),key];
    }));
    const ordering=el("div","admin-order-list");
    const draw=()=>{
        ordering.replaceChildren();
        const list=[...(groups.get(groupSelect.value)||[])];
        if(!list.length)return;
        let order=list;
        function paint(){
            ordering.replaceChildren();
            order.forEach((n,i)=>{
                const row=el("div","admin-order-row");
                row.draggable=true;
                row.title="Перетащите элемент для изменения порядка.";
                row.addEventListener("dragstart",event=>{
                    event.dataTransfer.setData("text/plain",String(i));
                    event.dataTransfer.effectAllowed="move";
                });
                row.addEventListener("dragover",event=>event.preventDefault());
                row.addEventListener("drop",event=>{
                    event.preventDefault();
                    const from=Number(event.dataTransfer.getData("text/plain"));
                    if(!Number.isInteger(from)||from<0||from>=order.length||from===i)return;
                    const [moved]=order.splice(from,1);
                    order.splice(i,0,moved);
                    paint();
                });
                row.append(el("span","",n.number+" · "+displayLegalTitle(n.title)));
                row.append(adminButton("↑",()=>{if(i){[order[i-1],order[i]]=[order[i],order[i-1]];paint();}}));
                row.append(adminButton("↓",()=>{if(i<order.length-1){[order[i+1],order[i]]=[order[i],order[i+1]];paint();}}));
                ordering.append(row);
            });
        }
        paint();
        saveOrder.onclick=()=>adminSafe(async()=>{
            const [parent,type]=groupSelect.value.split("|");
            await api("/nodes/order",{method:"POST",body:{code_id:codeId,
                parent_id:parent==="ROOT"?null:parent,type_id:type,mode:"manual",
                ordered_ids:order.map(n=>n.id)}});
            notify("Ручной порядок сохранён.");
        });
        autoOrder.onclick=()=>adminSafe(async()=>{
            const [parent,type]=groupSelect.value.split("|");
            await api("/nodes/order",{method:"POST",body:{code_id:codeId,
                parent_id:parent==="ROOT"?null:parent,type_id:type,mode:"auto"}});
            go("/admin/hierarchy?code="+encodeURIComponent(codeId));
        });
    };
    const saveOrder=adminButton("Сохранить ручной порядок",()=>{});
    const autoOrder=adminButton("Сортировать по номерам",()=>{});
    groupSelect.onchange=draw;
    section.append(adminField("Группа структурных элементов",groupSelect),
        ordering,saveOrder,autoOrder);
    draw();area.append(section);
}
async function adminAudit(area) {
    const section=adminSection("Закрытый журнал административных операций",
        "Доступен только суперадминистратору. Записи показывают действие, объект и дату.");
    const list=await api("/admin/audit?limit=200");
    if(!list.length)section.append(adminInfo("Записей нет."));
    list.forEach(item=>{
        const row=el("div","admin-list-row");
        const name=el("div");
        name.append(el("strong","",item.action),
            el("small","",item.object_type+" · "+item.object_id+" · "+item.user_id+
                      " · "+friendlyDate(item.created_at)));
        const details=el("details");
        details.append(el("summary","","Состояние до/после"));
        const pre=el("pre","admin-json",JSON.stringify({
            before:JSON.parse(item.before_json||"{}"),after:JSON.parse(item.after_json||"{}")
        },null,2));
        details.append(pre);
        row.append(name,details);
        section.append(row);
    });
    area.append(section);
}
