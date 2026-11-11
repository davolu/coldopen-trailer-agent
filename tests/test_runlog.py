import json

from coldopen.runlog import RunLog, redact


def test_redacts_secrets_but_keeps_token_counts(tmp_path):
    fake = "sk-" + "a" * 30
    assert "[REDACTED]" in redact(f"error with key {fake}")
    out = redact({"api_key": "x", "input_tokens": 123, "nested": [f"Bearer {fake}"]})
    assert out["api_key"] == "[REDACTED]" and out["input_tokens"] == 123
    assert "sk-" not in json.dumps(out)
    log = RunLog(tmp_path / "e.jsonl")
    log.event("x", "ok", note=f"OPENAI_API_KEY={fake}")
    assert fake not in (tmp_path / "e.jsonl").read_text()
