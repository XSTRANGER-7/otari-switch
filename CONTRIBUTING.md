# Contributing

Thanks for helping. Issues and pull requests are welcome.

## Setup

```bash
pip install -e ".[dev]"
pytest
otari-switch demo        # try your change in the app
```

The test suite is offline: it uses `otari_switch.fake_otari`, a fake gateway with Otari's API shape and headers.

## Where things live

| Step | Module |
| --- | --- |
| Find call sites | `scan.py` |
| Record prompts | `record/` |
| Mask private data | `privacy.py` |
| Replay and judge | `evaluate.py`, `judge.py` |
| Decide | `verdict.py` |
| Prove | `testgate.py` |
| Policies, code, PR | `policies.py`, `rewrite.py`, `pr.py` |
| Every Otari call | `otari_client.py` |
| The app | `server.py`, `web/` |

## Rules

- Every Otari call goes through `otari_client.py`.
- Never write to a user's code without a preview; rewritten files must compile.
- User data is inserted into the app as text, never as HTML.
- Keep the core dependency-free beyond PyYAML.
- Good first issues: new scanner patterns (LangChain, LlamaIndex), JavaScript and TypeScript scanning, more deterministic checks.
