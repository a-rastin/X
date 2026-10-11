"""Production worker entry point: ``python -m x_insight.reasoning`` (S57, T8).

Polls :func:`x_insight.reasoning.worker.poll_forever` with the adapter
selected from operator environment (configured endpoint or fail-closed).
"""

from x_insight.reasoning.worker import poll_forever

if __name__ == "__main__":
    poll_forever()
