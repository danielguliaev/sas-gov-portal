"""Portable, paginated Cyrillic PDF exports for Senate records."""
from html import escape, unescape
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
import re
from threading import Lock
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi.responses import Response
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle
from app.core import fail, loads

_FONT_LOCK = Lock()
FONT_PATH = Path(__file__).resolve().parents[1] / "web/senate-assets/NotoSans-Regular.ttf"

LABELS = {
    'draft': 'Черновик', 'committee_review': 'На рассмотрении в комитете',
    'held': 'Оставлен в комитете', 'revision_requested': 'На доработке',
    'committee_rejected': 'Окончательно отклонён комитетом',
    'ready_plenary': 'Готов к пленарному рассмотрению', 'agenda_published': 'В опубликованной повестке',
    'adopted': 'Принят', 'rejected': 'Не принят', 'published': 'Голосование открыто',
    'closed': 'Завершено', 'failed': 'Не состоялось', 'pending': 'Итог ещё не определён',
    'needs_review': 'Ожидает решения по исправлению', 'review_required': 'Требуется проверка',
    'no_quorum': 'Кворум не достигнут', 'no_winner': 'Победитель не определён',
    'predominantly_for': 'Преимущественно за', 'simple_majority': 'Простое большинство',
    'absolute_majority': 'Абсолютное большинство', 'two_thirds': '2/3',
    'yes': 'За', 'no': 'Против', 'abstain': 'Воздержался', 'absent': 'Отсутствовал',
    'do_pass': 'Рекомендовать к принятию', 'amend_do_pass': 'Внести поправку и рекомендовать к принятию',
    'not_pass': 'Окончательно отклонить', 'transfer': 'Передать в другой комитет',
    'amend_transfer': 'Внести поправку и передать в другой комитет', 'return_to_author': 'Вернуть на доработку',
    'draft_created': 'Создание черновика', 'revision_created': 'Сохранение новой редакции',
    'submitted': 'Подача проекта', 'coauthor_accepted': 'Подтверждение соавторства',
    'coauthor_declined': 'Отклонение приглашения в соавторы', 'committee_deadline': 'Истечение срока комитета',
    'committee_agenda': 'Включение в повестку комитета', 'consultant_report': 'Отчёт консультанта',
    'committee_decision': 'Решение председателя', 'administrative_correction': 'Административное исправление',
    'senate.agenda.published': 'Публикация пленарной повестки', 'senate.vote.closed': 'Итог голосования',
    'senate.vote.correction_pending': 'Исправление голоса и проверка итогов',
    'senate.vote.admin_resolved': 'Утверждение исправленных итогов',
    'deciding_vote': 'Решающий голос',
}


def display(value):
    return LABELS.get(value, str(value)) if value is not None else 'Не указано'


def export_timezone(name):
    if name == 'UTC':
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError):
        fail(422, 'INVALID_TIME_ZONE', 'Неизвестный часовой пояс экспорта.')


def display_time(value, zone):
    if not value:
        return 'Не указано'
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if len(value) == 10:
        return parsed.strftime('%d.%m.%Y')
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(zone).strftime('%d.%m.%Y %H:%M %Z')


def describe_event(event):
    raw = event['body']
    try:
        details = loads(raw)
    except (ValueError, TypeError):
        return display(event['event_type']) + ': ' + raw
    if not isinstance(details, dict):
        return display(event['event_type']) + ': ' + str(details)
    names = {'version': 'Редакция', 'before_version': 'Прежняя редакция', 'reason': 'Основание',
             'status': 'Новый статус', 'before_status': 'Прежний статус', 'decision': 'Решение',
             'recommendation': 'Рекомендация', 'result': 'Результат', 'committee_id': 'Комитет',
             'session_id': 'Заседание', 'review_id': 'Рассмотрение', 'agenda_id': 'Повестка',
             'quorum': 'Требуемый кворум', 'present': 'Присутствовали', 'tally': 'Подсчёт',
             'corrected_voter_id': 'Участник с исправленным голосом'}
    parts = []
    for key, value in details.items():
        if key == 'tally' and isinstance(value, dict):
            value = ', '.join(f'{display(k)}: {display(v) if k == "deciding_vote" else v}'
                              for k, v in value.items() if v is not None)
        elif isinstance(value, str):
            value = display(value)
        parts.append(f'{names.get(key, key)}: {value}')
    return display(event['event_type']) + '. ' + '; '.join(parts)


def text_content(value):
    text = re.sub(r"</(?:p|h[1-6]|li|blockquote)>|<br\s*/?>", "\n", str(value or ""), flags=re.I)
    return unescape(re.sub(r"<[^>]*>", "", text)).strip()


def pdf_response(title: str, sections: list[tuple[str, str]], filename: str, history_rows=None):
    with _FONT_LOCK:
        if "SenateText" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("SenateText", str(FONT_PATH)))
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=46, leftMargin=46,
                            topMargin=52, bottomMargin=50, title=title,
                            author="Сенат штата Сан-Андреас")
    body = ParagraphStyle("SenateBody", fontName="SenateText", fontSize=10,
                          leading=16, spaceAfter=8, alignment=TA_LEFT,
                          wordWrap="LTR", splitLongWords=True)
    heading = ParagraphStyle("SenateHeading", parent=body, fontSize=13, leading=19,
                             textColor=colors.HexColor("#a30c1f"), spaceBefore=16,
                             spaceAfter=8, keepWithNext=True)
    title_style = ParagraphStyle("SenateTitle", parent=heading, fontSize=19, leading=26)
    story = [Paragraph(escape(title), title_style), Spacer(1, 8)]
    for label, content in sections:
        if label:
            story.append(Paragraph(escape(str(label)), heading))
        paragraphs = text_content(content).splitlines() or ["Нет данных"]
        for line in paragraphs:
            if line.strip():
                story.append(Paragraph(escape(line), body))
    if history_rows:
        story.append(Paragraph('Журнал рассмотрения', heading))
        cell = ParagraphStyle('SenateCell', parent=body, fontSize=8, leading=12, spaceAfter=0)
        rows = [[Paragraph(escape(str(value)), cell) for value in row]
                for row in [['Дата и время', 'Исполнитель', 'Действие', 'Сведения']] + history_rows]
        table = LongTable(rows, colWidths=[80, 90, 105, A4[0] - 367], repeatRows=1, splitInRow=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f0eded')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('GRID', (0, 0), (-1, -1), .4, colors.HexColor('#cccccc')),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(table)

    def footer(canvas, document):
        canvas.setFont("SenateText", 8)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawString(46, 27, "Сенат штата Сан-Андреас • Архив документов")
        canvas.drawRightString(A4[0] - 46, 27, str(document.page))
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "-", filename)
    return Response(buffer.getvalue(), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{safe_name}"'})
