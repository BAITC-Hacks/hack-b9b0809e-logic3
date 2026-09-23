"""Reviewed public terms, separate from model instructions and catalog data."""
from app.catalog import Catalog
from app.ekt import number
import re
from typing import Literal

Topic = Literal['payment', 'delivery', 'minimum', 'other']


def requested_topics(question: str) -> list[Topic]:
    """Select only requested sections; a general conditions question gets all."""
    text = question.casefold()
    topics: list[Topic] = []
    if re.search(r'оплат|платить|заплат|платеж|платёж|наличн|безнал|картой|каспи|kaspi|рассроч|кредит|по сч[её]ту|перевод\w*\s+на|частями', text):
        topics.append('payment')
    if re.search(r'достав|самовывоз|курьер|привез|прив[её]з|забрать|отправк|отправите|высылаете|прислать', text):
        topics.append('delivery')
    if re.search(r'парти[яиюей]|кратност|минимальн\w*\s+(?:сумм\w*\s+)?(?:заказ|колич|объ[её]м)|от скольк|поштучно|одну штуку', text):
        topics.append('minimum')
    if re.search(r'возврат|вернуть|обмен|гаранти|отсроч|скидк|сч[её]т.фактур', text):
        topics.append('other')
    return topics


def is_purchase_question(question: str) -> bool:
    return bool(requested_topics(question)) or bool(re.search(r'услови\w*\s+(?:покуп|заказ)|как\s+(?:купить|заказать)', question.casefold()))

SOURCE = 'https://ekt.kz/about/information/'
CHECKED = '2026-09-23'


async def purchase_terms(catalog: Catalog, product_id: str | None = None, topics: list[Topic] | None = None, question: str = '') -> dict:
    selected = topics or ['payment', 'delivery', 'minimum']
    sections = []
    payment = (
        'Оплата. Физлица: карта онлайн, наличные при получении; при самовывозе — наличные или POS-терминал. '
        'Юрлица: перевод по счёту; при самовывозе возможна оплата наличными. Получателю нужны доверенность и удостоверение личности.'
    )
    delivery = (
        'Доставка. Для интернет-заказов по Алматы указан срок до 48 часов после согласования с менеджером. '
        'При сумме выше 15 000 ₸ указана бесплатная доставка; ниже — 1 000 ₸ внутри границ Саина / Аль-Фараби / ВОАД / Рыскулова, '
        '2 000 ₸ за ними в пределах города. Условия для ровно 15 000 ₸ не указаны. '
        'Для других городов стоимость и срок согласуются по адресу, весу и объёму. '
        'Также опубликованы пороги бесплатной доставки: свыше 400 000 ₸ в другие города, свыше 30 000 ₸ внутри городов присутствия. '
        'Эти правила пересекаются: применимый тариф уточните у менеджера. Доступен самовывоз.'
    )
    if 'payment' in selected:
        if re.search(r'рассроч|kaspi|каспи|кредит|частями|перевод\w*\s+на', question.casefold()):
            payment = 'Возможность оплаты указанным способом не подтверждена в опубликованных правилах. Уточните её у менеджера перед покупкой.'
        sections.append(payment)
    if 'delivery' in selected:
        if re.search(r'самовывоз|забрать', question.casefold()) and 'достав' not in question.casefold():
            delivery = 'Самовывоз доступен в городах присутствия Электрокомплекта. Адрес выдачи и готовность конкретного заказа согласуйте с менеджером.'
        if re.search(r'сегодня|завтра|ночью|выходн|точно|к\s*\d{1,2}(?::\d{2})?|за границ|росси|кыргыз|узбек', question.casefold()):
            delivery = 'Возможность доставки в указанный срок или по специальному маршруту не подтверждена. Адрес, дату и время нужно согласовать с менеджером; гарантировать их по имеющимся данным не могу.'
        sections.append(delivery)
    if 'minimum' in selected and product_id:
        product = await catalog.detail(product_id, fresh=True)
        raw = product.properties.get('KRATNOST_MIN')
        minimum = number(raw)
        sections.append(f'Минимальная партия для {product.article}: {minimum}; в прототипе это также шаг количества.'
                 if minimum is not None and minimum > 0 else
                 f'Для {product.article} минимальная партия не указана в API. Уточните у менеджера; значение 1 в интерфейсе — допущение прототипа.')
    elif 'minimum' in selected:
        sections.append('Минимальная партия зависит от товара. Укажите ID или артикул — проверю карточку. Единого подтверждённого минимума для всего каталога нет.')
    if 'other' in selected:
        sections.append('По этому дополнительному условию покупки в доступной базе нет подтверждённых правил. Уточните его у менеджера перед покупкой.')
    return {'message': '\n\n'.join(sections), 'sources': [{'title': 'Условия покупки ekt.kz', 'url': SOURCE, 'checked_at': CHECKED}]}
