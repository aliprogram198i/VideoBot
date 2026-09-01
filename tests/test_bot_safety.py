import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

import bot


def resolver_for(ip):
    return lambda host, port, type=None: [(None, None, None, None, (ip, port))]


class UrlValidationTests(unittest.TestCase):
    def test_rejects_non_public_addresses(self):
        for host in (
            "127.0.0.1", "localhost", "10.0.0.1", "172.16.0.1",
            "192.168.1.1", "169.254.169.254", "[::1]", "[fc00::1]",
        ):
            with self.assertRaises(ValueError, msg=host):
                bot.validate_public_http_url(f"http://{host}/", resolver=resolver_for("127.0.0.1"))

    def test_accepts_public_address_and_redacts_query(self):
        parsed = bot.validate_public_http_url("https://example.com/video", resolver=resolver_for("8.8.8.8"))
        self.assertEqual(parsed.hostname, "example.com")
        self.assertEqual(bot.redact_url("https://example.com/a?token=private#frag"), "https://example.com/a?<redacted>")

    def test_rejects_embedded_credentials(self):
        with self.assertRaises(ValueError):
            bot.validate_public_http_url("https://user:pass@example.com/", resolver=resolver_for("8.8.8.8"))


class ProcessCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_reaps_child(self):
        process = await asyncio.create_subprocess_exec("sh", "-c", "sleep 30")
        with self.assertRaises(asyncio.TimeoutError):
            await bot.communicate_with_cleanup(process, 0.01)
        self.assertIsNotNone(process.returncode)


class DatabaseDeletionTests(unittest.TestCase):
    def test_delete_user_removes_only_related_rows(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bot, "DB_FILE", os.path.join(directory, "db.sqlite")):
            bot.init_db()
            conn = bot.get_db()
            with conn:
                conn.execute("INSERT INTO users (user_id, downloads) VALUES (1, 0), (2, 0)")
                conn.execute("INSERT INTO downloads (user_id) VALUES (1), (2)")
                conn.execute("INSERT INTO broadcast_messages (broadcast_id, user_id, message_id) VALUES (1, 1, 10), (1, 2, 11)")
            conn.close()
            bot.delete_user(1)
            conn = bot.get_db()
            self.assertIsNone(conn.execute("SELECT 1 FROM users WHERE user_id=1").fetchone())
            self.assertIsNotNone(conn.execute("SELECT 1 FROM users WHERE user_id=2").fetchone())
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM downloads WHERE user_id=1").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM broadcast_messages WHERE user_id=1").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM broadcast_messages WHERE user_id=2").fetchone()[0], 1)
            conn.close()

    def test_save_download_creates_one_row_and_increments_counter(self):
        class User:
            id = 7
            username = "tester"
            first_name = "Test"
            last_name = None
        with tempfile.TemporaryDirectory() as directory, patch.object(bot, "DB_FILE", os.path.join(directory, "db.sqlite")):
            bot.init_db()
            bot.register_user(User())
            bot.save_download(User(), "https://example.com/video", "Example", "video", "720p")
            conn = bot.get_db()
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM downloads WHERE user_id=7").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT downloads FROM users WHERE user_id=7").fetchone()[0], 1)
            conn.close()


if __name__ == "__main__":
    unittest.main()
