/* Revision-bound scientific annotations; never migrate notes to a newer revision. */
"use strict";
function annotationRow(note) {
    const row=el("div","annotation-entry");
    row.append(el("blockquote","",note.quote),
        el("p","",note.note_text),
        el("small","annotation-meta","Автор: "+(note.author_name||note.author_id||"Неизвестный пользователь")+" · "+new Intl.DateTimeFormat("en-US",{month:"2-digit",day:"2-digit",year:"numeric",timeZone:"UTC"}).format(new Date(note.created_at)).replaceAll("/",".")));
    return row;
}
async function renderRevisionAnnotations(articleId,revisionId,host){
    if(!isLawyer())return;
    host.replaceChildren();
    try{
        const notes=await api("/articles/"+articleId+"/annotations?revision_id="+encodeURIComponent(revisionId));
        if(!notes.length)host.append(el("p","muted","Научных примечаний к этой редакции нет."));
        notes.forEach(n=>host.append(annotationRow(n)));
    }catch(error){
        host.append(el("p","notice",error.message));
    }
}
function attachArticleAnnotations(card,article,textBlock,detailsHost) {
    if(!isLawyer() || !article.revision_id)return;
    const panel=el("section","annotation-panel");
    panel.append(el("h3","","Научные примечания к редакции"));
    const entries=el("div","annotation-list");
    const currentRevision=article.revision_id;
    let lastRange=null;
    function rememberSelection(){
        const selection=window.getSelection();
        if(!selection||selection.isCollapsed||!selection.rangeCount)return;
        const range=selection.getRangeAt(0);
        if(textBlock.contains(range.commonAncestorContainer))lastRange=range.cloneRange();
    }
    textBlock.addEventListener("mouseup",rememberSelection);
    textBlock.addEventListener("keyup",rememberSelection);
    const actions=el("div","article-actions");
    const add=el("button","button primary annotation-add","＋ Добавить примечание");
    add.type="button";
    add.onclick=async()=>{
        if(!lastRange) {
            notify("Сначала выделите фрагмент текста статьи.");
            return;
        }
        // Range.toString() may introduce visual line breaks between HTML blocks.
        // The server anchors against text nodes concatenated as browser textContent.
        const from=document.createRange();
        from.selectNodeContents(textBlock);
        from.setEnd(lastRange.startContainer,lastRange.startOffset);
        const start=from.cloneContents().textContent.length;
        const selected=lastRange.cloneContents().textContent||"";
        const plain=textBlock.textContent||"";
        if(!selected || selected.length>1000 || plain.slice(start,start+selected.length)!==selected){
            notify("Выделенный фрагмент нельзя надёжно сопоставить с текстом редакции.");
            return;
        }
        const note=prompt("Научное примечание к выделенному фрагменту:");
        if(note===null)return;
        if(!note.trim())return notify("Введите текст примечания.");
        add.disabled=true;
        try{
            await api("/articles/"+article.id+"/annotations",{method:"POST",body:{
                revision_id:currentRevision,start_offset:start,end_offset:start+selected.length,
                quote:selected,note_text:note.trim()}});
            notify("Примечание привязано к текущей редакции.");
            await renderRevisionAnnotations(article.id,currentRevision,entries);
            lastRange=null;
        }catch(error) {notify(error.message);}
        finally {add.disabled=false;}
    };
    actions.append(add);
    panel.append(actions,entries);
    renderRevisionAnnotations(article.id,currentRevision,entries);
    (detailsHost||card).append(panel);
}
