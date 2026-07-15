import unittest

from scraper import validate_summary


class ValidateSummaryTests(unittest.TestCase):
    def test_accepts_normal_japanese_summary(self):
        summary = "産総研・東大・九大は、混合廃プラスチックからポリウレタンを分離回収する新手法を開発した。"
        self.assertEqual(validate_summary(summary, 150), (True, ""))

    def test_rejects_reasoning_leak(self):
        summary = "We need to summarize this article. Let's count characters. 要約本文をこれから作成する。"
        valid, reason = validate_summary(summary, 150)
        self.assertFalse(valid)
        self.assertEqual(reason, "思考過程を含む応答")

    def test_rejects_excessively_long_response(self):
        valid, reason = validate_summary("あ" * 501, 150)
        self.assertFalse(valid)
        self.assertIn("長すぎる", reason)

    def test_rejects_english_only_response(self):
        valid, reason = validate_summary("This is a short English summary of the automotive article.", 150)
        self.assertFalse(valid)
        self.assertEqual(reason, "日本語が不足した応答")


if __name__ == "__main__":
    unittest.main()
