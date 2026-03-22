"""Test suite for Discord emoji intent classification.

Tests the _classify_response_emoji function covering:
- Questions (ends with ? or ？)
- Done states (completed, finished, etc.)
- Default acknowledgment
- Edge cases and internationalization
"""

import pytest
from nanobot.channels.discord import _classify_response_emoji


class TestClassifyResponseEmoji:
    """Test emoji classification for bot response intents."""

    # ✅ Done/Completion tests
    def test_done_english_simple(self):
        """Recognize 'done' pattern."""
        assert _classify_response_emoji("All done!") == "✅"

    def test_done_english_completed(self):
        """Recognize 'completed' pattern."""
        assert _classify_response_emoji("Task completed successfully") == "✅"

    def test_done_english_finished(self):
        """Recognize 'finished' pattern."""
        assert _classify_response_emoji("I finished the report") == "✅"

    def test_done_chinese_jiancheng(self):
        """Recognize Chinese '已完成' pattern."""
        assert _classify_response_emoji("已完成了，请检查结果") == "✅"

    def test_done_chinese_gaoding(self):
        """Recognize Chinese '搞定' pattern."""
        assert _classify_response_emoji("搞定了！") == "✅"

    def test_done_already_has_checkmark(self):
        """Recognize response starting with ✅."""
        assert _classify_response_emoji("✅ Task completed") == "✅"

    # ❓ Question tests
    def test_question_english_question_mark(self):
        """Recognize English question mark at end."""
        assert _classify_response_emoji("Do you want to continue?") == "❓"

    def test_question_chinese_question_mark(self):
        """Recognize Chinese question mark at end."""
        assert _classify_response_emoji("你想继续吗？") == "❓"

    def test_question_multiple_questions(self):
        """Recognize multiple questions (ends with ?)."""
        assert _classify_response_emoji("What next? Any questions?") == "❓"

    # 👌 Default acknowledgment tests
    def test_default_empty_string(self):
        """Empty string returns default emoji."""
        assert _classify_response_emoji("") == "👌"

    def test_default_generic_message(self):
        """Generic message without questions or done markers."""
        assert _classify_response_emoji("Working on it") == "👌"

    def test_default_message_with_question_in_middle(self):
        """Question mark in middle doesn't trigger (only at end)."""
        assert _classify_response_emoji("What about this? Let me continue") == "👌"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
