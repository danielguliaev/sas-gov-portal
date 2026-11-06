"""The bill document is derived from its preamble and ordered amendment blocks."""
from html import escape
from datetime import datetime
from html.parser import HTMLParser


class TextBlocks(HTMLParser):
    """Contenteditable may place its first line outside a paragraph."""
    BLOCKS = {'p','h3','h4','ul','ol','li','blockquote'}

    def __init__(self):
        super().__init__()
        self.depth, self.inline, self.output = 0, [], []

    def flush(self):
        value = ''.join(self.inline)
        if value.strip():
            self.output.append('<p>' + value.replace('\n', '<br>') + '</p>')
        self.inline = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCKS:
            if not self.depth:
                self.flush()
            self.depth += 1
        (self.output if self.depth else self.inline).append(self.get_starttag_text())

    def handle_endtag(self, tag):
        (self.output if self.depth else self.inline).append('</' + tag + '>')
        if tag in self.BLOCKS:
            self.depth = max(0, self.depth-1)

    def handle_data(self, data):
        (self.output if self.depth else self.inline).append(escape(data))


def normalized_blocks(value):
    parser = TextBlocks()
    parser.feed(value)
    parser.flush()
    return ''.join(parser.output)

RESOLUTIONS = {
    'resolution_simple': ('SENATE RESOLUTIONS', 'Сенат Штата постановил, что'),
    'resolution_joint': ('JOINT RESOLUTIONS', 'Мы, Легислатура Сан Андреаса, призываем..'),
    'resolution_concurrent': ('CONCURRENT RESOLUTIONS', 'Сенат Штата и Ассамблея Штата Сан Андреас совместно постановили, что'),
}
VOTE_LABELS = {'simple_majority': 'Большинство', 'predominantly_for': 'Преимущественно за',
               'absolute_majority': 'Абсолютное большинство', 'two_thirds': '2/3'}


def header_date(value):
    if not value:
        return 'ДАТА ПОДАЧИ НЕ НАЗНАЧЕНА'
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    month = ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'][d.month-1]
    return f'{month.upper()} {d.day:02d}, {d.year}'


def document_header(bill):
    kind = RESOLUTIONS.get(bill.get('document_type'), ('SENATE BILL', ''))[0]
    number = (bill.get('number') or 'NNN').split('-')[-1]
    authors = escape(', '.join(bill.get('author_names', [])) or 'Автор не выбран')
    coauthors = ', '.join(bill.get('coauthor_names', []))
    yesno = lambda key: 'ДА' if bill.get(key, False) else 'НЕТ'
    return (f'<div class="bill-document-header"><p class="document-date">ПРЕДСТАВЛЕНО {header_date(bill.get("introduced_at"))}</p>'
            '<p class="document-legislature">ЛЕГИСЛАТУРА САН АНДРЕАСА</p>'
            f'<div class="document-number"><strong>{kind}</strong><strong>NO. {escape(number)}</strong></div><hr>'
            f'<p class="document-authors"><strong>Представлено {authors}</strong>'
            + (f'<br>(Соавторы: {escape(coauthors)})' if coauthors else '')
            + f'</p><p class="document-date">Последняя редакция: {header_date(bill.get("revised_at"))}</p><hr>'
            f'<p class="document-attributes">Голосование: {VOTE_LABELS.get(bill.get("vote_rule"), "Большинство")} '
            f'Ассигнование: {yesno("appropriation")} · Фискальный комитет: {yesno("fiscal_committee")} · '
            f'Локальный проект: {yesno("local_project")}</p><hr></div>'
            f'<h2 class="document-body-title">{"Текст резолюции" if bill.get("document_type") in RESOLUTIONS else "Текст Законопроекта"}</h2>')

ENACTING = 'НАРОД ШТАТА САН АНДРЕАС ПОСТАНОВЛЯЕТ СЛЕДУЮЩЕЕ:'
URGENCY = ('Настоящий акт является неотложным законом, необходимым для немедленного '
           'сохранения общественного порядка, здоровья или безопасности по смыслу '
           'Статьи IV Конституции Сан Андреаса, и <strong>вступает в силу немедленно</strong>.')
SANCTIONS = {'prison_min_months': 'Лишение свободы, минимум месяцев',
             'prison_max_months': 'Лишение свободы, максимум месяцев',
             'fine_min_usd': 'Штраф, минимум USD', 'fine_max_usd': 'Штраф, максимум USD'}


def effective_label(bill):
    if bill.get('document_type') in RESOLUTIONS:
        return 'Не применяется к резолюции'
    rule = bill.get('effective_rule', 'specified_date' if bill.get('effective_at') else 'standard')
    if rule == 'immediate':
        return 'Немедленно после подписания губернатором'
    if rule == 'specified_date':
        value = (bill.get('effective_at') or '')[:10]
        return '.'.join(reversed(value.split('-'))) if value else 'Дата не выбрана'
    return 'Спустя 14 дней после подписания губернатором'


def document_rich(bill):
    parts = [document_header(bill), '<div class="document-preamble">', bill.get('text_rich') or '', '</div>']
    if bill.get('document_type') in RESOLUTIONS:
        parts.extend(['<div class="resolution-body">', bill.get('resolution_text_rich') or '<p>' + RESOLUTIONS[bill['document_type']][1] + '</p>', '</div>'])
        return ''.join(parts)
    parts.append('<p class="enacting-clause"><strong>' + ENACTING + '</strong></p>')
    for i, change in enumerate(bill.get('changes', []), 1):
        article, target = change['article'], change['target']
        code = escape(target.get('code_name') or target['code_id'])
        number = escape(article.get('number') or target.get('number') or '')
        action = {'add': f'§ {number} добавлен в {code}:',
                  'amend': f'§ {escape(target.get("number") or number)} {code} изменён:',
                  'repeal': f'§ {escape(target.get("number") or number)} {code} отменён.'}[change['operation']]
        parts.append(f'<h3>Статья {i}. {action}</h3>')
        content = change.get('source') or article if change['operation'] == 'repeal' else article
        parts.append('<p><strong>' + escape(content.get('title', '')) + '</strong></p>')
        parts.append(content.get('text_rich', ''))
        if change['operation'] != 'repeal':
            details = article.get('display', {})
            if details.get('categories'):
                parts.append('<p>Категории: ' + escape(', '.join(details['categories'])) + '.</p>')
            if article.get('criminal_class'):
                parts.append('<p>Уголовно-правовой класс: ' + escape(article['criminal_class']) + '.</p>')
            for key, value in article.get('sanctions', {}).items():
                if value is not None:
                    parts.append(f'<p>{SANCTIONS[key]}: {value}.</p>')
            for name, value in details.get('custom_values', {}).items():
                parts.append('<p>' + escape(name) + ': ' + escape(str(value)) + '.</p>')
            for relation in article.get('relations', []):
                parts.append('<p>' + escape(relation.get('label', 'Юридическая связь')) + '.</p>')
    rule = bill.get('effective_rule', 'specified_date' if bill.get('effective_at') else 'standard')
    clause = URGENCY if rule == 'immediate' else (
        'Положения настоящего акта вступают в силу ' + effective_label(bill)
        if rule == 'specified_date' else 'Настоящий акт вступает в силу спустя 14 дней после подписания губернатором.')
    parts.append('<p>' + clause + '</p>')
    return ''.join(parts)
