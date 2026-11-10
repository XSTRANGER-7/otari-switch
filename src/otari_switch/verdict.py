"""Turn scores into a decision per call site: switch, switch with fallback, or keep frontier."""

from __future__ import annotations

from otari_switch.models import KEEP, SWITCH, SWITCH_WITH_FALLBACK, CandidateResult, SiteResult

MAX_ERROR_SHARE = 0.2


def too_slow(c: CandidateResult, today_p50: float | None, th: dict) -> bool:
    """Median latency more than `max_latency_ratio` times today's model on the same prompts (0 turns it off)."""
    ratio = float(th.get("max_latency_ratio") or 0)
    return bool(ratio and today_p50 and c.p50_ms and c.p50_ms > ratio * today_p50)


def too_costly(c: CandidateResult, today_cost_1k: float | None, th: dict) -> bool:
    """Costs more than `max_cost_ratio` times today's model on the same prompts (0 turns it off)."""
    ratio = float(th.get("max_cost_ratio") or 0)
    return bool(ratio and today_cost_1k and c.cost_per_1k and c.cost_per_1k > ratio * today_cost_1k)


def classify(c: CandidateResult, n: int, th: dict, today_p50: float | None = None,
             today_cost_1k: float | None = None) -> str:
    """Which verdict this candidate alone would earn."""
    if n == 0 or not c.samples:
        return KEEP
    if too_slow(c, today_p50, th) or too_costly(c, today_cost_1k, th):
        return KEEP
    if c.errors / max(n, 1) > MAX_ERROR_SHARE:
        return KEEP
    if c.tests_passed is False:
        return KEEP
    if c.rejected_params:  # the app sends these on every call, so the model would fail in production
        return KEEP
    if c.check_pass_rate >= 1.0 and c.mean_score >= th["switch_mean"] and c.share_ok >= th["switch_min_share"]:
        return SWITCH
    if c.check_pass_rate >= th["fallback_check_rate"] and c.mean_score >= th["fallback_mean"]:
        return SWITCH_WITH_FALLBACK
    return KEEP


def decide(site: SiteResult, th: dict) -> SiteResult:
    """Pick the cheapest candidate with the best verdict class."""
    by_class = {SWITCH: [], SWITCH_WITH_FALLBACK: []}
    for c in site.candidates:
        v = classify(c, site.recordings, th, site.frontier_p50_ms, site.frontier_cost_per_1k)
        if v in by_class:
            by_class[v].append(c)
    for v in (SWITCH, SWITCH_WITH_FALLBACK):
        pool = by_class[v]
        if pool:
            best = sorted(pool, key=lambda c: (c.cost_per_1k if c.cost_per_1k > 0 else float("inf"),
                                               -c.mean_score))[0]
            site.verdict, site.chosen = v, best.model
            site.reason = _reason(v, best, site)
            return site
    site.verdict, site.chosen = KEEP, None
    site.reason = _keep_reason(site, th)
    return site


def _reason(v: str, c: CandidateResult, site: SiteResult) -> str:
    base = f"{c.model} scored {c.mean_score:.1f}/5 with {round(c.share_ok * 100)}% of answers usable"
    if c.check_pass_rate < 1.0:
        base += f" and {round(c.check_pass_rate * 100)}% passing format checks"
    if site.frontier_cost_per_1k and c.cost_per_1k:
        pct = (1 - c.cost_per_1k / site.frontier_cost_per_1k) * 100
        if pct > 0:
            base += f", {pct:.0f}% cheaper"
        else:
            base += f", {abs(pct):.0f}% more expensive"
    if v == SWITCH_WITH_FALLBACK and "json" in site.site.flags:
        base += ". A runtime guard retries on the frontier model when an answer is not valid JSON."
    elif v == SWITCH_WITH_FALLBACK:
        base += ". Close to the bar, so review the lowest-scoring answers before merging."
    else:
        base += "."
    return base


def _keep_reason(site: SiteResult, th: dict) -> str:
    if not site.candidates or all(not c.samples for c in site.candidates):
        return "No candidate produced answers to judge."
    usable = [c for c in site.candidates if c.errors / max(site.recordings, 1) <= MAX_ERROR_SHARE] or site.candidates
    best = max(usable, key=lambda c: c.mean_score)
    n = max(site.recordings, 1)
    if best.errors / n > MAX_ERROR_SHARE:
        return (f"{best.errors} of {site.recordings} answers from {best.model} could not be scored (request or "
                "judge errors, listed under the model). Fix those and run again before deciding.")
    if too_costly(best, site.frontier_cost_per_1k, th):
        more = (best.cost_per_1k / site.frontier_cost_per_1k - 1) * 100
        return (f"Best candidate {best.model} matched the quality bar but costs {more:.0f}% more than today's model "
                f"({_money(best.cost_per_1k)} vs {_money(site.frontier_cost_per_1k)} per 1k calls). Raise "
                "thresholds.max_cost_ratio to switch anyway for openness.")
    if too_slow(best, site.frontier_p50_ms, th):
        return (f"Best candidate {best.model} answered well but too slowly: median {best.p50_ms / 1000:.1f} s against "
                f"{site.frontier_p50_ms / 1000:.1f} s for today's model (limit {th.get('max_latency_ratio'):g}x).")
    if best.rejected_params:
        return (f"Best-scoring candidate {best.model} rejects {', '.join(best.rejected_params)}, which this call "
                "sends, so it cannot serve it.")
    if best.tests_passed is False:
        return f"Best candidate {best.model} failed the repo's tests."
    if best.check_pass_rate < th["fallback_check_rate"]:
        return (f"Best candidate {best.model} passed format checks only "
                f"{round(best.check_pass_rate * 100)}% of the time.")
    return (f"Best candidate {best.model} scored {best.mean_score:.1f}/5, below the "
            f"{th['fallback_mean']:.1f} bar. The call still moves to Otari on the same frontier model.")


def _money(v: float) -> str:
    return f"${v:,.2f}" if abs(v) >= 1 else f"${v:,.4f}"
