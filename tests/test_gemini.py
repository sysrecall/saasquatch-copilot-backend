import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).parent.parent))

import gemini
from gemini import generate_structured, GeminiError


def setup_function():
    # module-level rate-limit state must not leak between tests
    gemini._request_times.clear()


def _mock_response(status_code, text_body="", json_body=None):
    resp = MagicMock(status_code=status_code, text=text_body)
    if json_body is not None:
        resp.json.return_value = json_body
    return resp


def _ok_response(subject="hi", body="there"):
    return _mock_response(200, json_body={
        "candidates": [{"content": {"parts": [{"text": f'{{"subject": "{subject}", "body": "{body}"}}'}]}}]
    })


def test_raises_clearly_when_no_api_key():
    with patch.object(gemini, "GEMINI_API_KEY", None):
        try:
            generate_structured("hello", {"type": "OBJECT", "properties": {}})
            assert False, "should have raised"
        except GeminiError as e:
            assert "GEMINI_API_KEY" in str(e)


def test_non_retryable_400_raises_immediately_without_retrying():
    with patch.object(gemini, "GEMINI_API_KEY", "fake-key"), \
         patch("gemini.time.sleep") as mock_sleep:
        mock_resp = _mock_response(400, "bad request")
        with patch("gemini.requests.post", return_value=mock_resp) as mock_post:
            try:
                generate_structured("hello", {"type": "OBJECT", "properties": {}}, max_retries=5)
                assert False, "should have raised"
            except GeminiError as e:
                assert "400" in str(e)
        mock_post.assert_called_once()  # no retries for a client error
        mock_sleep.assert_not_called()


def test_parses_valid_structured_response_on_first_try():
    with patch.object(gemini, "GEMINI_API_KEY", "fake-key"):
        with patch("gemini.requests.post", return_value=_ok_response()) as mock_post:
            result = generate_structured("hello", {"type": "OBJECT", "properties": {}})
            assert result == {"subject": "hi", "body": "there"}
            mock_post.assert_called_once()


def test_raises_on_malformed_response_shape_without_retrying():
    with patch.object(gemini, "GEMINI_API_KEY", "fake-key"), patch("gemini.time.sleep") as mock_sleep:
        mock_resp = _mock_response(200, json_body={"unexpected": "shape"})
        with patch("gemini.requests.post", return_value=mock_resp):
            try:
                generate_structured("hello", {"type": "OBJECT", "properties": {}}, max_retries=5)
                assert False, "should have raised"
            except GeminiError:
                pass
        mock_sleep.assert_not_called()  # a bad shape won't fix itself by retrying


def test_429_retries_then_succeeds():
    with patch.object(gemini, "GEMINI_API_KEY", "fake-key"), patch("gemini.time.sleep") as mock_sleep:
        responses = [_mock_response(429, "rate limited"), _ok_response()]
        with patch("gemini.requests.post", side_effect=responses) as mock_post:
            result = generate_structured("hello", {"type": "OBJECT", "properties": {}}, max_retries=3)
            assert result == {"subject": "hi", "body": "there"}
            assert mock_post.call_count == 2
            mock_sleep.assert_called_once()  # backed off exactly once, before the successful retry


def test_429_respects_retry_after_header():
    with patch.object(gemini, "GEMINI_API_KEY", "fake-key"), patch("gemini.time.sleep") as mock_sleep:
        rate_limited = MagicMock(status_code=429, text="slow down", headers={"Retry-After": "7"})
        with patch("gemini.requests.post", side_effect=[rate_limited, _ok_response()]):
            generate_structured("hello", {"type": "OBJECT", "properties": {}}, max_retries=3)
            mock_sleep.assert_called_once_with(7.0)


def test_exhausts_retries_and_raises_the_real_error():
    with patch.object(gemini, "GEMINI_API_KEY", "fake-key"), patch("gemini.time.sleep"):
        always_429 = _mock_response(429, "still rate limited")
        with patch("gemini.requests.post", return_value=always_429) as mock_post:
            try:
                generate_structured("hello", {"type": "OBJECT", "properties": {}}, max_retries=3)
                assert False, "should have raised"
            except GeminiError as e:
                assert "429" in str(e) or "rate limit" in str(e).lower()
        assert mock_post.call_count == 3


def test_timeout_retries_then_succeeds():
    import requests as requests_module
    with patch.object(gemini, "GEMINI_API_KEY", "fake-key"), patch("gemini.time.sleep"):
        with patch("gemini.requests.post", side_effect=[requests_module.Timeout(), _ok_response()]) as mock_post:
            result = generate_structured("hello", {"type": "OBJECT", "properties": {}}, max_retries=3)
            assert result == {"subject": "hi", "body": "there"}
            assert mock_post.call_count == 2


def test_5xx_retries_then_succeeds():
    with patch.object(gemini, "GEMINI_API_KEY", "fake-key"), patch("gemini.time.sleep"):
        server_error = _mock_response(503, "overloaded")
        with patch("gemini.requests.post", side_effect=[server_error, _ok_response()]) as mock_post:
            result = generate_structured("hello", {"type": "OBJECT", "properties": {}}, max_retries=3)
            assert result == {"subject": "hi", "body": "there"}
            assert mock_post.call_count == 2


def test_on_retry_callback_fires_with_a_message():
    with patch.object(gemini, "GEMINI_API_KEY", "fake-key"), patch("gemini.time.sleep"):
        messages = []
        responses = [_mock_response(429, "rate limited"), _ok_response()]
        with patch("gemini.requests.post", side_effect=responses):
            generate_structured(
                "hello", {"type": "OBJECT", "properties": {}}, max_retries=3,
                on_retry=lambda msg: messages.append(msg),
            )
        assert len(messages) == 1
        assert "attempt 1/3" in messages[0]


def test_throttle_sleeps_once_the_rpm_budget_is_used():
    with patch.object(gemini, "GEMINI_RPM_LIMIT", 2), patch("gemini.time.sleep") as mock_sleep:
        gemini._throttle()
        gemini._throttle()  # budget of 2 used up, no sleep yet
        mock_sleep.assert_not_called()
        gemini._throttle()  # third call within the window should wait
        mock_sleep.assert_called_once()


def test_throttle_does_not_sleep_when_under_budget():
    with patch.object(gemini, "GEMINI_RPM_LIMIT", 10), patch("gemini.time.sleep") as mock_sleep:
        for _ in range(5):
            gemini._throttle()
        mock_sleep.assert_not_called()
