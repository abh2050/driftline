# Driftline: brag plan

**What it is:** a multi-agent system on Azure that notices when a RAG application quietly gets worse, finds the cause, proves a fix in a sandbox and opens the pull request, but never merges it.
**Who it's for:** engineers who run RAG or LLM applications in production.
**What sets it apart:** every step is a scored decision with an audit trail. The fix is proven *before* the PR exists. 55 of 55 attacks on it were blocked.
**Most impressive claim (measured):** 21/21 injected faults detected, 0 false alarms in 48 h, the right action every time, about $0.07 per incident.
**Visual hook:** a quality metric falling off a cliff with no error anywhere: `hit_count 7.9 → 2.0`, `top_k: 8 → 2`.
**Real UI to show:** the live incident feed, the incident page (routing scores, root cause, sandbox evaluation), the "who can write" chain.
**Tone:** polished, meaning serious, restrained, long holds and soft fades. Visual identity from the console: white, near-black type (#0a0a0a), blue accent #2563eb, Geist.
**Share caption:** "Driftline notices when your RAG quietly gets worse, finds out why, proves the fix and opens the PR. It never merges."

## Angle

**Silent failure, loud evidence.** RAG doesn't crash; it drifts. The video opens on the silence (a metric collapsing with no alert), then shows Driftline answering with evidence: scores, a proven fix, a PR waiting for a human.

## Storyboard (20.5 s, 1920×1080, 30 fps)

| # | Time | Scene | On screen | Motion | Sound |
|---|---|---|---|---|---|
| 1 | 0.0–3.6 | **Hook** | "Your RAG got worse." Below it, a line chart: hit_count drops from 7.9 to 2.0. A small mono caption: `top_k: 8 → 2 · no error · no alert` | Headline settles; the line draws left to right and falls; the caption types in | Low pad; a soft low thump as the line drops |
| 2 | 3.6–7.2 | **Reveal** | "Driftline noticed." plus the real incident feed screenshot, the top row a sev1 `hit_count` incident | The screenshot rises in with a soft shadow; the top row gets a blue highlight | Chord opens; a soft chime on "noticed" |
| 3 | 7.2–11.0 | **Highlight 1: routing** | "Routed to the right specialist." plus the real incident page cropped to the D2/D3/D4 charts | Slow push-in on the D3 chart (chose retrieval · margin 0.86) | Chime |
| 4 | 11.0–14.6 | **Highlight 2: proof** | "Proves the fix before the PR exists." plus the sandbox evaluation table, the "gate passed" line and D5 accept | Pan down from the table to D5; "gate passed" glows blue | Chime |
| 5 | 14.6–17.6 | **Highlight 3: safety** | "Opens the PR. Never merges." plus the chain from shields to D6 to APIM to human review, and "55 / 55 attacks blocked" | Four steps light up left to right | Four soft ticks in key |
| 6 | 17.6–20.5 | **Outro** | "Driftline" · "Detect → Route → Prove → Propose" · "21/21 faults caught · ~$0.07 per incident" · github.com/abh2050/driftline | Fade up, then hold | Resolve on the tonic; tail out |

## Music cue guidance

About 96 BPM in C major (Cmaj9, Am9, Fmaj9, G6), a soft pad plus a sub, and a muted pulse from scene 2 onward. Effects (thump, chimes, ticks) are tuned to the chord tones and sit 8–12 dB under the music. It fades out over the last 1.5 s.
