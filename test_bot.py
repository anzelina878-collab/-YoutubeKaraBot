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

    def test_delivery_dedup(self):
        with patch.dict(bot.os.environ, {'DATABASE_PATH': ':memory:', 'TELEGRAM_CHAT_ID': '42'}):
            db = bot.database()
            v = {'id': 'abc', 'snippet': {'title': 'Test', 'channelTitle': 'Channel', 'publishedAt': '2026-11-01T00:00:00Z'}, 'contentDetails': {'duration': 'PT20M'}, 'statistics': {'viewCount': '12000'}}
            with patch.object(bot, 'discover', return_value=[(1000, v, 'en')]), patch.object(bot, 'baseline', return_value=(500, 5)), patch.object(bot, 'telegram') as send, patch.object(bot.time, 'sleep'):
                bot.report(db, dt.datetime.now(bot.UTC))
                send.assert_called_once()
                self.assertIsNotNone(db.execute('SELECT 1 FROM sent WHERE id="abc"').fetchone())


if __name__ == '__main__':
    unittest.main()
