# Developers Wilding submission kit

Deadline on the DoraHacks page: **Nov 12, 2026, 04:29 IST**. Submit by 03:30 for a buffer.

## Status on Nov 10, 2026 (evening)

- Done: real runs on hosted otari.ai (4 of 5 call sites switch, tests passed, $0.23 per evaluation) and on a self-hosted Otari (routing policies created through the API); see "Proof: real runs" in the README.
- Done: app redesign, product site in `site/` (video + real screenshots), silent product tour `site/media/demo.mp4` (also in the app's Welcome screen).
- Done: Otari bug drafts with steps to reproduce in `docs/OTARI_ISSUES.md`; file them from your account.
- Repo: https://github.com/XSTRANGER-7/otari-switch (main, plus the `devs-wilding` branch).
- Still yours: file the issues, and record the demo + feedback videos in your own voice (the rules ask for that; the silent tour is for the site, not the submission video).

## Before you record anything

- [ ] Register on DoraHacks; sign up on otari.ai with the same email; star `mozilla-ai/otari`; join the Mozilla OSAI Discord and take the devs-wilding role.
- [ ] Push this repo to GitHub on a branch named `devs-wilding`. Its first commit is the "first commit where Otari came in" link.
- [ ] In the Otari dashboard: enable code execution (set its price) and web search; copy 3 open-weight model ids and a judge model.
- [ ] Run `otari-switch doctor` in a real repo (or the demo app pointed at real Otari) and fix anything red.
- [ ] Do one real run against hosted Otari so the video shows real request ids: copy the demo app (`otari-switch-demo/`), `pip install openai`, set `OTARI_API_KEY`, then in `otari-switch.yml` set `otari.base_url` back to hosted Otari, `otari.api_key_env: OTARI_API_KEY`, `policies_api: false`, and real candidate and judge ids. Delete `.otari-switch/recordings.jsonl` and record from the tests for real prompts.
- [ ] File every Otari bug you hit as an issue on `mozilla-ai/otari` (steps, request id, environment). The submission asks for these links.

## Demo video script (3 to 5 minutes, your screen and your own voice; no AI voice or editing)

1. **0:00** One sentence: "Teams want open-weight models but fear quality drops; Otari Switch moves each AI call only where it is proven safe."
2. **0:20** `otari-switch ui`. Steps list. Connect Otari: region, key test, model chips.
3. **0:50** Scan: "5 AI calls found", tags for JSON and tools.
4. **1:10** Record from tests; privacy masking toast.
5. **1:30** Choose models and budget cap; start evaluation. Rings fill, verdicts pop, spend meter stays under the cap.
6. **2:30** Report: 4 of 5 switch, savings. Open `extract_fields`: answers side by side, the model that rejected `response_format`, request ids.
7. **3:15** Review changes: diff, policy chains, PR text. Create branch, open the PR (through GitHub MCP in Otari).
8. **3:50** Rollback: reorder a policy in the Otari dashboard; no code change.

## Feedback recording (3 to 5 minutes) prompts

- What I built and which Otari features: routing policies as the output, budgets as a hard cap, code execution for contract checks, MCP for the PR, request ids on every score.
- What worked well.
- What did not: anything that cost time, with steps to reproduce.
- What is missing and what you would change.

## Written feedback (draft from your own experience; these are prompts, not answers)

1. When were you most tempted to skip Otari and call the provider directly? What happened?
2. What did you end up building yourself that Otari should have done for you? (Candidates from this build: per-call-site evaluation, a way to create routing policies from code, quality-based fallback, not only error-based.)
3. If the rules had not required Otari, would you have continued using it? Why or why not?

## Submission form

- Project name: Otari Switch
- Email used on otari.ai
- One line: Moves an app's AI calls to open-weight models through Otari, one call site at a time, with proof and a PR.
- Repo link and branch: `devs-wilding`
- First Otari commit link
- Otari features used: see "How Otari is used" in the README
- Otari issues or PRs opened
- Demo video link
- Feedback video link and written answers
