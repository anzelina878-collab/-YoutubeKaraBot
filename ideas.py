"""Editorial concepts, not claims about the content of unseen videos."""
TOPICS = [
    ('Мегастройки: как устроены невероятные сооружения',
     ['megaprojects engineering', 'megaproyectos ingeniería', 'megaconstruções engenharia', 'mégaprojets ingénierie', 'Megaprojekte Ingenieurtechnik', 'мегастройки инженерия', '巨大建造物 技術', 'मेगा प्रोजेक्ट इंजीनियरिंग'],
     ['Почему здесь построили тоннель вместо моста?', 'Город посреди пустыни: как туда доставляют воду?', 'Как строят мосты там, где слишком глубокая вода?'],
     'Разбор одного сооружения: задача → необычное решение → цена и ограничения. Карты, схемы и собственная озвучка.', 'Средняя: нужны проверенные источники и схемы.'),
    ('Повседневная жизнь людей в прошлом',
     ['ancient daily life history', 'vida cotidiana antigua historia', 'vida cotidiana antiguidade', 'vie quotidienne histoire antique', 'Alltag Antike Geschichte', 'быт древности история', '古代 日常生活 歴史', 'प्राचीन दैनिक जीवन इतिहास'],
     ['Как обычная семья переживала зиму без отопления?', 'Что покупал работник на дневную зарплату 500 лет назад?', 'Один день в древнем городе: еда, работа и ночлег'],
     'История через одного героя и его бытовую задачу. Закадровый рассказ, карты и оригинальные иллюстрации.', 'Средняя: основная работа — историческое исследование.'),
    ('Географические загадки на картах',
     ['geography explained maps', 'geografía explicada mapas', 'geografia explicada mapas', 'géographie expliquée cartes', 'Geografie erklärt Karten', 'география загадки карты', '地理 地図 解説', 'भूगोल मानचित्र रहस्य'],
     ['Почему соседние города так трудно соединить дорогой?', 'Что скрывается под водой самого глубокого озера?', 'Почему люди живут по одну сторону гор, а другая пустует?'],
     'Один вопрос о конкретном месте, ответ через рельеф и карты. Начать можно с 2D-схем.', 'Средняя; с полноценной 3D-графикой — высокая.'),
    ('Исчезнувшие бренды и забытые технологии',
     ['forgotten brands obsolete technology history', 'marcas desaparecidas tecnología historia', 'marcas antigas tecnologias esquecidas', 'marques disparues technologies oubliées', 'verschwundene Marken vergessene Technik', 'исчезнувшие бренды забытые технологии', '消えたブランド 技術 歴史', 'पुराने ब्रांड भूली तकनीक'],
     ['Почему популярная техника исчезла из наших домов?', 'Бренд, который был у всех: что с ним случилось?', 'Забытая технология: почему от неё отказались?'],
     'История одного продукта: взлёт → перелом → причина исчезновения. Использовать собственный анализ и разрешённые изображения.', 'Средняя: нужны архивные источники.'),
]
LANG_NAMES = dict(en='английский', es='испанский', pt='португальский', fr='французский', de='немецкий', ru='русский', ja='японский', hi='хинди')


def topic_for(now):
    # Six slots daily; cycle through topics over consecutive days.
    local = now.astimezone(__import__('zoneinfo').ZoneInfo('Europe/Kyiv'))
    return (local.date().toordinal() * 6 + max(0, (local.hour - 10)//2)) % len(TOPICS)


def render_card(index, evidence, now):
    name, _, ideas, approach, difficulty = TOPICS[index]
    languages = ', '.join(dict.fromkeys(LANG_NAMES[row[2]] for row in evidence))
    lines = [f'💡 Идея для YouTube: {name}', '', 'Что можно снять:']
    lines += [f'{i}. {title}' for i, title in enumerate(ideas, 1)]
    lines += ['', '🎬 Подача', approach, 'Для первого теста: 3 видео по 12–20 минут.',
              '', '🌍 Где искать аудиторию', f'Найдены примеры по запросам на языках: {languages}. Язык самих видео требует проверки.',
              '', '🔎 Почему стоит проверить',
              f'В поиске по этой теме найдены свежие ролики {len(evidence)} независимых каналов, которые заметно обгоняют соседние публикации.',
              '', '🛠 Сложность', difficulty, '', '📎 Примеры для изучения']
    for rate, video, lang, base, subscribers in evidence:
        snip = video['snippet']
        views = int(video['statistics'].get('viewCount', 0))
        lines += [f"• {snip['channelTitle'][:65]} — {views:,} просмотров, {subscribers:,} подписчиков; {snip['publishedAt'][:10]}",
                  f"https://www.youtube.com/watch?v={video['id']}"]
    lines += ['', 'Идеи выше — редакционные предложения. Совпадение тем найдено поиском; содержание роликов не просмотрено. Сравнение просмотров приблизительное, возраст публикаций различается.',
              f"Данные проверены: {now.astimezone(__import__('zoneinfo').ZoneInfo('Europe/Kyiv')).strftime('%d.%m %H:%M')} по Киеву."]
    return '\n'.join(lines)
