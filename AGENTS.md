# Agent routing

- The root session uses the current Sol model (`gpt-5.6-sol`) for planning, architecture, delegation, integration, and final decisions.
- Never create, spawn, delegate to, or configure a Sol subagent, including temporary or dynamically named agents. Sol is reserved for the root orchestrator.
- The default subagent model is `gpt-5.6-luna`, configured under `[agents]` in the user `config.toml`.
- Use the named roles first: `researcher` and `browser_debugger` for bounded read-only investigation, `coder` for implementation and tests, and `reviewer` for read-only diff review.
- Every delegation must pass an explicit named `agent_type` and use fresh context. With Multi-Agent V1, pass `fork_context=false`; with the current agent tool, pass `fork_turns="none"`. Prompts must be self-contained and bounded, with exact write scope and proof required. Keep concurrent write sets disjoint.
- Installed Codex CLI 0.156.1 has no global `fork_turns` config setting; enforce fresh context at dispatch and do not invent a TOML key.
- `browser_debugger` is read-only and uses Chrome DevTools MCP at `http://localhost:3000/mcp` with a 20-second startup timeout.
- Pinned roles: `researcher` and `browser_debugger` use `gpt-5.6-luna`; `coder` uses `gpt-5.6-luna`; `reviewer` uses `gpt-5.6-terra`.
- Dispatch only with an explicit named `agent_type` and fresh context; never use an unspecified, inherited, temporary, dynamic, or `executor_sol` worker. `executor_sol` is a legacy alias and must not be spawned.
- Keep the root lead responsible for decomposition, architecture, synthesis, integration, and final verification; close completed delegated work promptly.

## Repository memory

- Read `repository-state/README.md` and `repository-state/CURRENT.md` before making substantial changes.
- Whenever a bug is found, record the symptom, root cause, fix, and prevention lesson in `repository-state/BUGS.md` so it is not repeated.
- Whenever a feature is added or a large change is made, update `repository-state/CURRENT.md` and add a concise entry to `repository-state/CHANGES.md` in the same change.
- Keep these notes factual and brief. Update existing entries instead of duplicating them.

## Behavior-preserving refactoring

- Refactor architecture before algorithms. An extraction keeps the existing implementation and execution order; do not combine it with translation, layout, rendering, scheduling, or persistence changes.
- Keep UX, UI styling and interactions, API routes/responses, database schema, translation prompts/configuration, and pipeline stage order unchanged during extraction.
- Preserve public imports and method/function signatures through the old module as a compatibility facade.
- Before editing, read the target module, trace its callers and imports, and identify the tests that cover its observable behavior. Add characterization coverage where that behavior is not already protected.
- Extract one responsibility per change. Move code with constants, branch order, candidate ordering, and error handling intact; do not opportunistically rename or simplify logic.
- Run the focused tests for the old entry points and the extracted module, then the affected subsystem contracts. Report behavior/API/schema/order changes explicitly; extraction-only work should report none.
- For layout-only extractions, do not change scoring equations, weights, thresholds, rounding, font metrics, candidate/fallback ordering, or solver iteration. Preserve output comparisons before proposing algorithm work separately.
- Run `python3 devscripts/check_line_limits.py` for the application line-count ratchet. Existing application files may not grow beyond `repository-state/line-count-baseline.json`; new application files are capped at 500 lines. Model architecture, vendored, generated, and test files are excluded. Oversized in-progress extractions are listed with reasons in the baseline and may only shrink.

## Take liberties as you see fit, i trust you so make me proud
