import unittest

from downloader.smart_extractor import extract_telegram_post_candidates


class TelegramExactMessageExtractionTests(unittest.TestCase):
    def test_only_requested_message_media_is_extracted(self):
        page = '''
        <div class="tgme_widget_message_wrap js-widget_message_wrap">
          <div class="tgme_widget_message" data-post="syrevarch/8452">
            <video src="https://cdn.example/previous.mp4"></video>
            <a class="tgme_widget_message_date" href="https://t.me/syrevarch/8452"></a>
          </div>
        </div>
        <div class="tgme_widget_message_wrap js-widget_message_wrap">
          <div class="tgme_widget_message" data-post="syrevarch/8453">
            <video src="https://cdn.example/target.mp4"></video>
            <a class="tgme_widget_message_date" href="https://t.me/syrevarch/8453"></a>
          </div>
        </div>
        <div class="tgme_widget_message_wrap js-widget_message_wrap">
          <div class="tgme_widget_message" data-post="syrevarch/8454">
            <video src="https://cdn.example/next.mp4"></video>
            <a class="tgme_widget_message_date" href="https://t.me/syrevarch/8454"></a>
          </div>
        </div>
        '''
        candidates = extract_telegram_post_candidates(
            page,
            "https://t.me/syrevarch/8453?embed=1",
            channel="syrevarch",
            message_id=8453,
        )
        self.assertEqual([item.url for item in candidates], ["https://cdn.example/target.mp4"])

    def test_embed_player_uses_exact_post_href(self):
        page = '''
        <a class="tgme_widget_message_video_player" href="https://t.me/syrevarch/8452">
          <video src="https://cdn.example/previous.mp4"></video>
        </a>
        <a class="tgme_widget_message_video_player" href="https://t.me/syrevarch/8453">
          <video src="https://cdn.example/target.mp4"></video>
        </a>
        <a class="tgme_widget_message_video_player" href="https://t.me/syrevarch/8454">
          <video src="https://cdn.example/next.mp4"></video>
        </a>
        '''
        candidates = extract_telegram_post_candidates(
            page,
            "https://t.me/syrevarch/8453?embed=1&mode=tme",
            channel="syrevarch",
            message_id=8453,
        )
        self.assertEqual(
            [item.url for item in candidates],
            ["https://cdn.example/target.mp4"],
        )

    def test_embed_player_without_exact_href_fails_closed(self):
        page = '''
        <a class="tgme_widget_message_video_player" href="https://t.me/syrevarch/8454">
          <video src="https://cdn.example/next.mp4"></video>
        </a>
        '''
        self.assertEqual(
            extract_telegram_post_candidates(
                page,
                "https://t.me/syrevarch/8453?embed=1",
                channel="syrevarch",
                message_id=8453,
            ),
            [],
        )

    def test_missing_requested_message_fails_closed(self):
        page = '''
        <div class="tgme_widget_message_wrap">
          <div class="tgme_widget_message" data-post="syrevarch/8454">
            <video src="https://cdn.example/next.mp4"></video>
          </div>
        </div>
        '''
        self.assertEqual(
            extract_telegram_post_candidates(
                page,
                "https://t.me/syrevarch/8453?embed=1",
                channel="syrevarch",
                message_id=8453,
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
