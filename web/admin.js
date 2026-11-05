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
    else if(section==="hierarchy") await adminHierarchyWorkbench(area);
    else if(section==="categories") await adminCategories(area);
    else if(section==="relations") await adminRelationsWorkbench(area);
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
/* Redesigned workbench lives in admin-workbench.js. */
async function adminArchive(area) {
    const section=adminSection("Архив статей и разделов",
        "Здесь можно убрать статью или раздел из действующей библиотеки и при необходимости вернуть обратно. Тексты и история изменений сохранятся.");
    const source=adminSelect([["Статья","article_id"],["Раздел и вложенная структура","node_id"]],
        new URLSearchParams(location.search).has("node")?"node_id":"article_id");
    const target=adminInput(new URLSearchParams(location.search).get("article")||
        new URLSearchParams(location.search).get("node")||"","ID статьи или раздела");
    const form=adminForm(async()=>{
        const body={[source.value]:target.value.trim()};
        if(!body[source.value])throw Error("Укажите ID объекта для архивирования.");
        const plan=await api("/archive/preview",{method:"POST",body});
        const panel=el("div","admin-preview");
        panel.append(el("h3","","Что будет перемещено в архив"),
            el("p","","Статей: "+plan.article_count+" · Разделов: "+plan.node_count),
            el("p","muted","Материалы исчезнут из действующего каталога, но останутся доступны в архиве для восстановления."));

        const confirmButton=adminButton("Подтвердить архивирование",()=>adminSafe(async()=>{
            if(!confirm("Переместить выбранные материалы в архив? Их можно будет восстановить."))return;
            await api("/archive-batches",{method:"POST",headers:{
                "Idempotency-Key":crypto.randomUUID()},
                body:{...body,preview_hash:plan.preview_hash,confirm:true}});
            notify("Материалы перемещены в архив.");
            go("/admin/archive");
        }),"danger");
        panel.append(confirmButton);
        preview.replaceChildren(panel);
    });
    form.save.textContent="Посмотреть, что попадёт в архив";
    form.form.prepend(adminField("Тип объекта",source),adminField("Постоянный ID",target));
    const preview=el("div");
    section.append(form.form,preview);
    const inventory=el("div","admin-list");
    section.append(el("h3","","Ранее архивированные материалы"),inventory);
    const records=await api("/admin/archives");
    if(!records.length)inventory.append(adminInfo("Архив пока пуст."));
    records.forEach(item=>{
        const card=el("div","admin-subpanel");
        card.append(el("h3","","Архив от "+friendlyDate(item.created_at)),
            el("p","muted",item.article_count+" статей · "+item.node_count+" разделов"));
        const details=el("details");
        details.append(el("summary","","Показать список материалов"));
        item.archived_articles.forEach(a=>details.append(adminLink(
            "Статья "+a.number+" · "+displayLegalTitle(a.title),"/articles/"+a.id)));
        item.archived_nodes.forEach(n=>details.append(el("p","",n.number+" · "+displayLegalTitle(n.title))));
        card.append(details);
        const previewBtn=adminButton("Восстановить материалы",()=>adminSafe(async()=>{
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
                if(!confirm("Вернуть выбранные материалы в действующую библиотеку?"))return;
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
/* Redesigned workbench lives in admin-workbench.js. */
async function adminAudit(area) {
    const section=adminSection("Закрытый журнал административных операций",
        "Доступен только суперадминистратору. Журнал показывает источник действия, результат, объект и время без токенов и текстов запросов.");
    const list=await api("/admin/audit?limit=200");
    if(!list.length)section.append(adminInfo("Записей нет."));
    list.forEach(item=>{
        const row=el("div","admin-list-row");
        const name=el("div");
        const outcome=item.result||"success";
        const state=el("span","audit-result "+(outcome==="success"?"audit-ok":"audit-error"),
            outcome==="success"?"Выполнено":outcome==="forbidden"?"Отклонено доступом":"Ошибка операции");
        name.append(el("strong","",item.action),state,
            el("small","",item.object_type+" · "+item.object_id+" · "+item.user_id+
                      " · "+friendlyDate(item.created_at)),
            el("small","",item.source||"api"));
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
