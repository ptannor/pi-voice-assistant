# Workspace Rules & Preferences

These rules and preferences are migrated from Claude's project memories.

## Kids Collaborate in Hebrew
- The user's children are also collaborators on the coding (especially on this project) and interact in **Hebrew**. One son is Hillel (GitHub account `htannor`).
- **Language Heuristic**: Messages in English are probably from the father (an experienced developer); messages in Hebrew are probably from the kids (less experienced).
- Respond in Hebrew when addressed in Hebrew. Use clear, simple, and encouraging explanations with more hand-holding.
- Respond in English when addressed in English. Keep it concise and technical.
- Explain the "why" in plain terms, avoid unexplained jargon, and prefer small verifiable steps.
- Keep code, filenames, terminal/git commands, and commit messages in English, but explain them in Hebrew if the conversation is in Hebrew.
- Confirm before executing any irreversible or destructive actions.

## GitHub Identity (htannor)
- Contributions to `ptannor/pi-voice-assistant` must be done as the **htannor** GitHub account (Hillel).
- Dedicated SSH key: `~/.ssh/id_ed25519_htannor`
- SSH host alias `github.com-htannor` is set in `~/.ssh/config` (clones/remotes use `git@github.com-htannor:<owner>/<repo>.git`).
- Git identity: global default is set to htannor (`user.name=htannor`, `user.email=301408870+htannor@users.noreply.github.com`).
- The `gh` CLI has `htannor` logged in as the active account. Use `gh pr create --repo ptannor/pi-voice-assistant --base main --head htannor:<branch>` to open PRs.

## Commits & PR Workflow
- Whenever changes are pushed to GitHub (including when the user says "commit and push", "push", or similar), always ensure a Pull Request is opened or updated on GitHub for the branch. If no PR exists yet, proactively create one using: `gh pr create --repo ptannor/pi-voice-assistant --base main --head htannor:<branch>`
- When new commits appear in the repository (e.g. from git status, git log, git fetch), proactively offer the rebase skill to update onto the latest base and tidy history. Do not rebase automatically; just surface the option.
- Whenever a PR is merged, delete its branch locally (`git branch -D <branch>`) and on the remote (`git push origin --delete <branch>`). This deletion is pre-authorized.
