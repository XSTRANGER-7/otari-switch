# Otari Switch: demo video and voice-over script

The video is [`site/media/demo.mp4`](../site/media/demo.mp4) (3:31, 1440×900, no sound). The hackathon asks for the demo in **your own voice** (no AI voice), so read this script over it: record your screen playing the video with your mic on, or add your voice track in any editor. Times match the video. Say it in your own words; the lines are a guide, about 2.5 words per second.

Everything after 1:44 is the **real run on hosted otari.ai** (Nov 10, 2026). The live evaluation scene (1:14 to 1:44) uses the local demo gateway, so it is fast and free; say so when it plays, the video also shows a "Demo gateway" badge.

---

## Part 1: the problem and the idea (0:00 to 0:32)

**0:00 Title card**
> "Hi, I'm Vikram. This is Otari Switch. It moves your app's AI calls from frontier APIs to open-weight models through Otari, Mozilla.ai's gateway, with proof."

**0:06 The problem**
> "Teams want open-weight models: they're cheaper and there's no lock-in. But they're scared. Will the JSON still parse? Will the agent call the wrong tool? So they never switch, and they keep calling OpenAI directly. That's exactly the question Otari's own feedback form asks."

**0:15 What's new**
> "Almost every entry I saw builds a new app on Otari. Otari Switch is different: it's the road that brings apps that already exist onto Otari. You get evidence per call site, Otari routing policies as the output, and one pull request you can review."

**0:23 How it works**
> "Seven steps, one command: scan, record, privacy, replay and judge, test gate, verdict, and finally the policy and the PR."

## Part 2: the app, step by step (0:32 to 1:44)

**0:32 The app**
> "This is the app. It runs on your laptop with `otari-switch ui`. Your Otari key is never written to disk."

**0:41 Step 0, Connect**
> "I connect my otari.ai key. Otari Switch reads the real catalog, 165 models, filters the open-weight ones and shows price, JSON support and tool support. It even suggests candidates that fit what this repo needs."

**0:49 Step 1, Scan**
> "Scan finds every OpenAI and Anthropic call in the repo. It only parses the code, it never runs it. Five AI features here, and flags show which ones need JSON or tools."

**0:57 Step 2, Record**
> "Record runs my own test suite once, with every call going through Otari on today's model. That gives real prompts, not made-up ones. Emails, keys and phone numbers are masked before anything is replayed."

**1:06 Step 3, Models and budget**
> "I pick three open-weight candidates and a judge. And there's a hard budget: the run can never spend more than the cap."

**1:14 Step 4, Replay and judge (demo gateway scene)**
> "Now the evaluation, shown here on the local demo gateway so it's quick. Each recorded prompt goes to every candidate through Otari. Exact checks first, valid JSON, the right tool, then a judge model scores each answer from one to five against today's answer. Verdicts pop in per call site."

**1:24 Step 5, Test gate**
> "Then the test gate: my repo's own tests run again on the chosen models, and JSON contract checks run in Otari's code sandbox. If tests fail, a baseline run tells me whether the switch broke it or it was already broken."

## Part 3: the real run on otari.ai (1:44 to 3:01)

**1:44 Real run card**
> "Now the real thing: hosted otari.ai, my key, real models, real money."

**1:53 Step 6, Verdicts**
> "Four out of five call sites can move to open-weight models. That's 80 percent, about 86 dollars a month saved at 200,000 calls, and the whole evaluation cost 23 cents."

**2:02 The report**
> "Per call site I see quality, format checks, cost and latency against today's model. And it's honest: `choose_action` stays on today's model, because the best open model only got 3.1 out of 5 at picking the right tool."

**2:10 Proof for every answer**
> "Every answer is traceable: side by side with today's answer, the judge's reason, the Otari request id, the routing policy chain, and the sandbox check that passed 100 percent."

**2:21 Trend**
> "Spend comes straight from Otari's own usage API. Every request is tagged by role, so I can see what the judge cost, what the candidates cost and what the tests cost."

**2:30 Step 7, the pull request**
> "Now the output. One commit points the clients at Otari, then one commit per call site, so a reviewer can drop any one of them. Model names become routing-policy names."

**2:37 Routing policies**
> "Each call site gets an Otari routing policy: the open model first, today's model kept as the fallback. Rolling back needs no code change, you just reorder the policy in the Otari dashboard."

**2:43 PR text**
> "The PR text is ready. It can be opened through GitHub's MCP server stored in Otari, so the GitHub token stays inside Otari."

**2:49 Branch created**
> "One click, and the branch exists, with seven commits and exact steps for the Otari dashboard."

**2:55 Settings**
> "All the rules are settings: quality bars, latency and cost guards, privacy mode and the test command."

## Part 4: results, features and close (3:01 to 3:31)

**3:01 Results**
> "So, on a real run: 80 percent of the AI calls moved to open models, tests passed, and the evaluation cost 23 cents. I also ran it on a self-hosted Otari, where it created the routing policies through Otari's API."

**3:11 Otari features**
> "Otari is the engine here, not a logo: routing policies, budgets, usage tags, the code sandbox, guardrails, MCP, request ids and the catalog all do real work."

**3:19 Outro**
> "Everyone built a new app on Otari. Otari Switch brings your existing apps onto it. It's open source: clone it, run `otari-switch demo`, no key needed. Thank you!"

---

## Showing the newer features live (for the rubric)

The rubric only counts a feature that the app actually uses, so show each one running, in your own voice:

1. **Web fetch (licence check):** Connect or Models → *Verify licences*, or `otari-switch licenses`. Say: "Otari's catalog has no licence field, so Otari Switch reads each model card through Otari's web fetch: this one is Apache-2.0, this one is gated."
2. **Per-user budgets (team mode):** Settings → Team mode → teammate `alice` (needs a service key). Start an evaluation and point at "Cap lowered … to fit alice's Otari budget", then Trend → *Spend by teammate*.
3. **Guardrails:** Settings → Guardrail screen → pick the profile. After a run, show "Guardrail: N checked, M blocked" in Evaluate and the Report.
4. **MCP:** Settings → pick the GitHub MCP server stored in Otari, then Changes → *Open pull request*; the result says "Opened through GitHub's MCP server in Otari" with the PR link.
5. **Canary routing:** Changes → Policies → *Canary 25%*: the policy becomes a weighted split, created through Otari's routing API.

## Short version (if you need 60 seconds)

> "Teams want open-weight models but fear quality drops, so they keep calling OpenAI directly. Otari Switch fixes that for apps that already exist. It finds every AI call in your repo, records real prompts through Otari, replays them on open models, judges the answers and runs your own tests. Then it writes an Otari routing policy per call site, with today's model as the fallback, and opens one pull request. On a real otari.ai run, 4 of 5 calls moved to open models, tests passed, and the evaluation cost 23 cents. Everyone built a new app on Otari; this one brings your apps onto it."

## Talking points for questions

- **USP:** the only entry we found that migrates existing apps onto Otari, instead of building a new app on it.
- **Proof, not guesses:** real prompts, an LLM judge plus exact checks, and the repo's own tests through Otari.
- **Safe by design:** a hard spend cap, privacy masking, nothing written without a preview, a new branch every time, and rollback in the dashboard.
- **Honest:** "Keep frontier" is a normal result, with the reason. Slower or pricier models are never chosen by default.
- **Real:** tested on hosted otari.ai and on a self-hosted Otari; 8 Otari bugs and doc gaps written up in `docs/OTARI_ISSUES.md`.
- **Everywhere:** CLI, local app, GitHub Action and MCP server (coding agents can run it too).
