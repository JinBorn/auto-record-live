# LLM copywriting engine validation

## Automated Checks

- `python -m compileall src/arl` passed.
- `.\.venv\Scripts\python.exe -m pytest tests/pipeline/test_copywriter_service.py tests/pipeline/test_editing_service.py tests/pipeline/test_postprocess_service.py tests/pipeline/test_postprocess_reset_service.py tests/pipeline/test_status_service.py tests/test_config.py` passed: 116 tests.
- `.\.venv\Scripts\python.exe -m pytest tests` passed: 652 tests.
- `python ./.trellis/scripts/task.py validate 07-06-llm-copywriting-engine` passed.
- `python -m compileall src/arl tests` passed.

## Live Provider Smoke

Completed after the user confirmed the LLM env was configured.

- Settings visible to the process: `ARL_LLM_ENABLED=1`, key present,
  model `deepseek-v4-flash`, base URL `https://api.deepseek.com`.
- Session: `session-20260616122238-2469b78a`, match `1`.
- Command shape: `CopywriterService(load_settings()).run_semantic(...)` scoped to
  that session/match with `force_reprocess=True`.
- Provider usage: `prompt_tokens=5719`, `completion_tokens=762`,
  `total_tokens=6481`.
- Recommended title: `盖伦顶级拉扯，三路高地翻盘`.
- Raw-excerpt guard result: `False`.
- First subtitle cues started with:
  `所以说就是没有必要嘲笑你知道吧...`, so the title is not the leading ASR
  excerpt.
- Cover lines: `三路高地 / 六百层狗头 / 折磨王盖伦`.
- Summary: `盖伦面对大树剑圣，逆风三路高地被破，靠顶级拉扯和运营最终翻盘，狗头六百层也无力回天。`
- Tags: `LOL,盖伦,上单,拉扯,翻盘,运营,高端局,耐心`.
- Teaser recommendations: 3.
