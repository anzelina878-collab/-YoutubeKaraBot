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
from zoneinfo import ZoneInfo

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


def request(url, data=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=45) as response:
        return json.load(response)


def telegram(method, **data):
    result = request('https://api.telegram.org/bot' + os.environ['TELEGRAM_BOT_TOKEN'] + '/' + method, data)
    if not result.get('ok'):
        raise RuntimeError('Telegram API rejected request')
    return result['result']


def youtube(resource, **params):
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
                     'CREATE TABLE IF NOT EXISTS observations(id TEXT, at TEXT, views INTEGER);')
    return db


def discover(db, now):
    candidates = {}
    for lang, query in LANGS.items():
        result = youtube('search', part='snippet', type='video', maxResults=10,
                         q=query, relevanceLanguage=lang, order='viewCount', videoDuration='long',
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
    return sorted(results, key=lambda row: row[0], reverse=True)[:6]


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


def report(db, now, dry_run=False):
    rows = discover(db, now)
    if not rows:
        return 0  # No filler alerts when no new candidates qualify.
    for rate, video, lang in rows:
        base, count = baseline(video, now)
        views = int(video['statistics'].get('viewCount', 0))
        comparison = (f'{rate/base:.1f}× медианы просмотров/день у {count} соседних видео'
                      if base else 'недостаточно сопоставимых видео')
        snip = video['snippet']
        message = (f"Кандидат для исследования ниши\n\n{snip['title'][:220]}\n"
                   f"Канал: {snip['channelTitle']}\n"
                   f"Язык поиска: {lang}; метаданные языка: {snip.get('defaultAudioLanguage', 'нет данных')}\n"
                   f"Опубликовано: {snip['publishedAt'][:10]}\n"
                   f"Длина: {duration(video['contentDetails']['duration'])//60} мин\n"
                   f"Просмотры: {views:,}; среднее за срок жизни: {rate:,.0f}/день\n"
                   f"Сравнение: {comparison}. Возраст видео различается.\n"
                   f"Проверено: {now.strftime('%Y-%m-%d %H:%M UTC')}\n"
                   f"https://www.youtube.com/watch?v={video['id']}\n\n"
                   'Это отдельный сигнал, а не подтверждённая новая ниша. '
                   'Причины успеха и повторяемость у других авторов не проверены.')
        if dry_run:
            print(message + '\n')
        else:
            telegram('sendMessage', chat_id=os.environ['TELEGRAM_CHAT_ID'], text=message,
                     link_preview_options={'is_disabled': True})
            db.execute('INSERT OR IGNORE INTO sent VALUES(?)', (video['id'],))
            db.commit()
            time.sleep(1)
    return len(rows)


def slot(now):
    local = now.astimezone(ZoneInfo('Europe/Kyiv'))
    if local.hour in (10, 12, 14, 16, 18, 20):
        return local.strftime('%Y-%m-%d-%H')
    return None


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--setup', action='store_true')
    args = parser.parse_args()
    load_env()
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
    retry_after = 0
    print('Бот запущен. Расписание: 10,12,14,16,18,20 Europe/Kyiv.', flush=True)
    while True:
        now = dt.datetime.now(UTC)
        key = slot(now)
        if key and time.monotonic() >= retry_after and not db.execute('SELECT 1 FROM runs WHERE slot=?', (key,)).fetchone():
            try:
                delivered = report(db, now)
                db.execute('INSERT INTO runs VALUES(?)', (key,))
                db.commit()
                print(f'Цикл {key}: отправлено {delivered}', flush=True)
            except Exception as exc:
                # Never print HTTP exception URLs: they may contain API tokens.
                print('Сбой цикла:', type(exc).__name__, flush=True)
                retry_after = time.monotonic() + 600
        time.sleep(30)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        raise SystemExit('Ошибка: ' + type(exc).__name__ + '. Проверьте ключи, сеть и квоту API.') from None
