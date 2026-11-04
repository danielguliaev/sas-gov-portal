/* Standalone normative acts in the library, separate from code articles. */
"use strict";
async function libraryActsPage() {
    main.append(breadcrumb([{label:"Каталог",href:"/laws"},{label:"Нормативные акты"}]));
    pageHeading("Правовая библиотека","Отдельные нормативные акты",
        "Самостоятельные документы штата, округа и города, не входящие в иерархию кодекса.");
    const filter=adminSelect([
        ["Штат Сан-Андреас","state"],["Округ","county"],["Город Лос-Сантос","city"]
    ],new URLSearchParams(location.search).get("jurisdiction")||"","Все юрисдикции");
    main.append(adminField("Юрисдикция",filter));
    const list=el("div","catalog-grid");main.append(list);
    async function draw(){
        const query=filter.value?"?jurisdiction="+encodeURIComponent(filter.value):"";
        const rows=await api("/library/acts"+query);
        list.replaceChildren();
        if(!rows.length)list.append(el("p","empty",
            "Опубликованных отдельных нормативных актов пока нет."));
        rows.forEach(doc=>{
            const a=el("a","law-code-card");
            a.href="/acts/"+doc.id;a.dataset.link="";
            a.append(el("div","eyebrow",doc.document_type),
                el("h3","",doc.title),
                el("div","code-card-meta",doc.issuer+" · "+
                    friendlyDate(doc.published_at)),
                el("span","card-forward","Открыть документ →"));
            list.append(a);
        });
    }
    filter.onchange=()=>{
        history.replaceState(history.state,"","/acts"+
            (filter.value?"?jurisdiction="+encodeURIComponent(filter.value):""));
        adminSafe(draw);
    };
    await draw();
    if(isAdmin()){
        const section=adminSection("Публикация самостоятельного нормативного акта",
            "Публикация суперадминистратора фиксируется в закрытом журнале. Источником не становится промежуточный законопроект.");
        const form=adminForm(async()=>{
            const body={jurisdiction_id:jur.value,title:title.value.trim(),
                document_type:kind.value.trim(),issuer:issuer.value.trim(),
                text_rich:text.value,confirm:true};
            if(!body.title||!body.text_rich.trim())throw Error("Введите заголовок и текст.");
            if(!confirm("Подтвердить прямую публикацию нормативного акта?"))return;
            const created=await api("/library/acts",{method:"POST",
                headers:{"Idempotency-Key":crypto.randomUUID()},body});
            go("/acts/"+created.id);
        });
        const jur=adminSelect([["Штат","state"],["Округ","county"],["Лос-Сантос","city"]],"state");
        const title=adminInput("","Название правового акта");
        const kind=adminInput("Нормативный акт");
        const issuer=adminInput("Штат Сан-Андреас");
        const text=el("textarea");text.rows=9;text.placeholder="Форматированный текст (HTML)";
        form.save.textContent="Опубликовать акт";
        form.form.prepend(adminField("Юрисдикция",jur),
            adminField("Заголовок",title),adminField("Тип документа",kind),
            adminField("Орган или источник",issuer),adminField("Текст документа",text));
        section.append(form.form);main.append(section);
    }
}
async function libraryActPage(id) {
    const doc=await api("/library/acts/"+id);
    main.append(breadcrumb([{label:"Нормативные акты",href:"/acts"},{label:doc.title}]));
    pageHeading(doc.document_type,doc.title,doc.issuer+" · "+friendlyDate(doc.published_at));
    const article=el("article","article-card normative-act");
    if(doc.status!=="published")
        article.append(el("p","archive-status","Архивный документ, не входит в действующую выдачу."));
    const body=el("div","article-text");body.innerHTML=doc.text_rich;
    article.append(body);
    const meta=el("p","small muted","Дата издания: "+friendlyDate(doc.issued_at)+
        " · ID: "+doc.id);
    article.append(meta);
    const toolbar=el("div","article-actions");
    const copy=adminButton("Копировать ссылку",()=>navigator.clipboard.writeText(
        location.origin+"/acts/"+id).then(()=>notify("Ссылка скопирована."))
        .catch(()=>notify("Не удалось скопировать ссылку.")));
    toolbar.append(copy);
    if(isAdmin()){
        if(doc.status==="published"){
            toolbar.append(adminButton("Архивировать акт",()=>adminSafe(async()=>{
                const p=await api("/library/acts/"+id+"/archive/preview",{method:"POST"});
                if(!confirm("Архивировать «"+p.title+"»?"))return;
                await api("/library/acts/"+id+"/archive",{method:"POST",headers:{
                    "Idempotency-Key":crypto.randomUUID()},
                    body:{confirm:true,preview_hash:p.preview_hash}});
                notify("Акт архивирован.");go("/acts/"+id);
            }),"danger"));
        }else{
            toolbar.append(adminButton("Восстановить акт",()=>adminSafe(async()=>{
                if(!confirm("Вернуть документ в действующий каталог?"))return;
                await api("/library/acts/"+id+"/restore",{method:"POST",headers:{
                    "Idempotency-Key":crypto.randomUUID()},body:{confirm:true}});
                notify("Акт восстановлен.");go("/acts/"+id);
            }),"primary"));
        }
    }
    article.append(toolbar);main.append(article);
}
