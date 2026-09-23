"""Local inverted index: exact articles first, AND matching for technical queries."""
import re
from bisect import bisect_left
from collections import defaultdict

from app.models import Product

STOP = set('есть ли наличие наличии найди найти покажи товар товары артикул мне нужен нужна нужны нужно пожалуйста у вас на в и для по с сколько стоит цена купить характеристики сертификат сертификаты аналог аналоги'.split())
STOP.update('ищу хочу подбери подобрать подберите проверь проверить товара товару можно пожалуйста стоимость узнать про'.split())
ALIASES = {
    'ав': 'автомат', 'автоматы': 'автомат', 'автоматический': 'автомат',
    'автоматические': 'автомат', 'автомата': 'автомат',
    'кабеля': 'кабель', 'кабели': 'кабель',
    'розетки': 'розетка', 'розетку': 'розетка',
    'лампы': 'лампа', 'лампу': 'лампа',
    'светодиодная': 'led', 'светодиодный': 'led', 'светодиодные': 'led',
    'иэк': 'iek', 'іек': 'iek', 'екф': 'ekf', 'екфproxima': 'ekf',
    'легранд': 'legrand', 'шнайдер': 'schneider',
}
CATEGORY_TERMS = {
    'modulnye_avtomaticheskie_vyklyuchateli': 'автомат выключатель',
    'silovye_avtomaticheskie_vyklyuchateli': 'автомат выключатель',
    'kabel_silovoy': 'кабель',
    'kabel_kontrolnyy': 'кабель',
}


def article_key(value: str) -> str:
    return value.casefold().strip().rstrip('_')


def tokens(value: str) -> set[str]:
    value = value.casefold().replace('ё', 'е').replace(',', '.')
    value = re.sub(r'(\d+(?:\.\d+)?)\s*ампер(?:а|ов)?\b', r'\1a', value)
    value = re.sub(r'(\d)\s+(?=ма\b|а\b|кв\b|в\b|вт\b|мм\b|a\b|v\b|w\b)', r'\1', value)
    value = re.sub(r'(?<=\d)\s*[хx×*]\s*(?=\d)', 'x', value)
    words = re.findall(r'[a-zа-яіїәғқңөұүһ0-9]+(?:[.][0-9]+)?', value)
    result = set()
    for word in words:
        if word in STOP:
            continue
        if any(c.isdigit() for c in word):
            word = word.translate(str.maketrans({'а': 'a', 'в': 'v', 'с': 'c', 'м': 'm'}))
        result.add(ALIASES.get(word, word))
    return result


class SearchIndex:
    def __init__(self, products: dict[str, Product]):
        self.exact: dict[str, set[str]] = defaultdict(set)
        self.postings: dict[str, set[str]] = defaultdict(set)
        self.names: dict[str, set[str]] = {}
        for p in products.values():
            for key in (p.id, p.article, str(p.properties.get('ARTIKULPOSTAVSHCHIKA', ''))):
                if key:
                    self.exact[article_key(key)].add(p.id)
            self.names[p.id] = tokens(p.name)
            terms = tokens(' '.join((p.name, p.article, p.id, *map(str, p.properties.values()))))
            # Real names such as "ВА47-29" and "ВВГнг" omit the product type.
            # Use the partner's category, not a guessed meaning of the model code.
            for marker, labels in CATEGORY_TERMS.items():
                if marker in p.category:
                    terms.update(tokens(labels))
            for term in terms:
                self.postings[term].add(p.id)
        self.vocabulary = sorted(self.postings)

    def search(self, query: str, products: dict[str, Product], limit: int = 6) -> list[Product]:
        # Underscore suffixes in EKT articles are optional for a customer's query.
        identifiers = [query.strip(), *re.findall(r'[\w.-]+', query)]
        exact = set().union(*(self.exact.get(article_key(q), set()) for q in identifiers))
        if exact:
            return [products[key] for key in sorted(exact)[:limit]]
        terms = tokens(query)
        if not terms:
            return []
        matches: set[str] | None = None
        for term in terms:
            found = set(self.postings.get(term, ()))
            # Numeric parameters match whole tokens: 16 A must never match 160 A.
            if len(term) >= 3 and not any(c.isdigit() for c in term):
                start = bisect_left(self.vocabulary, term)
                for position in range(start, len(self.vocabulary)):
                    word = self.vocabulary[position]
                    if not word.startswith(term):
                        break
                    found.update(self.postings[word])
            matches = found if matches is None else matches & found
            if not matches:
                return []
        ranked = sorted(matches or (), key=lambda key: (-len(self.names[key] & terms), products[key].name, key))
        return [products[key] for key in ranked[:limit]]
