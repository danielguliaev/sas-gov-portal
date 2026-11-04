/* Revision-bound scientific annotations; never migrate notes to a newer revision. */
"use strict";
function annotationRow(note) {
    const row=el("div","annotation-entry");
    row.append(el("blockquote","",note.quote),
        el("p","",note.note_text),
        el("small","",friendlyDate(note.created_at)));
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
function attachArticleAnnotations(card,article,textBlock) {
    if(!isLawyer() || !article.revision_id)return;
    const panel=el("details","annotation-panel");
    panel.append(el("summary","","Научные примечания к редакции"));
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
    const add=el("button","button subtle","Добавить примечание к выделению");
    add.type="button";
    add.onclick=async()=>{
        if(!lastRange) {
            notify("Сначала выделите фрагмент текста статьи.");
            return;
        }
        const from=document.createRange();
        from.selectNodeContents(textBlock);
        from.setEnd(lastRange.startContainer,lastRange.startOffset);
        const start=from.toString().length;
        const quote=lastRange.toString();
        // Use the exact visible substring to match the revision's textual anchor.
        const plain=textBlock.textContent||"";
        const check=plain.slice(start,start+quote.length);
        const selected=check===quote?quote:quote.trim();
        if(!selected||selected.length>1000){
            notify("Выберите фрагмент текста длиной до 1000 символов.");
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
    panel.addEventListener("toggle",()=>{
        if(panel.open)renderRevisionAnnotations(article.id,currentRevision,entries);
    });
    card.append(panel);
}
