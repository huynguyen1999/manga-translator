# Agent routing

- The root session is the Sol lead. It owns planning, architecture, delegation, integration, and final decisions.
- Never spawn or configure a Sol subagent. Sol is exclusively reserved for the root orchestrator.
- The root runtime uses `gpt-5.6-sol`; the default subagent model remains `gpt-5.6-luna`.
- Use the named roles first: `researcher` and `browser_debugger` for bounded read-only investigation, `coder` for implementation and tests, and `reviewer` for read-only diff review.
- Every Multi-Agent V1 delegation must pass `agent_type` explicitly and `fork_context=false`. Prompts must be self-contained, bounded, and include the exact write scope and proof required. Keep concurrent write sets disjoint.
- Codex CLI 0.150.1 does not support a `fork_turns` TOML setting; do not add one. The default subagent model remains `gpt-5.6-luna` in the user config.
- `browser_debugger` is read-only and uses Chrome DevTools MCP at `http://localhost:3000/mcp` with a 20-second startup timeout.
- Pinned roles: `researcher` and `browser_debugger` use `gpt-5.6-luna`; `coder` uses `gpt-5.6-luna`; `reviewer` uses `gpt-5.6-terra`.
- Dispatch only with an explicit `agent_type` and `fork_context=false`; never use an unspecified, inherited, dynamic, or `executor_sol` worker. `executor_sol` is a legacy Luna alias and must not be spawned.
- Keep the root lead responsible for decomposition, architecture, synthesis, integration, and final verification; close completed delegated work promptly.

## Repository memory

- Read `repository-state/README.md` and `repository-state/CURRENT.md` before making substantial changes.
- Whenever a bug is found, record the symptom, root cause, fix, and prevention lesson in `repository-state/BUGS.md` so it is not repeated.
- Whenever a feature is added or a large change is made, update `repository-state/CURRENT.md` and add a concise entry to `repository-state/CHANGES.md` in the same change.
- Keep these notes factual and brief. Update existing entries instead of duplicating them.

## Take liberties as you see fit, i trust you so make me proud