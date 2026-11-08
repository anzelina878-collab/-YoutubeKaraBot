import datetime as dt
import unittest
from unittest.mock import patch
import bot


class BotTests(unittest.TestCase):
    def test_errors_never_disclose_secrets(self):
        url = 'https://www.googleapis.com/youtube/v3/search?key=SECRET'
        exc = bot.safe_http_error(url, 403, '{"error":{"message":"SECRET", "errors":[{"reason":"quotaExceeded"}]}}')
        self.assertEqual(str(exc), 'YouTube: HTTP 403, причина=quotaExceeded')
        self.assertNotIn('SECRET', str(exc))
        tg = bot.safe_http_error('https://api.telegram.org/botSECRET/sendMessage', 400,
                                 '{"description":"Bad Request: chat not found SECRET"}')
        self.assertEqual(tg.reason, 'chat_not_found')
        self.assertNotIn('SECRET', str(tg))
        unknown = bot.safe_http_error(url, 403, '{"error":{"errors":[{"reason":"SECRET"}]}}')
        self.assertNotIn('SECRET', str(unknown))

    def test_quota_reset_uses_pacific_timezone(self):
        now = dt.datetime(2026, 11, 8, 16, tzinfo=bot.UTC)
        delay = bot.retry_delay(bot.ApiError('YouTube', 403, 'quotaExceeded'), now)
        self.assertEqual(delay, 16*3600+5*60)

    def test_schedule_dst(self):
        self.assertEqual(bot.slot(dt.datetime(2026, 11, 8, 8, 0, tzinfo=bot.UTC)), '2026-11-08-10')
        self.assertEqual(bot.slot(dt.datetime(2026, 7, 8, 7, 0, tzinfo=bot.UTC)), '2026-07-08-10')
        self.assertIsNone(bot.slot(dt.datetime(2026, 11, 8, 9, 0, tzinfo=bot.UTC)))

    def test_video_filter(self):
        now = dt.datetime(2026, 11, 8, tzinfo=bot.UTC)
        v = {'snippet': {'publishedAt': '2026-10-15T00:00:00Z'}, 'contentDetails': {'duration': 'PT20M'}}
        self.assertTrue(bot.eligible(v, now))
        v['contentDetails']['duration'] = 'PT3M'
        self.assertFalse(bot.eligible(v, now))
        v['contentDetails']['duration'] = 'PT20M'
        v['snippet']['publishedAt'] = '2026-09-01T00:00:00Z'
        self.assertFalse(bot.eligible(v, now))

    def test_card_requires_independent_channels(self):
        with patch.dict(bot.os.environ, {'DATABASE_PATH': ':memory:', 'TELEGRAM_CHAT_ID': '42'}):
            db = bot.database()
            now = dt.datetime(2026, 11, 8, 12, tzinfo=bot.UTC)
            def video(vid, channel):
                return {'id': vid, 'snippet': {'channelId': channel, 'title': 'Example', 'channelTitle': channel, 'publishedAt': '2026-11-01T00:00:00Z'}, 'statistics': {'viewCount': '50000'}}
            rows = [(5000, video('a', 'one'), 'en'), (5000, video('b', 'two'), 'fr')]
            with patch.object(bot, 'discover', return_value=rows), patch.object(bot, 'baseline', return_value=(1000, 5)), patch.object(bot, 'youtube', return_value={'items': [{'statistics': {'subscriberCount': '5000'}}]}), patch.object(bot, 'telegram') as send:
                self.assertEqual(bot.report(db, now), 1)
                text = send.call_args.kwargs['text']
                self.assertIn('Что снять первым', text)
                self.assertIn('https://www.youtube.com/channel/one', text)
                self.assertIn('Язык: не указан', text)
                self.assertNotIn('Кандидат для исследования', text)
                self.assertNotIn('метаданные', text)
                self.assertLess(len(text), 4096)
                self.assertEqual(bot.report(db, now), 0)
                send.assert_called_once()

    def test_single_channel_is_not_sent(self):
        with patch.dict(bot.os.environ, {'DATABASE_PATH': ':memory:'}):
            db = bot.database()
            with patch.object(bot, 'discover', return_value=[]), patch.object(bot, 'telegram') as send:
                self.assertEqual(bot.report(db, dt.datetime.now(bot.UTC)), 0)
                send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
