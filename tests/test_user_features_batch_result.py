from types import SimpleNamespace

from plugins import user_features


class _Conn:
    def __init__(self, count):
        self.count = count

    def execute(self, *_args):
        return self

    def fetchone(self):
        return {"count": self.count}

    def close(self):
        pass


class _Bot:
    def __init__(self, count):
        self.count = count

    def get_db(self):
        return _Conn(self.count)


def test_download_record_count_reads_existing_ledger():
    assert user_features._download_record_count(_Bot(7), 123, "https://example.com/x") == 7


def test_download_record_count_fails_closed_on_db_error():
    class BrokenBot:
        def get_db(self):
            raise RuntimeError("db unavailable")

    assert user_features._download_record_count(BrokenBot(), 123, "https://example.com/x") == 0


def test_batch_summary_reports_success_and_failed_indexes():
    text = user_features._batch_summary("ar", 4, [1, 3], [2, 4])
    assert "نجح: 2" in text
    assert "فشل: 2" in text
    assert "2, 4" in text
    assert "2/4" in text or "4/4" in text


def test_batch_summary_reports_all_success():
    text = user_features._batch_summary("en", 3, [1, 2, 3], [])
    assert "Succeeded: 3" in text
    assert "Failed: 0" in text
    assert "All links downloaded successfully." in text
    assert "3/3" in text
