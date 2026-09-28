---
name: worktree-commit-back
description: Bring completed agent worktree changes onto the branch the session started from and commit them there, preserving unrelated work.
---

# Commit completed worktree changes back

Use when a user wants work from a Codex or agent worktree committed to the branch it was created from.

Identify the source worktree and the original target branch from the session context before changing Git state. Confirm the agent session has finished. Inspect the source branch commits since its fork point, plus its working-tree changes and untracked files; include only changes from that session.

If the source work is not committed, commit only its intended files on the source branch so it can be transferred reliably. Then apply those session commits to the original branch, preferably with cherry-pick. Make the resulting commit on the original branch. Keep the source branch intact unless the user asks to remove it.

Use the existing checkout of the original branch when available. Preserve unrelated tracked and untracked changes there: do not reset, clean, stash, or stage broad path sets. If applying the work would overwrite local edits, or the target branch/session cannot be identified confidently, stop before mutating and report what needs resolving. Resolve straightforward conflicts while retaining both sides; stop with the conflict state intact when intent is unclear.

Do not push or publish unless the user separately asks. After committing, verify the target branch, commit hash, and working-tree status, and report any source commits included.
