import datetime as dt
import unittest
from unittest.mock import patch
import bot


class BotTests(unittest.TestCase):
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
                self.assertIn('Что можно снять', send.call_args.kwargs['text'])
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
