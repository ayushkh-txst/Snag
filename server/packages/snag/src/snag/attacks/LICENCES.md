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
| [garak](https://github.com/NVIDIA/garak) (NVIDIA) | Apache-2.0 | Yes | `instruction_override`, `encoding`, `payload_splitting` |
| [PyRIT](https://github.com/Azure/PyRIT) (Microsoft) | MIT | Yes | `roleplay`, `obfuscation` |
| [HackAPrompt](https://huggingface.co/datasets/hackaprompt/hackaprompt-dataset) | MIT | Yes | `continuation` |
| [TensorTrust](https://tensortrust.ai/) | CC-BY-4.0 | Yes (attribution required for verbatim reuse — not applicable here, no verbatim text reused) | `debug_pretext` |
| [JailbreakBench](https://github.com/JailbreakBench/jailbreakbench) | MIT | Yes | `authority_claim`, `many_shot` |
| [promptfoo](https://github.com/promptfoo/promptfoo) red-team plugins | MIT | Yes | `context_switch` |
| [OWASP LLM Top 10](https://owasp.org/www-project-top-10-for-large-language-model-applications/) | CC-BY-SA-4.0 | Yes (share-alike applies to verbatim reuse of OWASP's own text — not applicable here; used only as a category checklist, per §7.2) | `translation` |
| hand-written (app-specific) | N/A — original work, no external source | Yes | `business_logic_bypass`, `tool_arg_injection`, `auth_confusion`, `refusal_bypass` |

## Excluded

Nothing was excluded — every source above cleared the commercial-use check.
If a future addition to `TECHNIQUES` draws on a source whose licence
forbids commercial use (or is unclear), it must be excluded from this
library rather than adapted "just this once" — see PROJECT.md's cost/BYOK
constraints, which assume this product may be run commercially.

## Verification note

`test_attacks_library.py` greps this file for every `source` value that
appears in `TECHNIQUES`, so an added technique whose source isn't listed
here (with a commercial-use verdict) fails the test — the manifest can't
silently drift out of sync with the data.
