# Attack library — source licences

Per PROJECT.md ("Out of Scope"): no automated dataset sync. This is a
one-time, hand-adapted research pass over public red-teaming projects and
Snag's own app-specific patterns (backend-feasibility.md). Every `Technique`
in `library.py` is a **paraphrase** — none of the `template`/`turns` text
is copied verbatim from any source below. `library.py`'s per-technique
`licence`/`source` fields record provenance for audit; this file is the
commercial-use verdict per source, checked before any of it reached
`TECHNIQUES`.

| Source | Licence (as adapted from) | Commercial use permitted? | Used for |
|---|---|---|---|
| [garak](https://github.com/NVIDIA/garak) (NVIDIA) | Apache-2.0 | Yes | `instruction_override`, `encoding`, `payload_splitting`, `verbatim_extraction` (`sysprompt_extraction`), `policy_puppetry` (`doctor.py`), `template_forgery` (`latentinjection.injection_sep_pairs`), `url_exfiltration` (`xss`/`web_injection`) |
| [PyRIT](https://github.com/microsoft/PyRIT) (Microsoft) | MIT | Yes | `roleplay`, `obfuscation`, `shallow_cipher` (FlipAttack converter) |
| [HackAPrompt](https://huggingface.co/datasets/hackaprompt/hackaprompt-dataset) | MIT | Yes | `continuation` |
| [TensorTrust](https://tensortrust.ai/) | CC-BY-4.0 | Yes (attribution required for verbatim reuse — not applicable here, no verbatim text reused) | `debug_pretext` |
| [JailbreakBench](https://github.com/JailbreakBench/jailbreakbench) | MIT | Yes | `authority_claim`, `many_shot` |
| [promptfoo](https://github.com/promptfoo/promptfoo) red-team plugins | MIT | Yes | `context_switch`, `refusal_suppression` (`jailbreak:composite` design) |
| [OWASP LLM Top 10](https://owasp.org/www-project-top-10-for-large-language-model-applications/) | CC-BY-SA-4.0 | Yes (share-alike applies to verbatim reuse of OWASP's own text — not applicable here; used only as a category checklist, per §7.2) | `translation` (low-resource-language variant) |
| [AgentDojo](https://github.com/ethz-spylab/agentdojo) (ETH Zürich) | MIT | Yes | `indirect_envelope` (`important_instructions`) |
| [agent-threat-rules](https://github.com/Agent-Threat-Rule/agent-threat-rules) | MIT | Yes | `tool_error_injection` (ATPA fabricated-error variant) |
| [RaccoonBench](https://github.com/M0gician/RaccoonBench) | CC-BY-4.0 | Yes (attribution required for verbatim reuse — not applicable here; taxonomy only) | `verbatim_extraction` (continuation / two-step shapes) |
| [EPFL llm-past-tense](https://github.com/tml-epfl/llm-past-tense) | MIT | Yes | `past_tense` |
| [GenAI-Red-Team-Lab](https://github.com/GenAI-Security-Project/GenAI-Red-Team-Lab) | Apache-2.0 | Yes | `context_padding` (long benign-context / NINJA) |
| hand-written (app-specific) | N/A — original work, no external source | Yes | `business_logic_bypass`, `tool_arg_injection`, `auth_confusion`, `refusal_bypass` |

Note on the PyRIT URL: the canonical repository is `microsoft/PyRIT`.
`Azure/PyRIT` (which an earlier revision of this file linked) is an archived
stub; the link above was corrected on the 2026-08 hardening pass.

## Excluded — AVOID list (mechanism may be described, TEXT must never be adapted)

These sources appear in the red-teaming literature and their *mechanisms*
informed the families above, but their **text/content must never be copied
or paraphrased into `TECHNIQUES`** because their licences forbid commercial
reuse, are absent, or are otherwise incompatible with a product that may be
run commercially (PROJECT.md cost/BYOK constraints). Every family listed
above was written from scratch or adapted only from a USE-list source.

| Source | Why it is on AVOID |
|---|---|
| [L1B3RT4S](https://github.com/elder-plinius/L1B3RT4S) | AGPL-3.0 (copyleft, commercially incompatible). Its repo description is itself a live injection payload with invisible characters — do not ingest it at all. |
| [promptmap](https://github.com/utkusen/promptmap) | GPL-3.0. Its DAN content is also in garak under Apache-2.0 — take it from there instead. |
| [prompt-injection-defenses](https://github.com/tldrsec/prompt-injection-defenses) | No licence file — no grant of reuse. |
| H-CoT / ProxyPrompt / MHJ | CC-BY-NC / NC-ND — non-commercial only. |
| x-teaming / FITD repo / jailbreakfunction | No LICENSE file — no grant of reuse. |
| [AgentHarm](https://huggingface.co/datasets/ai-safety-institute/AgentHarm) | MIT **plus a field-of-use clause that propagates on redistribution** — must NOT be logged as plain MIT; not adapted from here. |

If a future addition to `TECHNIQUES` draws on a source whose licence forbids
commercial use (or is unclear), it must be excluded from this library rather
than adapted "just this once".

## Verification note

`test_attacks_library.py` greps this file for every `source` value that
appears in `TECHNIQUES`, so an added technique whose source isn't listed
here (with a commercial-use verdict) fails the test — the manifest can't
silently drift out of sync with the data.
