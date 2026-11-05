/* Focused administrative workspaces for hierarchy and legal relations. */
"use strict";

function workbenchHeading(eyebrow,title,description) {
    const top=el("div","wb-heading");
    const intro=el("div","wb-heading-copy");
    intro.append(el("span","admin-eyebrow",eyebrow),el("h3","",title),
        el("p","muted",description));
    top.append(intro);
    return top;
}
function workbenchSteps(steps,current,activate) {
    const nav=el("nav","wb-steps");
    nav.setAttribute("aria-label","Разделы рабочей области");
    steps.forEach(([id,name],index)=>{
        const button=el("button","wb-step",name);
        button.type="button";
        button.dataset.step=id;
        button.setAttribute("aria-current",current===id?"step":"false");
        button.onclick=()=>activate(id);
        nav.append(button);
    });
    return nav;
}
function wbEmpty(headline,description) {
    const item=el("div","wb-empty");
    item.append(el("span","wb-empty-symbol","§"),el("strong","",headline),
        el("p","",description));
    return item;
}
function wbStatus(text,kind) {
    return el("span","wb-tag "+(kind||""),text);
}
function wbInlineAction(label,fn,kind){
    const button=adminButton(label,()=>adminSafe(fn),kind||"ghost");
    return button;
}
function wbSelectTo(candidates,selectedId){
    return adminSelect(candidates.map(x=>[x.name,x.id]),selectedId);
}
function wbSelectCat(categories,id,blank){
    return adminSelect(categories.map(c=>[c.name,c.id]),id,blank||"Любая категория");
}
async function adminHierarchyWorkbench(area){
    const section=adminSection("Иерархия кодекса",
        "Здесь определяется, как устроен кодекс: части, разделы и главы. Можно изменить вложенность и порядок, не меняя текст статей.");
    section.classList.add("admin-hierarchy-workbench");
    const select=wbSelectTo(allCodes,new URLSearchParams(location.search).get("code")||allCodes[0]?.id);
    select.id="hierarchyCodeSelect";
    select.setAttribute("aria-label","Выбрать кодекс");
    select.onchange=()=>go("/admin/hierarchy?code="+encodeURIComponent(select.value));
    const selectLine=el("div","wb-context");
    selectLine.append(adminField("Кодекс для настройки",select));
    if(select.value)selectLine.append(adminLink("Открыть кодекс","/codes/"+select.value));
    section.append(selectLine);
    if(!select.value){
        section.append(wbEmpty("Кодексы пока не созданы","Создайте первый кодекс, чтобы настроить его структуру."),
            adminLink("Создать кодекс","/admin/codes?new=1"));
        area.append(section);return;
    }
    const codeId=select.value;
    const tree=await api("/codes/"+encodeURIComponent(codeId)+"/tree");
    const types=tree.types;
    const nodes=tree.nodes;
    const nodesById=new Map(nodes.map(n=>[n.id,n]));
    const typesById=new Map(types.map(t=>[t.id,t]));
    const choices=[["Корень кодекса","ROOT"],...types.map(t=>[t.name,t.id])];
    const summary=el("div","wb-summary");
    summary.append(wbStatus(types.length+" уровней"),wbStatus(nodes.length+" разделов"));
    section.append(summary);
    const outer=el("div","wb-layout");
    const navigation=el("aside","wb-nav");
    const content=el("div","wb-content");
    const tabs=[
        ["levels","Какие есть уровни"],
        ["move","Куда входит раздел"],
        ["order","В каком порядке показывать"]
    ];
    let chosen=new URLSearchParams(location.search).get("view")||"levels";
    if(!tabs.some(x=>x[0]===chosen))chosen="levels";
    const nav=workbenchSteps(tabs,chosen,activate);
    navigation.append(nav);
    const navHint=el("p","wb-nav-hint","Изменения в структуре кодекса не создают новых редакций текста статей.");
    navigation.append(navHint);
    outer.append(navigation,content);
    section.append(outer);
    area.append(section);

    function activate(id){
        chosen=id;
        nav.querySelectorAll(".wb-step").forEach(button=>{
            const active=button.dataset.step===id;
            button.classList.toggle("active",active);
            button.setAttribute("aria-current",active?"step":"false");
        });
        const query=new URLSearchParams({code:codeId,view:id});
        history.replaceState(history.state,"","/admin/hierarchy?"+query);
        content.replaceChildren();
        if(id==="levels")levelScreen();
        else if(id==="move")moveScreen();
        else orderScreen();
    }
    function levelScreen(){
        const header=workbenchHeading("КОНСТРУКТОР","Уровни кодекса",
            "Например: часть → раздел → глава. Выберите уровень для изменения его правил.");
        const createButton=adminButton("＋ Добавить уровень",()=>{
            create.hidden=!create.hidden;
            createButton.setAttribute("aria-expanded",String(!create.hidden));
            if(!create.hidden)create.querySelector("input").focus();
        },"primary");
        createButton.id="hierarchyAddLevel";
        createButton.setAttribute("aria-controls","hierarchyLevelCreator");
        createButton.setAttribute("aria-expanded","false");
        header.append(createButton);
        content.append(header);
        const create=el("div","wb-create");
        create.id="hierarchyLevelCreator";create.hidden=true;
        const createForm=adminForm(async()=>{
            const name=nameInput.value.trim();
            if(!name)throw Error("Введите название уровня.");
            const allowed=[...createParents.querySelectorAll("input:checked")].map(x=>x.value);
            if(!allowed.length)throw Error("Выберите хотя бы одного допустимого родителя.");
            await api("/codes/"+codeId+"/hierarchy-types",{method:"POST",body:{
                name,level_order:Number(levelInput.value),allowed_parents:allowed,
                number_format:format.value}});
            go("/admin/hierarchy?code="+codeId+"&view=levels");
        });
        createForm.save.textContent="Создать уровень";
        const nameInput=adminInput("","Например: Подглава");nameInput.required=true;
        const levelInput=adminInput(types.length+1);levelInput.type="number";levelInput.min="1";
        const format=adminSelect([["Смешанная нумерация","mixed"],["Цифровая","numeric"],
            ["Римская","roman"],["Буквенная","alpha"],["Без номера","none"]],"mixed");
        const createParents=el("div","wb-check-grid");
        choices.forEach(([title,id])=>{
            const input=el("input");input.type="checkbox";input.value=id;
            if(id==="ROOT")input.checked=true;
            createParents.append(adminField(title,input));
        });
        const newGrid=el("div","wb-form-grid");
        newGrid.append(adminField("Название уровня",nameInput),
            adminField("Порядок",levelInput),adminField("Нумерация",format));
        createForm.form.prepend(el("h4","","Новый уровень структуры"),newGrid,
            el("strong","","Где разрешено размещать этот уровень"),createParents);
        create.append(createForm.form);content.append(create);

        const inner=el("div","wb-split");
        const picker=el("div","wb-picker");
        const editor=el("div","wb-editor");
        inner.append(picker,editor);content.append(inner);
        let current=types[0]?.id||null;
        function selectType(id){
            current=id;
            picker.querySelectorAll(".wb-picker-item").forEach(btn=>{
                const active=btn.dataset.id===id;
                btn.classList.toggle("active",active);
                btn.setAttribute("aria-pressed",String(active));
            });
            renderEditor();
        }
        if(!types.length)picker.append(wbEmpty("Нет уровней",
            "Добавьте тип структуры, чтобы организовать кодекс."));
        types.forEach(type=>{
            const btn=el("button","wb-picker-item");
            btn.type="button";btn.dataset.id=type.id;
            btn.append(el("strong","",type.name),
                el("span","", "Уровень "+type.level_order+
                    " · "+nodes.filter(n=>n.type_id===type.id).length+" элементов"));
            btn.onclick=()=>selectType(type.id);
            picker.append(btn);
        });
        function renderEditor(){
            editor.replaceChildren();
            const type=typesById.get(current);
            if(!type){editor.append(wbEmpty("Выберите уровень","Настройки появятся здесь."));return;}
            editor.append(el("h4","",type.name),
                el("p","muted","Измените название, расположение в общей последовательности и допустимые родительские уровни."));
            const form=adminForm(async()=>{
                const parents=[...parentList.querySelectorAll("input:checked")].map(x=>x.value);
                if(!title.value.trim())throw Error("Название уровня не может быть пустым.");
                if(!parents.length)throw Error("Выберите допустимого родителя.");
                await api("/hierarchy-types/"+type.id,{method:"PATCH",body:{
                    name:title.value.trim(),level_order:Number(order.value),
                    allowed_parents:parents}});
                go("/admin/hierarchy?code="+codeId+"&view=levels");
            });
            form.save.textContent="Сохранить настройки";
            const title=adminInput(type.name);
            title.required=true;
            const order=adminInput(type.level_order);order.type="number";order.min="1";order.step="1";
            const grid=el("div","wb-form-grid");
            grid.append(adminField("Название уровня",title),
                adminField("Место в структуре",order));
            const parentList=el("div","wb-check-grid");
            const existing=allowedParentTypes(type);
            choices.filter(([name,id])=>id!==type.id).forEach(([name,id])=>{
                const item=el("input");item.type="checkbox";item.value=id;
                item.checked=existing.includes(id);
                parentList.append(adminField(name,item));
            });
            form.form.prepend(grid,
                el("strong","","Разрешённые родительские уровни"),
                el("p","wb-explain","Новый раздел можно будет разместить только внутри отмеченных типов. Изменение правил проверяется на уже созданных разделах."),
                parentList);
            editor.append(form.form);
            const danger=el("details","wb-danger");
            danger.append(el("summary","","Исключить этот уровень"),
                el("p","wb-explain","Операция не удаляет статьи: система предварительно проверяет, куда переместятся дочерние разделы и тексты."));
            const preview=el("div","wb-operation-preview");
            danger.append(wbInlineAction("Проверить последствия",async()=>{
                const plan=await api("/hierarchy-types/"+type.id+"/retire/preview");
                preview.replaceChildren();
                preview.append(el("strong","","Предварительный расчёт"),
                    el("p","", "Поднимутся "+plan.moved_nodes.length+" разделов и "+
                        plan.moved_articles.length+" статей."));
                if(plan.conflicts.length){
                    const list=el("div","wb-conflict-list");
                    adminConflicts(list,{details:{conflicts:plan.conflicts}});
                    preview.append(list);
                }else{
                    preview.append(wbInlineAction("Подтвердить исключение уровня",async()=>{
                        if(!confirm("Исключить тип «"+type.name+"»? Его элементы будут архивированы, а потомки перенесены."))return;
                        await api("/hierarchy-types/"+type.id+"/retire",{
                            method:"POST",body:{confirm:true,preview_hash:plan.preview_hash}});
                        go("/admin/hierarchy?code="+codeId+"&view=levels");
                    },"danger"));
                }
            }),preview);
            editor.append(danger);
        }
        selectType(current);
    }
    function moveScreen(){
        const header=workbenchHeading("ОРГАНИЗАЦИЯ","Переместить раздел",
            "Выберите раздел и новый родительский уровень. Перед изменением система проверит перенос всего содержимого.");
        content.append(header);
        if(!nodes.length){content.append(wbEmpty("Нет разделов","Создайте раздел в выбранном кодексе."));return;}
        const form=el("div","wb-task");
        const moving=adminSelect(nodes.map(n=>[(n.number?n.number+" · ":"")+displayLegalTitle(n.title),n.id]));
        moving.id="hierarchyMovingNode";
        const destination=adminSelect(nodes.map(n=>[(n.number?n.number+" · ":"")+displayLegalTitle(n.title),n.id]),
            null,"Корень кодекса");
        destination.id="hierarchyMoveDestination";
        const grid=el("div","wb-form-grid");
        grid.append(adminField("Что переместить",moving),
            adminField("Куда переместить",destination));
        const operation=el("div","wb-operation-preview");
        const button=wbInlineAction("Проверить перенос",async()=>{
            if(!moving.value)throw Error("Выберите раздел.");
            if(destination.value===moving.value)throw Error("Раздел нельзя перенести внутрь самого себя.");
            const body={new_parent_id:destination.value||null};
            const plan=await api("/nodes/"+moving.value+"/move/preview",{
                method:"POST",body});
            operation.replaceChildren(el("h4","","Результат проверки"),
                el("p","","Изменят расположение: "+(plan.nodes?.length||plan.node_count||0)+
                    " разделов, "+(plan.articles?.length||plan.article_count||0)+" статей."));
            if(plan.conflicts?.length){adminConflicts(operation,{details:{conflicts:plan.conflicts}});return;}
            const position=adminInput(0);
            position.type="number";position.min="0";position.step="1";
            operation.append(adminField("Позиция при ручной сортировке (необязательно)",position));
            operation.append(wbInlineAction("Подтвердить перемещение",async()=>{
                if(!confirm("Переместить выбранный раздел со всем содержимым?"))return;
                await api("/nodes/"+moving.value+"/move",{method:"POST",
                    body:{...body,preview_hash:plan.preview_hash,
                        insert_at:Number(position.value),confirm:true}});
                go("/admin/hierarchy?code="+codeId+"&view=move");
            },"primary"));
        },"primary");
        moving.onchange=()=>operation.replaceChildren();
        destination.onchange=()=>operation.replaceChildren();
        form.append(grid,el("p","wb-explain",
            "Перемещение не меняет постоянные ссылки на разделы и статьи."),button,operation);
        content.append(form);
    }
    function orderScreen(){
        content.append(workbenchHeading("ПОСЛЕДОВАТЕЛЬНОСТЬ","Порядок разделов",
            "Меняйте порядок стрелками или перетаскиванием. Это влияет только на одну выбранную группу, а не на номера статей."));
        const groups=new Map();
        nodes.forEach(n=>{
            const key=(n.parent_id||"ROOT")+"|"+n.type_id;
            if(!groups.has(key))groups.set(key,[]);
            groups.get(key).push(n);
        });
        if(!groups.size){content.append(wbEmpty("Нет разделов для сортировки",
            "Добавьте структурные элементы кодекса."));return;}
        const choice=adminSelect([...groups].map(([key,children])=>{
            const first=children[0];
            const parent=nodesById.get(first.parent_id);
            const type=typesById.get(first.type_id);
            return [(parent?displayLegalTitle(parent.title):"Корень кодекса")+" / "+
                (type?type.name:"Раздел")+" · "+children.length,key];
        }));
        choice.id="hierarchySortGroup";
        const panel=el("div","wb-task");
        const entries=el("div","wb-sort-list");
        panel.append(adminField("Группа для сортировки",choice),entries);
        content.append(panel);
        let order=[];
        function repaint(){
            entries.replaceChildren();
            order.forEach((n,i)=>{
                const item=el("div","wb-sort-row");
                item.draggable=true;
                item.append(el("span","wb-sort-number",String(i+1)),
                    el("span","wb-sort-name",n.number+" · "+displayLegalTitle(n.title)));
                const up=adminButton("↑",()=>{
                    if(i>0){[order[i-1],order[i]]=[order[i],order[i-1]];repaint();}
                });
                up.setAttribute("aria-label","Поднять "+n.title);
                const down=adminButton("↓",()=>{
                    if(i<order.length-1){[order[i+1],order[i]]=[order[i],order[i+1]];repaint();}
                });
                down.setAttribute("aria-label","Опустить "+n.title);
                item.append(up,down);
                item.addEventListener("dragstart",evt=>{
                    evt.dataTransfer.setData("text/plain",String(i));
                    evt.dataTransfer.effectAllowed="move";
                });
                item.addEventListener("dragover",evt=>evt.preventDefault());
                item.addEventListener("drop",evt=>{
                    evt.preventDefault();
                    const from=Number(evt.dataTransfer.getData("text/plain"));
                    if(!Number.isInteger(from)||from<0||from>=order.length||from===i)return;
                    const [moved]=order.splice(from,1);order.splice(i,0,moved);repaint();
                });
                entries.append(item);
            });
        }
        const controls=el("div","wb-actions");
        const save=wbInlineAction("Сохранить ручной порядок",async()=>{
            const [parent,type]=choice.value.split("|");
            await api("/nodes/order",{method:"POST",body:{
                code_id:codeId,parent_id:parent==="ROOT"?null:parent,
                type_id:type,mode:"manual",ordered_ids:order.map(n=>n.id)}});
            notify("Порядок разделов сохранён.");
        },"primary");
        const automatic=wbInlineAction("Вернуть порядок по номерам",async()=>{
            if(!confirm("Вернуть сортировку по юридическим номерам для этой группы?"))return;
            const [parent,type]=choice.value.split("|");
            await api("/nodes/order",{method:"POST",body:{
                code_id:codeId,parent_id:parent==="ROOT"?null:parent,
                type_id:type,mode:"auto"}});
            go("/admin/hierarchy?code="+codeId+"&view=order");
        });
        controls.append(save,automatic);panel.append(controls);
        choice.onchange=()=>{order=[...(groups.get(choice.value)||[])];repaint();};
        choice.onchange();
    }
    activate(chosen);
}
async function adminRelationsWorkbench(area){
    const section=adminSection("Связи между статьями",
        "Свяжите нормы, которые дополняют или уточняют друг друга. Можно посмотреть существующие связи, изменить их правила и восстановить отключённые.");
    section.classList.add("admin-relations-workbench");
    const [types,categories]=await Promise.all([api("/relation-types"),api("/categories")]);
    const categoryById=new Map(categories.map(c=>[c.id,c.name]));
    const ruleById=new Map(types.map(t=>[t.id,t]));
    const count=el("div","wb-summary");
    count.append(wbStatus(types.length+" видов связей"),
        wbStatus("Проверка совместимости на сервере"));
    section.append(count);
    const tabs=[["rules","Типы связей"],["assign","Добавить связь"],["review","Связи статьи"]];
    const outer=el("div","wb-layout");
    const sidebar=el("aside","wb-nav");
    const content=el("div","wb-content");
    let chosen=new URLSearchParams(location.search).get("view")||"rules";
    if(!tabs.some(([k])=>k===chosen))chosen="rules";
    const nav=workbenchSteps(tabs,chosen,activate);
    sidebar.append(nav,el("p","wb-nav-hint",
        "Связи создаются вручную и не восстанавливаются автоматически после архива."));
    outer.append(sidebar,content);
    section.append(outer);
    area.append(section);
    function activate(view){
        chosen=view;
        nav.querySelectorAll(".wb-step").forEach(btn=>{
            const active=btn.dataset.step===view;
            btn.classList.toggle("active",active);
            btn.setAttribute("aria-current",active?"step":"false");
        });
        const query=new URLSearchParams({view});
        const article=new URLSearchParams(location.search).get("article");
        if(article)query.set("article",article);
        history.replaceState(history.state,"","/admin/relations?"+query);
        content.replaceChildren();
        if(view==="rules")rulesScreen();
        else if(view==="assign")assignScreen();
        else reviewScreen();
    }
    function ruleTitle(t){
        return (categoryById.get(t.source_category)||"Любая статья")+" → "+
               (categoryById.get(t.target_category)||"Любая статья");
    }
    function rulesScreen(){
        const header=workbenchHeading("ПРАВИЛА","Виды юридических связей",
            "Выберите правило, чтобы изменить допустимые категории и ограничения. Действующие связи проверяются перед сохранением.");
        const add=adminButton("＋ Новый вид",()=>{
            creator.hidden=!creator.hidden;
            add.setAttribute("aria-expanded",String(!creator.hidden));
            if(!creator.hidden)creator.querySelector("input").focus();
        },"primary");
        add.id="relationsAddType";
        add.setAttribute("aria-controls","relationTypeCreator");
        add.setAttribute("aria-expanded","false");
        header.append(add);
        content.append(header);
        const creator=el("div","wb-create");
        creator.id="relationTypeCreator";creator.hidden=true;
        const typeForm=adminForm(async()=>{
            if(!newName.value.trim())throw Error("Введите название вида связи.");
            await api("/relation-types",{method:"POST",body:{
                name:newName.value.trim(),source_category:newSource.value||null,
                target_category:newTarget.value||null,exclusive_target:onlyOne.checked}});
            go("/admin/relations?view=rules");
        });
        typeForm.save.textContent="Создать вид связи";
        const newName=adminInput("","Например: Обвинение → мера наказания");
        newName.required=true;
        const newSource=wbSelectCat(categories,null),newTarget=wbSelectCat(categories,null);
        const onlyOne=el("input");onlyOne.type="checkbox";
        const newGrid=el("div","wb-form-grid");
        newGrid.append(adminField("Название",newName),
            adminField("Категория исходной статьи",newSource),
            adminField("Категория целевой статьи",newTarget));
        typeForm.form.prepend(el("h4","","Создать правило"),newGrid,
            adminField("Запретить несколько исходящих назначений на одну целевую статью",onlyOne));
        creator.append(typeForm.form);content.append(creator);
        const split=el("div","wb-split");
        const picker=el("div","wb-picker");
        const editor=el("div","wb-editor");
        split.append(picker,editor);content.append(split);
        let selectedId=types[0]?.id||null;
        if(!types.length)picker.append(wbEmpty("Пока нет видов связей",
            "Создайте правило, чтобы связать статьи."));
        types.forEach(type=>{
            const btn=el("button","wb-picker-item");
            btn.type="button";btn.dataset.id=type.id;
            btn.append(el("strong","",type.name),el("span","",ruleTitle(type)));
            btn.onclick=()=>selectRule(type.id);
            picker.append(btn);
        });
        function selectRule(id){
            selectedId=id;
            picker.querySelectorAll(".wb-picker-item").forEach(button=>{
                const active=button.dataset.id===id;
                button.classList.toggle("active",active);
                button.setAttribute("aria-pressed",String(active));
            });
            editor.replaceChildren();
            const type=ruleById.get(id);
            if(!type){editor.append(wbEmpty("Выберите правило","Настройки появятся здесь."));return;}
            editor.append(el("h4","",type.name),
                el("p","muted","Источник и цель — статьи с указанными категориями. «Любая» снимает ограничение категории."));
            const form=adminForm(async()=>{
                if(!name.value.trim())throw Error("Название связи обязательно.");
                await api("/relation-types/"+type.id,{method:"PATCH",body:{
                    name:name.value.trim(),source_category:source.value||null,
                    target_category:target.value||null,exclusive_target:exclusive.checked}});
                go("/admin/relations?view=rules");
            });
            form.save.textContent="Сохранить правило";
            const name=adminInput(type.name);name.required=true;
            const source=wbSelectCat(categories,type.source_category);
            const target=wbSelectCat(categories,type.target_category);
            const exclusive=el("input");exclusive.type="checkbox";
            exclusive.checked=!!type.exclusive_target;
            const grid=el("div","wb-form-grid");
            grid.append(adminField("Название вида связи",name),
                adminField("Категория источника",source),
                adminField("Категория цели",target));
            const hint=el("p","wb-explain");
            function redrawHint(){
                hint.textContent=(categoryById.get(source.value)||"Любая статья")+
                    " → "+(categoryById.get(target.value)||"Любая статья")+
                    (exclusive.checked ?
                    ". Одна цель не может быть назначена повторно в рамках этого правила." :
                    ". Несколько назначений допускаются, если нет других конфликтов.");
            }
            [source,target,exclusive].forEach(control=>control.onchange=redrawHint);
            form.form.prepend(grid,adminField("Только одна связь на одну целевую статью",exclusive),hint);
            redrawHint();
            editor.append(form.form);
        }
        selectRule(selectedId);
    }
    async function assignScreen(){
        content.append(workbenchHeading("НАЗНАЧЕНИЕ","Связать две статьи",
            "Сначала выберите вид связи, затем найдите исходную и целевую статьи по номеру или названию."));
        if(!types.length){content.append(wbEmpty("Сначала создайте вид связи",
            "Перейдите в раздел «Виды и правила»."));return;}
        const form=adminForm(async()=>{
            if(!sourceFinder.select.value || !targetFinder.select.value){
                throw Error("Найдите и выберите обе статьи.");
            }
            if(sourceFinder.select.value===targetFinder.select.value){
                throw Error("Нельзя связать статью с самой собой.");
            }
            await api("/relations",{method:"POST",body:{
                type_id:kind.value,source_id:sourceFinder.select.value,
                target_id:targetFinder.select.value}});
            notify("Связь создана. Её можно просмотреть в журнале связей статьи.");
            go("/admin/relations?view=review&article="+encodeURIComponent(sourceFinder.select.value));
        });
        form.save.textContent="Создать юридическую связь";
        const kind=adminSelect(types.map(t=>[t.name,t.id]));
        kind.id="relationAssignType";
        const hint=el("p","wb-explain");
        function describe(){
            const type=ruleById.get(kind.value);
            hint.textContent=type ? ruleTitle(type)+
                (type.exclusive_target?" · Повторное назначение цели запрещено.":"") : "";
        }
        kind.onchange=describe;
        const sourceFinder=await adminArticleFinder("От какой статьи",new URLSearchParams(location.search).get("source")||"");
        const targetFinder=await adminArticleFinder("К какой статье","");
        sourceFinder.wrap.classList.add("wb-finder");
        targetFinder.wrap.classList.add("wb-finder");
        form.form.prepend(adminField("Вид юридической связи",kind),hint,
            sourceFinder.wrap,targetFinder.wrap);
        content.append(form.form);
        describe();
    }
    async function reviewScreen(){
        content.append(workbenchHeading("ДЕЙСТВУЮЩИЕ И АРХИВНЫЕ","Связи одной статьи",
            "Откройте существующие отношения, отключите ошибочное назначение или выберите совместимую архивную связь для восстановления."));
        const articleFinder=await adminArticleFinder("Какую статью посмотреть",new URLSearchParams(location.search).get("article")||"");
        const id=adminInput(new URLSearchParams(location.search).get("article")||"",
            "Постоянный ID статьи");
        id.id="relationReviewArticleId";
        const row=el("div","wb-context");
        row.append(adminField("Постоянный ID статьи",id));
        const fromSearch=adminButton("Использовать найденную",()=>{
            if(articleFinder.select.value)id.value=articleFinder.select.value;
        });
        row.append(fromSearch);
        articleFinder.select.addEventListener("change",()=>{id.value=articleFinder.select.value; if(id.value)adminSafe(draw);});
        const load=wbInlineAction("Показать связи",draw,"primary");
        row.append(load);
        const result=el("div","wb-review-results");
        content.append(articleFinder.wrap,row,result);
        async function draw(){
            const articleId=id.value.trim();
            if(!articleId)throw Error("Найдите статью или укажите её ID.");
            result.replaceChildren(el("p","muted","Загрузка действующих и архивных связей…"));
            const [active,candidates]=await Promise.all([
                api("/articles/"+encodeURIComponent(articleId)+"/relations"),
                api("/articles/"+encodeURIComponent(articleId)+"/relations/restore-candidates")]);
            result.replaceChildren();
            const activeSection=el("section","wb-result-section");
            activeSection.append(el("h4","","Действующие связи"),
                wbStatus(active.items.length+" связей"));
            if(!active.items.length)activeSection.append(
                wbEmpty("Действующих связей нет","У этой статьи пока нет действующих назначений."));
            active.items.forEach(link=>{
                const other=link.source_id===articleId?link.target_id:link.source_id;
                const otherTitle=link.source_id===articleId?link.target_title:link.source_title;
                const card=el("div","wb-relationship");
                const details=el("div","wb-relationship-copy");
                details.append(el("strong","",displayLegalTitle(otherTitle)),
                    el("span","",link.type_name||"Юридическая связь"));
                const controls=el("div","wb-actions");
                controls.append(adminLink("Открыть статью","/articles/"+other),
                    wbInlineAction("Отключить",async()=>{
                        if(!confirm("Отключить связь? Для статьи будет создана новая редакция."))return;
                        await api("/relations/"+link.id+"/deactivate",{
                            method:"POST",body:{reason:"manual"}});
                        await draw();
                    },"danger"));
                card.append(details,controls);activeSection.append(card);
            });
            const history=el("section","wb-result-section");
            history.append(el("h4","","Ранее отключённые связи"),
                wbStatus(candidates.length+" записей"));
            if(!candidates.length)history.append(
                wbEmpty("Восстанавливать нечего","Архивных связей для этой статьи не найдено."));
            candidates.forEach(link=>{
                const card=el("div","wb-relationship");
                const copy=el("div","wb-relationship-copy");
                copy.append(el("strong","",link.type_name),
                    el("span","",displayLegalTitle(link.source_label)+" → "+
                        displayLegalTitle(link.target_label)));
                card.append(copy);
                const controls=el("div","wb-actions");
                controls.append(adminLink("Источник","/articles/"+link.source_id),
                    adminLink("Цель","/articles/"+link.target_id));
                card.append(controls);
                if(link.conflicts.length){
                    const warnings=el("div","wb-operation-preview");
                    warnings.append(el("strong","","Восстановление невозможно"),
                        el("p","",link.conflicts.join(". ")));
                    card.append(warnings);
                }else{
                    card.append(wbInlineAction("Восстановить эту связь",async()=>{
                        if(!confirm("Восстановить только эту связь? Будут созданы новые редакции статей."))return;
                        await api("/relations/"+link.relation_id+"/restore",{
                            method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()},
                            body:{confirm:true,preview_hash:link.preview_hash}});
                        notify("Совместимая связь восстановлена.");
                        await draw();
                    },"primary"));
                }
                history.append(card);
            });
            result.append(activeSection,history);
        }
        if(id.value)await adminSafe(draw);
    }
    // Promise rejection from the asynchronous screen is handled with user feedback.
    function safeActivate(id){
        activate(id);
    }
    // Keep step navigation synchronous; each asynchronous view handles its own actions.
    activate(chosen);
}
