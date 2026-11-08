"""Private YouTube discovery bot. Python 3.11+, standard library only."""
import datetime as dt
import json
import os
import re
import sqlite3
import statistics
import time
import urllib.parse
import urllib.request
import urllib.error
from zoneinfo import ZoneInfo
from ideas import TOPICS, topic_for, render_card

UTC = dt.timezone.utc
LANGS = {
    'en': 'documentary explained', 'es': 'documental explicado',
    'pt': 'documentário curiosidades', 'fr': 'histoire explication',
    'de': 'dokumentation erklärt', 'ru': 'история объяснение',
    'ja': '地理 解説', 'hi': 'रहस्य इतिहास',
}


def load_env():
    if os.path.exists('.env'):
        with open('.env', encoding='utf-8') as f:
            for line in f:
                if '=' in line and not line.lstrip().startswith('#'):
                    k, v = line.strip().split('=', 1)
                    os.environ.setdefault(k, v.strip().strip('"').strip("'"))


class ApiError(Exception):
    def __init__(self, service, status, reason):
        self.service, self.status, self.reason = service, status, reason
        super().__init__(f'{service}: HTTP {status}, причина={reason}')


def safe_http_error(url, status, body):
    host = urllib.parse.urlsplit(url).hostname
    service = 'YouTube' if host == 'www.googleapis.com' else 'Telegram' if host == 'api.telegram.org' else 'HTTP'
    reason = 'unknown'
    try:
        data = json.loads(body)
        if service == 'YouTube':
            errors = data.get('error', {}).get('errors', [])
            raw = errors[0].get('reason', '') if errors else ''
            allowed = {'quotaExceeded', 'dailyLimitExceeded', 'keyInvalid', 'accessNotConfigured',
                       'ipRefererBlocked', 'forbidden', 'badRequest', 'invalidParameter',
                       'youtubeSignupRequired', 'rateLimitExceeded', 'backendError'}
            reason = raw if raw in allowed else 'unclassified'
            # ErrorInfo reasons expose API restrictions without logging free-text messages.
            for detail in data.get('error', {}).get('details', []):
                r = detail.get('reason')
                if r in {'API_KEY_INVALID', 'API_KEY_SERVICE_BLOCKED', 'API_KEY_HTTP_REFERRER_BLOCKED',
                         'API_KEY_IP_ADDRESS_BLOCKED', 'SERVICE_DISABLED', 'RATE_LIMIT_EXCEEDED'}:
                    reason = r
        elif service == 'Telegram':
            desc = str(data.get('description', '')).lower()
            if 'chat not found' in desc:
                reason = 'chat_not_found'
            elif 'blocked' in desc:
                reason = 'bot_blocked'
            elif 'too long' in desc:
                reason = 'message_too_long'
            elif status == 401:
                reason = 'token_invalid'
            elif status == 429:
                reason = 'rate_limit'
    except (ValueError, TypeError, AttributeError, IndexError):
        pass
    return ApiError(service, status, reason)


def request(url, data=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise safe_http_error(url, exc.code, exc.read(32768)) from None


def retry_delay(exc, now):
    if isinstance(exc, ApiError) and exc.reason in {'quotaExceeded', 'dailyLimitExceeded'}:
        pacific = now.astimezone(ZoneInfo('America/Los_Angeles'))
        reset = dt.datetime.combine(pacific.date()+dt.timedelta(days=1), dt.time(0, 5), pacific.tzinfo)
        return max(600, (reset.astimezone(UTC)-now).total_seconds())
    if isinstance(exc, ApiError) and (exc.status == 429 or exc.reason in {'RATE_LIMIT_EXCEEDED', 'rateLimitExceeded'}):
        return 3600
    return 600


def telegram(method, **data):
    result = request('https://api.telegram.org/bot' + os.environ['TELEGRAM_BOT_TOKEN'] + '/' + method, data)
    if not result.get('ok'):
        raise RuntimeError('Telegram API rejected request')
    return result['result']


_last_youtube_request = None


def youtube(resource, **params):
    global _last_youtube_request
    if _last_youtube_request is not None:
        time.sleep(max(0, 2.0 - (time.monotonic() - _last_youtube_request)))
    _last_youtube_request = time.monotonic()
    params['key'] = os.environ['YOUTUBE_API_KEY']
    return request('https://www.googleapis.com/youtube/v3/' + resource + '?' + urllib.parse.urlencode(params))


def duration(value):
    m = re.fullmatch(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', value)
    return sum(int(v or 0) * unit for v, unit in zip(m.groups(), (3600, 60, 1))) if m else 0


def published(video):
    return dt.datetime.fromisoformat(video['snippet']['publishedAt'].replace('Z', '+00:00'))


def eligible(video, now):
    return (dt.timedelta(0) <= now - published(video) <= dt.timedelta(days=30)
            and duration(video['contentDetails']['duration']) >= 480
            and video['snippet'].get('liveBroadcastContent', 'none') == 'none'
            and 'liveStreamingDetails' not in video)


def videos(ids):
    if not ids:
        return []
    return youtube('videos', part='snippet,statistics,contentDetails,liveStreamingDetails',
                   id=','.join(ids))['items']


def database():
    db = sqlite3.connect(os.getenv('DATABASE_PATH', 'state.sqlite3'))
    db.executescript('CREATE TABLE IF NOT EXISTS sent(id TEXT PRIMARY KEY);'
                     'CREATE TABLE IF NOT EXISTS runs(slot TEXT PRIMARY KEY);'
                     'CREATE TABLE IF NOT EXISTS observations(id TEXT, at TEXT, views INTEGER);'
                     'CREATE TABLE IF NOT EXISTS search_cache(key TEXT PRIMARY KEY, expires REAL, data TEXT);'
                     'CREATE TABLE IF NOT EXISTS runtime(key TEXT PRIMARY KEY, value REAL);')
    return db


def cached_search(db, key, now, **params):
    row = db.execute('SELECT expires,data FROM search_cache WHERE key=?', (key,)).fetchone()
    if row and row[0] > now.timestamp():
        return json.loads(row[1])
    result = youtube('search', **params)
    db.execute('INSERT OR REPLACE INTO search_cache VALUES(?,?,?)',
               (key, now.timestamp()+7200, json.dumps(result)))
    db.commit()
    return result


def cooldown_until(db):
    row = db.execute("SELECT value FROM runtime WHERE key='retry_after'").fetchone()
    return row[0] if row else 0


def save_cooldown(db, until):
    db.execute("INSERT OR REPLACE INTO runtime VALUES('retry_after',?)", (until,))
    db.commit()


def discover(db, now, index=None, languages=None):
    candidates = {}
    index = topic_for(now) if index is None else index
    topic = TOPICS[index]
    queries = dict(zip(LANGS, topic[1]))
    for position, lang in enumerate(languages or list(LANGS)):
        query = queries[lang].removesuffix(' faceless')
        order = 'viewCount' if position % 2 == 0 else 'date'
        result = cached_search(db, f'expanded:{index}:{lang}:{order}', now, part='snippet', type='video', maxResults=50,
                         q=query, relevanceLanguage=lang, order=order, videoDuration='any',
                         publishedAfter=(now-dt.timedelta(days=30)).isoformat().replace('+00:00', 'Z'))
        for item in result['items']:
            candidates.setdefault(item['id']['videoId'], lang)
    results = []
    ids = list(candidates)
    for start in range(0, len(ids), 50):
        for video in videos(ids[start:start+50]):
            if not eligible(video, now):
                continue
            views = int(video.get('statistics', {}).get('viewCount', 0))
            db.execute('INSERT INTO observations VALUES(?,?,?)', (video['id'], now.isoformat(), views))
            if db.execute('SELECT 1 FROM sent WHERE id=?', (video['id'],)).fetchone():
                continue
            age = max((now-published(video)).total_seconds()/86400, 1)
            if views >= int(os.getenv('MIN_VIEWS', '10000')):
                results.append((views/age, video, candidates[video['id']]))
    db.commit()
    return sorted(results, key=lambda row: row[0], reverse=True)[:60]


def baseline(video, now):
    channel = youtube('channels', part='contentDetails', id=video['snippet']['channelId'])['items']
    if not channel:
        return None, 0
    uploads = channel[0]['contentDetails']['relatedPlaylists']['uploads']
    recent = youtube('playlistItems', part='contentDetails', playlistId=uploads, maxResults=20)['items']
    peers = videos([i['contentDetails']['videoId'] for i in recent])
    rates = []
    for peer in peers:
        if peer['id'] == video['id'] or not eligible(peer, now):
            continue
        age = max((now-published(peer)).total_seconds()/86400, 1)
        rates.append(int(peer.get('statistics', {}).get('viewCount', 0))/age)
    return (statistics.median(rates) if len(rates) >= 3 else None), len(rates)


def report(db, now, dry_run=False, index=None, languages=None):
    index = topic_for(now) if index is None else index
    # One editorial concept per day at most; delivery history survives restarts.
    concept_key = f"idea:{index}:{now.astimezone(ZoneInfo('Europe/Kyiv')).date()}"
    if db.execute('SELECT 1 FROM sent WHERE id=?', (concept_key,)).fetchone():
        return 0
    if now.timestamp() < cooldown_until(db):
        return 0
    rows = discover(db, now, index=index, languages=languages)
    evidence = []
    channels_seen = set()
    for rate, video, lang in rows:
        channel_id = video['snippet']['channelId']
        if channel_id in channels_seen:
            continue
        channel = youtube('channels', part='statistics,snippet', id=channel_id).get('items', [])
        stats = channel[0].get('statistics', {}) if channel else {}
        if stats.get('hiddenSubscriberCount') or 'subscriberCount' not in stats:
            continue
        video['snippet']['channelLanguage'] = channel[0].get('snippet', {}).get('defaultLanguage')
        subs = int(stats['subscriberCount'])
        views = int(video['statistics'].get('viewCount', 0))
        if not 100 <= subs <= int(os.getenv('MAX_SUBSCRIBERS', '300000')):
            continue
        if views < max(10000, subs * 2):
            continue
        base, count = baseline(video, now)
        if base is None or base <= 0 or rate < base * 2:
            continue
        channels_seen.add(channel_id)
        evidence.append((rate, video, lang, base, subs))
        if len(evidence) == 3:
            break
    if len(evidence) < 2:
        return 0
    message = render_card(index, evidence, now)
    if dry_run:
        print(message)
    else:
        telegram('sendMessage', chat_id=os.environ['TELEGRAM_CHAT_ID'], text=message,
                 link_preview_options={'is_disabled': True})
        db.executemany('INSERT OR IGNORE INTO sent VALUES(?)',
                       [(concept_key,)] + [(row[1]['id'],) for row in evidence])
        db.commit()
    return 1


def slot(now):
    local = now.astimezone(ZoneInfo('Europe/Kyiv'))
    if local.hour in (10, 12, 14, 16, 18, 20):
        return local.strftime('%Y-%m-%d-%H')
    return None


def search_plan(db, now):
    key = slot(now)
    saved = db.execute('SELECT data FROM search_cache WHERE key=?', ('plan:'+key,)).fetchone()
    if saved:
        return json.loads(saved[0])
    row = db.execute("SELECT value FROM runtime WHERE key='search_cursor'").fetchone()
    cursor = int(row[0]) if row else 0
    langs = list(LANGS)
    plan = []
    for step in range(4):
        n = cursor + step
        # Rotate languages after one complete pass through every topic.
        language_start = (n // len(TOPICS) * 2 + n % 4 * 2) % len(langs)
        plan.append([(n % len(TOPICS)), [langs[language_start], langs[(language_start+1) % len(langs)]]])
    db.execute("INSERT OR REPLACE INTO runtime VALUES('search_cursor',?)", (cursor+4,))
    db.execute('INSERT OR REPLACE INTO search_cache VALUES(?,?,?)',
               ('plan:'+key, now.timestamp()+86400, json.dumps(plan)))
    db.commit()
    return plan


def scheduled_delivery(db, now):
    key = slot(now)
    if not key or db.execute('SELECT 1 FROM runs WHERE slot=?', (key,)).fetchone():
        return 0
    if now.timestamp() < cooldown_until(db):
        return 0
    delivered = 0
    for index, languages in search_plan(db, now):
        try:
            delivered += report(db, now, index=index, languages=languages)
        except ApiError as exc:
            if exc.service != 'YouTube':
                raise
            save_cooldown(db, dt.datetime.now(UTC).timestamp() + retry_delay(exc, now))
            print('Поиск отложен:', str(exc), flush=True)
            return delivered
        except (urllib.error.URLError, TimeoutError):
            save_cooldown(db, dt.datetime.now(UTC).timestamp() + 600)
            print('Поиск отложен: ошибка соединения', flush=True)
            return delivered
    db.execute('INSERT OR IGNORE INTO runs VALUES(?)', (key,))
    db.commit()
    print(f'Проверено направлений: 4; карточек: {delivered}', flush=True)
    return delivered


def send_examples():
    cards = [
        "💡 Ниша: географические загадки на картах\n\n🎬 Подача\nКарты, простые 2D-схемы и закадровая озвучка.\n\nЧто снять первым:\n1. Почему между соседними островами нет моста?\n2. Что скрывает рельеф под глубоким озером?\n3. Почему два близких города трудно соединить дорогой?\n\nПочему стоит попробовать\nУ японского канала 深読み地図 ролик о мосте между Сикоку и Кюсю набрал около 2,3 млн просмотров, а ролик о Гавайях — около 498 тыс.\n\n📺 Канал: 深読み地図\nhttps://www.youtube.com/@fukayomi-chizu\nЯзык: японский\nПример: https://www.youtube.com/watch?v=3xdIwOjzUL8\n\nИдеи — предложения для самостоятельных роликов. Данные из подборки на 6 ноября 2026, сейчас не перепроверены. Отсутствие автора в кадре и монетизацию проверь вручную.",
        "💡 Ниша: повседневная жизнь людей в прошлом\n\n🎬 Подача\nОригинальные иллюстрации, карты, анимация и закадровый рассказ.\n\nЧто снять первым:\n1. Как обычная семья переживала зиму без отопления?\n2. Что покупал работник на дневную зарплату 500 лет назад?\n3. Что происходило, если римскому солдату задерживали жалованье?\n\nПочему стоит попробовать\nУ французского канала Epopik видео о том, как римские солдаты спали рядом с врагом, набрало около 482 тыс. просмотров. Другой ролик об армейских рангах — около 207 тыс.\n\n📺 Канал: Epopik\nhttps://www.youtube.com/@epopikexplain\nЯзык: французский\nПример: https://www.youtube.com/watch?v=CMfPXynsGZg\n\nИдеи — предложения для самостоятельных роликов. Данные из подборки на 6 ноября 2026, сейчас не перепроверены. Отсутствие автора в кадре и монетизацию проверь вручную."
    ]
    for card in cards:
        telegram('sendMessage', chat_id=os.environ['TELEGRAM_CHAT_ID'], text=card,
                 link_preview_options={'is_disabled': True})
        time.sleep(1)
    print('Отправлены две карточки-примера.', flush=True)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--setup', action='store_true')
    parser.add_argument('--examples', action='store_true')
    args = parser.parse_args()
    load_env()
    if args.examples:
        if not all(os.getenv(k) for k in ('TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID')):
            raise SystemExit('Нужны TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID')
        send_examples()
        return
    required = ['TELEGRAM_BOT_TOKEN'] if args.setup else ['YOUTUBE_API_KEY']
    if not args.setup and not args.dry_run:
        required += ['TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID']
    if any(not os.getenv(k) for k in required):
        raise SystemExit('Заполните в .env: ' + ', '.join(required))
    if args.setup:
        me = telegram('getMe')
        print('Бот: @' + me['username'])
        print('После /start вашему боту найдите свой личный chat_id:')
        for update in telegram('getUpdates', timeout=0):
            message = update.get('message', {})
            chat = message.get('chat', {})
            if chat.get('type') == 'private':
                print('chat_id:', chat['id'], 'username:', chat.get('username', '(нет)'))
        return
    db = database()
    if args.once or args.dry_run:
        report(db, dt.datetime.now(UTC), args.dry_run)
        return
    delivery_retry_after = 0
    print('KARA-CARDS-3.4 | Бот запущен. Расписание: 10,12,14,16,18,20 Europe/Kyiv.', flush=True)
    while True:
        now = dt.datetime.now(UTC)
        key = slot(now)
        if key and now.timestamp() >= delivery_retry_after and not db.execute('SELECT 1 FROM runs WHERE slot=?', (key,)).fetchone():
            try:
                delivered = scheduled_delivery(db, now)
                print(f'Цикл {key}: отправлено {delivered}', flush=True)
            except Exception as exc:
                # Never print HTTP exception URLs: they may contain API tokens.
                print('Сбой цикла:', str(exc) if isinstance(exc, ApiError) else type(exc).__name__, flush=True)
                delay = retry_delay(exc, dt.datetime.now(UTC))
                delivery_retry_after = dt.datetime.now(UTC).timestamp() + delay
                print(f'Повтор не ранее чем через {int(delay // 60)} мин.', flush=True)
        time.sleep(30)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        raise SystemExit('Ошибка: ' + type(exc).__name__ + '. Проверьте ключи, сеть и квоту API.') from None
