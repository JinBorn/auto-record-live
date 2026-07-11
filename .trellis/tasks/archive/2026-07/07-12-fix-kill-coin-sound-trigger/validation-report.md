# Validation Report

- Real regression cue `kills=2->3 deaths=0->1 previous_at=560.000 current_at=594.000` produces no `_KdaKillEvent`.
- `tests/pipeline/test_editing_service.py`: 55 passed.
- Full Python test suite: 724 passed.
- `python -m compileall -q src tests`: passed.
- `git diff --check`: passed.
- No repository lint or type-check tool is configured in `pyproject.toml`.
