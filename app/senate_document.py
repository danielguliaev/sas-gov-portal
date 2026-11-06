"""The bill document is derived from its preamble and ordered amendment blocks."""
from html import escape

ENACTING = 'НАРОД ШТАТА САН АНДРЕАС ПОСТАНОВЛЯЕТ СЛЕДУЮЩЕЕ:'
URGENCY = ('Настоящий акт является неотложным законом, необходимым для немедленного '
           'сохранения общественного порядка, здоровья или безопасности по смыслу '
           'Статьи IV Конституции Сан Андреаса, и <strong>вступает в силу немедленно</strong>.')
SANCTIONS = {'prison_min_months': 'Лишение свободы, минимум месяцев',
             'prison_max_months': 'Лишение свободы, максимум месяцев',
             'fine_min_usd': 'Штраф, минимум USD', 'fine_max_usd': 'Штраф, максимум USD'}


def effective_label(bill):
    rule = bill.get('effective_rule', 'specified_date' if bill.get('effective_at') else 'standard')
    if rule == 'immediate':
        return 'Немедленно после подписания губернатором'
    if rule == 'specified_date':
        value = (bill.get('effective_at') or '')[:10]
        return '.'.join(reversed(value.split('-'))) if value else 'Дата не выбрана'
    return 'Спустя 14 дней после подписания губернатором'


def document_rich(bill):
    parts = [bill.get('text_rich') or '', '<p><strong>' + ENACTING + '</strong></p>']
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
