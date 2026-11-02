"""Safety boundary for untrusted historical attempt classes."""

from unittest.mock import MagicMock
from uuid import uuid4

from x_insight import contracts
from x_insight.operations.status import _run_failure_alert


def test_unrecognized_attempt_class_is_not_returned(monkeypatch):
    monkeypatch.delenv("X_INSIGHT_RUN_FAILURE_ALERT_COUNT", raising=False)
    monkeypatch.delenv("X_INSIGHT_RUN_FAILURE_ALERT_WINDOW_MINUTES", raising=False)
    session = MagicMock()
    success, count, details, attempt = [MagicMock() for _ in range(4)]
    success.first.return_value = None
    count.scalar_one.return_value = 3
    details.scalar_one.return_value = {"job_id": str(uuid4()), "attempt_index": 1}
    attempt.scalars.return_value.first.return_value = "unsafe provider text"
    session.execute.side_effect = [success, count, details, attempt]

    alert = _run_failure_alert(session, contracts.utcnow())

    assert alert is not None
    assert alert["last_failure_class"] == "unknown"
    assert "unsafe provider text" not in str(alert)
