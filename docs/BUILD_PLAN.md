# Build plan: ColdOpen

> Generated from `ARCHITECTURE.md` / `architecture.json` with Agentic Architect. Rule: code must match the architecture. If implementation needs to differ, update the spec and the diagram first, then this plan.

## 0. Ground rules
- [x] The model proposes, the system disposes: authorization, budgets and side effects live in plain, tested code (Ch4, Ch48).
- [x] No secrets in code, prompts, logs or state; `.env.example` documents them; the run log redacts (Ch5).
- [x] Business logic in framework-agnostic functions; the CLI and Streamlit are thin edges over `coldopen.pipeline` (Ch21).
- [x] Every task below has a test or an eval that proves it.

## 1. Baseline (Ch5)
- [x] Repo skeleton, pinned dependency floors, README, `.env.example`, `.gitignore` for media and outputs
- [x] Provider seams: `ClaudeMiner` (one function for model calls, SDK timeout + 2 retries) and `transcribe()` (Whisper)
- [x] Structured logs: `RunLog` JSONL with stage, status, seconds, tokens, cost; secrets redacted (`tests/test_runlog.py`)

## 2. Contracts (Ch8, Ch11)
- [x] `TrailerRequest` edge schema (`coldopen/models.py`)
- [x] `MomentProposal` (model output: sentence IDs + scores) and `Moment` (verified, code-owned times)
- [x] One error shape per tool: `GuardError(stop_reason)` and `ToolError`; named stop reasons in `StopReason`

## 3. Vertical slice: URL → approved trailer (Ch63)
- [x] Edge: CLI `coldopen <source>`; Streamlit `app.py`
- [x] Input guard: host allowlist, file type, size, duration (`guards.check_source`, `check_duration`)
- [x] Orchestrator: staged workflow with checkpoints and stop reasons (`pipeline.prepare/render`)
- [x] Model step: one structured-output Claude call, transcript wrapped as data (`miner.py`)
- [x] Verifier: real sentence IDs, snapping, length, overlap (`guards.verify_proposals`)
- [x] HITL: `moments.md`/`moments.json` + `approve()` with a digest-bound, expiring approval (`review.py`)
- [x] Render gate denies by default (`guards.check_render_gate`)
- [x] Write tool confined to the job directory, argv only (`render.py`, `proc.py`)
- [x] Output check with ffprobe (`render.check_output`)

## 4. Remaining components
| Component (spec) | Task | Test that proves it | Done |
|---|---|---|---|
| Streamlit UI + CLI | upload or URL, settings, progress, review table, players | manual run + screenshots; `coldopen --help` | ✅ |
| Input guard | allowlist + limits | `test_input_guard_*` | ✅ |
| Moment miner | structured output, windowing, schema retry | eval harness (real Claude) | ✅ |
| Tension sequencer | fit length, hook first, cliffhanger last | `test_cut_fits_budget_and_orders_for_tension` | ✅ |
| Moment verifier | reject invented / reversed / overlong / overlapping | `test_verifier_*` | ✅ |
| Budget guard | minutes, calls, tokens, dollars | `test_budget_guard` | ✅ |
| Human review | approve exact list, edits need re-approval | `test_render_gate_denies_by_default`, `test_review_gate_then_render` | ✅ |
| Render gate | missing / mismatched / expired approval denied | `test_render_gate_denies_by_default` | ✅ |
| Ingest | yt-dlp or local file, cached by source hash | real run on a YouTube URL | ✅ |
| Transcriber | Whisper words, punctuation, sentence split | `test_sentence_split_on_punctuation` + real run | ✅ |
| ffmpeg renderer | fades, captions, 16:9 + 9:16, title, music | `test_review_gate_then_render` (synthetic video) | ✅ |
| Output check | streams, size, duration | same test + real run | ✅ |
| Run log + cost ledger | redacted JSONL, ledger from events | `test_redacts_secrets_but_keeps_token_counts` | ✅ |
| Eval script + gate | golden set, PROMOTE/HOLD | `python evals/run_evals.py` | ✅ |

## 5. Safety tests (Ch47, Ch50)
- [x] Prompt-injection fixture (spoken "ignore your instructions" + sponsor read) never reaches the cut: eval `gc-003-injection`
- [x] Unapproved render attempt: `NOT_APPROVED`, no file written (`test_review_gate_then_render`)
- [x] Edited or expired approval: rejected (`test_render_gate_denies_by_default`)
- [x] Invented timestamps / sentence IDs: rejected by the verifier (`test_verifier_rejects_*`, offline eval stub)
- [x] Budget exhaustion produces `BUDGET_EXCEEDED` (`test_budget_guard`)

## 6. Evaluation harness (Ch41-Ch45)
- [x] Golden set with stable case IDs (`evals/golden/gc-00*.json`)
- [x] Deterministic checks on the model's output, safety separate from quality
- [x] Release gate: PROMOTE only with 100% safety and ≥ 80% quality on ≥ 5 cases; report saved to `evals/report.json`

## 7. Operations (Ch51-Ch55)
- [ ] Dockerfile (later)
- [x] Cost and latency in `ledger.json` and `manifest.json` per job
- [ ] Hosted queue + worker for long podcasts (later; spec D12)

## 8. Traceability check
- [x] Every component in `architecture.json` maps to code and a test (table above)
- [x] Every trust boundary has a checker in code and a test
- [x] The HITL point is enforced in code (`check_render_gate`), not just in a prompt
- [x] Spec updated when code changed: verifier max clip length set to 21 s to enforce the prompt's 20 s rule
