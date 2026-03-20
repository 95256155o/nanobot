"""Tests for IntentClassifier."""
from __future__ import annotations
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from nanobot.intent.classifier import IntentClassifier, Intent, IntentResult

SAMPLE_RESPONSE = json.dumps({
    "primary": [
        {"label": "code_task", "description": "fix the login bug", "confidence": 0.88},
        {"label": "research",  "description": "find JWT libraries",  "confidence": 0.55},
    ],
    "side": [
        {"label": "memo", "description": "save progress note", "confidence": 0.91},
    ],
})


def _mock_completion(content: str):
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


@pytest.mark.asyncio
async def test_classify_returns_primary_and_side_intents():
    with patch("nanobot.intent.classifier.acompletion", new_callable=AsyncMock) as mock_ac:
        mock_ac.return_value = _mock_completion(SAMPLE_RESPONSE)
        clf = IntentClassifier()
        result = await clf.classify("fix the login bug and remind me later", "openrouter/test-model")

    assert isinstance(result, IntentResult)
    assert len(result.primary) == 2
    assert result.primary[0].label == "code_task"
    assert result.primary[0].confidence == pytest.approx(0.88)
    assert len(result.side) == 1
    assert result.side[0].label == "memo"


@pytest.mark.asyncio
async def test_classify_handles_malformed_json_gracefully():
    """Bad LLM output falls back to a low-confidence chat intent."""
    with patch("nanobot.intent.classifier.acompletion", new_callable=AsyncMock) as mock_ac:
        mock_ac.return_value = _mock_completion("This is not JSON at all.")
        clf = IntentClassifier()
        result = await clf.classify("hello", "openrouter/test-model")

    assert isinstance(result, IntentResult)
    assert len(result.primary) >= 1
    assert result.primary[0].label == "chat"
    assert result.primary[0].confidence < 0.5


@pytest.mark.asyncio
async def test_classify_passes_model_to_acompletion():
    with patch("nanobot.intent.classifier.acompletion", new_callable=AsyncMock) as mock_ac:
        mock_ac.return_value = _mock_completion(SAMPLE_RESPONSE)
        clf = IntentClassifier()
        await clf.classify("hello", "openrouter/my-model")

    call_kwargs = mock_ac.call_args
    assert call_kwargs.kwargs["model"] == "openrouter/my-model"


@pytest.mark.asyncio
async def test_classify_raises_on_api_error():
    with patch("nanobot.intent.classifier.acompletion", new_callable=AsyncMock) as mock_ac:
        mock_ac.side_effect = RuntimeError("API down")
        clf = IntentClassifier()
        with pytest.raises(RuntimeError, match="API down"):
            await clf.classify("hello", "openrouter/test-model")
