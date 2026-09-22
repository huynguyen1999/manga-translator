# Gemini instructions

## Repository memory

- Read `repository-state/README.md` and `repository-state/CURRENT.md` before making substantial changes.
- Whenever a bug is found, record the symptom, root cause, fix, and prevention lesson in `repository-state/BUGS.md` so it is not repeated.
- Whenever a feature is added or a large change is made, update `repository-state/CURRENT.md` and add a concise entry to `repository-state/CHANGES.md` in the same change.
- Keep these notes factual and brief. Update existing entries instead of duplicating them.

## Working rules

- Inspect existing code and callers before changing shared behavior.
- Prefer the smallest root-cause fix and reuse existing project patterns.
- Preserve unrelated working-tree changes.
- Run the narrowest relevant checks before finishing.

## Take liberties as you see fit, i trust you so make me proud