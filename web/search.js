/* Library search: only lawyer/superadmin may use extended filters or historical records. */
"use strict";
async function advancedSearchPage() {
    const incoming=new URLSearchParams(location.search);
    pageHeading("Правовая библиотека","Поиск по законодательству",
        "Поиск действующих статей, юридических классификаций и исторических редакций.");
    const form=el("form","search-page-form");
    form.setAttribute("role","search");
    const query=el("input");query.type="search";query.id="searchQuery";query.name="query";
    query.maxLength=200;query.value=incoming.get("query")||incoming.get("q")||"";
    query.placeholder="Юридический номер, заголовок или фрагмент текста";
    const submit=el("button","button primary","Найти");submit.type="submit";
    form.append(query,submit);
    main.append(form);
    const filterArea=el("div","search-page-filters");
    const filters={};
    const choices=(name,label,items,empty)=>{
        const field=adminSelect(items,incoming.get(name)||"",empty===undefined?"Все":empty);
        field.name=name;
        filters[name]=field;
        filterArea.append(adminField(label,field));
        return field;
    };
    let categories=[];
    let historic=null;
    if(isLawyer()) {
        categories=await api("/categories");
        choices("jurisdiction","Юрисдикция",[
            ["Штат","state"],["Округ","county"],["Лос-Сантос","city"]]);
        choices("code_id","Кодекс",allCodes.map(c=>[c.name,c.id]));
        choices("number","Номер",[]);
        const number=filters.number;
        const numberInput=el("input");numberInput.type="text";numberInput.maxLength=100;
        numberInput.placeholder="Точный номер статьи";
        numberInput.value=incoming.get("number")||"";
        filterArea.lastChild.replaceChild(numberInput,number);
        filters.number=numberInput;
        choices("category","Категория",categories.map(c=>[c.name,c.id]));
        choices("criminal_class","Класс обвинения",[
            ["Тяжкое преступление","felony"],["Проступок","misdemeanor"],["Нарушение","infraction"]]);
        choices("has_sanctions","Наличие санкций",[
            ["Есть санкции","true"],["Без санкций","false"]]);
        [
            ["min_months","Лишение свободы: минимум, мес."],
            ["max_months","Лишение свободы: максимум, мес."],
            ["min_usd","Штраф: минимум, USD"],
            ["max_usd","Штраф: максимум, USD"]
        ].forEach(([name,label])=>{
            const input=el("input");input.type="number";input.min="0";input.step="1";
            input.placeholder="Не указано";
            input.value=incoming.get(name)||"";
            input.name=name;filters[name]=input;
            filterArea.append(adminField(label,input));
        });
        const field=choices("field_id","Пользовательское поле",[],"Не выбрано");
        const valueBox=el("div","search-custom-value");
        const fieldValue=el("input");fieldValue.type="text";fieldValue.name="field_value";
        fieldValue.value=incoming.get("field_value")||"";fieldValue.placeholder="Значение поля";
        valueBox.append(adminField("Значение",fieldValue));filterArea.append(valueBox);
        filters.field_value=fieldValue;
        function refreshFields() {
            const selected=categories.filter(c=>!filters.category.value||
                c.id===filters.category.value);
            const fields=selected.flatMap(c=>(c.fields||[]).map(f=>({
                ...f,label:c.name+" · "+f.name
            })));
            const was=field.value||incoming.get("field_id")||"";
            field.replaceChildren(new Option("Не выбрано",""));
            fields.filter(f=>f.field_type!=="range" && f.field_type!=="rich_text")
                .forEach(f=>field.append(new Option(f.label,f.id)));
            if([...field.options].some(o=>o.value===was))field.value=was;
            refreshValue();
        }
        function refreshValue() {
            const chosen=categories.flatMap(c=>c.fields||[]).find(f=>f.id===field.value);
            const saved=filters.field_value.value;
            let input;
            if(chosen && chosen.field_type==="choice") {
                input=adminSelect((chosen.options||[]).filter(o=>o.active)
                    .map(o=>[o.name,o.id]),saved,"Не выбрано");
            }else if(chosen && chosen.field_type==="boolean"){
                input=adminSelect([["Да","true"],["Нет","false"]],saved,"Не выбрано");
            }else{
                input=el("input");input.type=chosen&&chosen.field_type==="number"?"number":"text";
                input.value=saved;input.placeholder="Значение поля";
            }
            input.name="field_value";
            filters.field_value=input;
            valueBox.replaceChildren(adminField("Значение",input));
        }
        filters.category.onchange=refreshFields;
        field.onchange=refreshValue;
        refreshFields();
        historic=choices("mode","Режим поиска",[
            ["Архивные статьи","archive"],["Исторические редакции","revisions"]],
            "Действующие статьи");
        const reset=adminButton("Сбросить фильтры",()=>{
            query.value="";
            Object.keys(filters).forEach(k=>filters[k].value="");
            filters.category.value="";refreshFields();
            history.pushState({scrollY:0},"","/search");
            draw(false);
        });
        filterArea.append(reset);
        main.append(filterArea);
    }
    const summary=el("div","search-results-head");
    const heading=el("h2","","Результаты");
    const count=el("span","small muted","");
    summary.append(heading,count);main.append(summary);
    const resultBox=el("div","search-layout");main.append(resultBox);
    let serial=0;
    async function draw(updateUrl){
        const searchId=++serial;
        const params=new URLSearchParams();
        const value=query.value.trim();
        if(value)params.set("query",value);
        if(isLawyer()){
            Object.entries(filters).forEach(([k,input])=>{
                if(input.value!=="" && !(k==="field_value"&&!filters.field_id.value)){
                    params.set(k,input.value);
                }
            });
            if(filters.mode && filters.mode.value==="revisions"&&!value){
                resultBox.replaceChildren(el("p","notice","Для поиска по архиву редакций необходимо ключевое слово."));
                return;
            }
            if(filters.field_id.value && !filters.field_value.value){
                resultBox.replaceChildren(el("p","notice","Выберите значение пользовательского поля."));
                return;
            }
        }
        const hasQuery=Array.from(params.keys()).some(k=>k!=="mode");
        if(updateUrl){
            history.pushState({scrollY:0},"","/search"+(params.size?"?"+params:""));
        }
        resultBox.replaceChildren();
        if(!hasQuery){
            count.textContent="";
            resultBox.append(el("p","search-begin",
                "Введите запрос или выберите расширенные фильтры для поиска по библиотеке."));
            return;
        }
        count.textContent="Выполняется поиск…";
        try {
            const mode=filters.mode&&filters.mode.value;
            const request=new URLSearchParams(params);
            request.delete("mode");
            let found;
            if(mode==="revisions"){
                const historical=new URLSearchParams();
                historical.set("query",value);
                if(filters.code_id.value)historical.set("code_id",filters.code_id.value);
                found=await api("/search/revisions?"+historical);
            }else{
                if(mode==="archive")request.set("archive","true");
                found=await api("/search?"+request);
            }
            if(searchId!==serial||!main.contains(resultBox))return;
            const result=found.items;
            count.textContent="Найдено: "+result.length+(result.length===found.limit?
                " · показаны первые "+found.limit:"");
            if(!result.length)resultBox.append(el("p","empty","Совпадений нет."));
            result.forEach(article=>{
                const link=el("a","search-result");
                link.dataset.link="";
                if(mode==="revisions"){
                    link.href="/revisions/"+article.article_id+"?revision="+encodeURIComponent(article.id);
                    link.append(el("span","match-code",article.code_name+
                        " · Статья "+article.number+" · Редакция "+article.sequence),
                        el("strong","",displayLegalTitle(article.title)),
                        el("span","",friendlyDate(article.published_at)+" · Открыть редакцию"));
                }else{
                    link.href="/articles/"+article.id;
                    const code=allCodes.find(c=>c.id===article.code_id);
                    link.append(el("span","match-code",(code?code.name:"Кодекс")+
                        " · Статья "+article.number),
                        el("strong","",displayLegalTitle(article.title)),
                        el("span","",article.status==="archived"?
                            "Архивная статья · открыть историю":"Открыть полный текст"));
                }
                resultBox.append(link);
            });
        }catch(error){
            if(searchId!==serial||!main.contains(resultBox))return;
            count.textContent="Ошибка поиска";
            resultBox.replaceChildren(el("p","notice",error.message));
        }
    }
    form.onsubmit=event=>{event.preventDefault();draw(true);};
    await draw(false);
}

async function archivedArticlePage(article){
    if(!isLawyer())throw Error("Архивные статьи доступны юристам и суперадминистратору.");
    const code=allCodes.find(c=>c.id===article.code_id);
    main.append(breadcrumb([{label:"Каталог кодексов",href:"/laws"},
        {label:code?code.name:"Кодекс",href:"/codes/"+article.code_id},
        {label:"Архив · статья "+article.number}]));
    pageHeading("Архив правовых текстов","Статья "+article.number+" · "+displayLegalTitle(article.title),
        "Архивная статья не входит в действующий консолидированный текст кодекса.");
    const panel=el("section","article-card archived-article");
    panel.append(el("p","archive-status","Архивный статус: исключена из действующего законодательства"));
    const body=el("div","article-text");
    body.innerHTML=article.text_rich||"<p>Текст не указан.</p>";
    panel.append(body);
    const actions=el("div","article-actions");
    const revisions=adminLink("История редакций","/revisions/"+article.id);
    actions.append(revisions);
    if(isAdmin())actions.append(adminLink("Восстановление через архив","/admin/archive"));
    panel.append(actions);
    main.append(panel);
}
