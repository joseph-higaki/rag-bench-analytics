---
name: release
description: Guide the user through cutting a SemVer release of rag-bench-analytics. Use this skill whenever the user mentions shipping, releasing, tagging, publishing, finalizing, or cutting a version (e.g. "release v0.1.0", "let's ship the dashboard", "tag and publish", "create a release"), even if they don't explicitly say "release." Claude does not execute the release — Claude presents each step and the user runs the commands themselves in their terminal.
---

# Release procedure

This skill walks the user through cutting a SemVer-tagged release of rag-bench-analytics. The tag push triggers @.github/workflows/release.yml, which creates the GitHub Release from a release-notes file.

## Execution model

**Claude does not run git, gh, or any commands that modify the repository or remote state.** Claude presents each command in a code block with an explanation of what it does and what the expected outcome is. The user runs the command themselves in their own terminal and reports back. Claude waits for confirmation before proposing the next step.

This applies to every step below. There are no exceptions for "safe" or "obvious" commands. The whole point of this skill is to make a deliberate, observable release process.

## Confirmation gate (always run this first)

Before walking through any steps, ask the user:

> I'm ready to walk you through a release. Confirm:
> - Version to release: `<version>` (e.g. `v0.1.0`)
> - Currently on `main` with a clean working tree?
> - CI green on the commit you're releasing (`make pipeline` proves it offline; CI proves it on fixtures)?
>
> Reply with "yes" and the version number to proceed, or describe what's not ready.

Do not begin presenting steps until the user explicitly confirms.

## Choosing the version

This repo's public contract is the **marts schema** — the dashboard and any downstream consumer read it via the read-only `marts_reader` role. Version bumps are anchored on that contract, not on eval results (those belong to the upstream benchmark).

- **MAJOR** — breaks the marts contract or the star grain. Dropped/renamed/retyped mart column, changed fact grain, removed dim — anything that makes an existing consumer query fail or silently change meaning.
- **MINOR** — additive, contract-preserving. New dim/fact column, new model, new dashboard view, new seed/lookup, new source landed.
- **PATCH** — bug fix that corrects values without changing the schema. Transform logic fix, wrong join, mislabeled axis, pricing-seed correction.

The first release of this repo is `v0.1.0` — it is intentionally pre-1.0: the marts contract is still allowed to shift. Reserve `v1.0.0` for when you're ready to promise that contract downstream.

## Steps

For each step:

1. Explain what the step does and why.
2. Show the command(s) in a code block.
3. Tell the user what to expect when they run it.
4. Wait for them to report the outcome before proceeding.

### Step 1 — Write the release notes file

Explain: the release notes file is the most important artifact of the release. The GitHub Action that creates the Release reads this file as the Release body. It also fails the workflow if the file doesn't exist at the tagged commit, which is the safety mechanism preventing accidental releases.

Ask the user to create `.github/release-notes/<version>.md` by copying `.github/release-notes/TEMPLATE.md` and filling it in. The template is a **component changelog** grouped by this repo's layers — fill the sections that changed, delete the ones that didn't:

```markdown
# <version> — <descriptive title>

One-paragraph summary of what shipped in this release.

## Ingestion
- EL changes: new sources landed, raw.* shape, idempotency/keying changes.

## dbt models & marts
- Staging / intermediate / marts changes. Call out CONTRACT changes explicitly
  (added/dropped/retyped mart columns, grain changes) — these drive the version bump.

## Dashboard
- New views, metric/label changes, anything a viewer would notice.

## Infra
- docker-compose, CI, Terraform, env/contract changes for self-hosting.

## Reproducing locally
\`\`\`bash
git clone https://github.com/joseph-higaki/rag-bench-analytics
cd rag-bench-analytics
git checkout <version>
make pipeline   # docker compose up + seed fixtures + ingest + dbt build, fully offline
make dashboard  # v1 on :8501, v2 on :8502
\`\`\`

## Data coverage at this release (optional)
What the marts actually contain at this tag — generators present, retriever
conditions, harness/question-type coverage; null where a mechanism didn't produce it.
```

When the user confirms the notes file exists and has real content, present these commands for them to run:

```bash
git add .github/release-notes/<version>.md
git commit -m "Release notes for <version>"
git push origin main
```

Expected outcome: commit lands on `main` and pushes to origin without errors.

Wait for confirmation before continuing.

### Step 2 — Create and push the tag

Explain: the tag is the bookmark that pins the release. Pushing it triggers the GitHub Action.

Important: the tag must be created after the release notes file is committed. The Action verifies the notes file exists at the tagged commit.

Present these commands for the user to run:

```bash
git tag -a <version> -m "<descriptive title for the release>"
git push origin <version>
```

Expected outcome: `git tag` creates the tag locally; `git push origin <version>` sends it to GitHub and triggers the workflow.

Wait for confirmation before continuing.

### Step 3 — Verify the workflow ran successfully

Explain: the GitHub Action should complete within ~30 seconds, creating a Release page attached to the tag.

Present this command:

```bash
gh run watch
```

Or, if the user prefers the browser:

```bash
gh run list --limit 1
```

Then explain how to interpret the result.

If the workflow succeeded: proceed to Step 4.

If the workflow failed with "Missing release notes file": the notes file wasn't committed before tagging. Walk the user through rollback:

```bash
git tag -d <version>
git push --delete origin <version>
```

Then return to Step 1.

If the workflow failed with "Resource not accessible by integration": the repo's Actions token lacks write access. The workflow already requests `permissions: contents: write`, so this only happens when the repo-wide default is locked down. Walk the user through: GitHub → Settings → Actions → General → Workflow permissions → set to "Read and write permissions" → Save. Then retry from Step 2 (no rollback needed; the tag is still valid).

### Step 4 — Verify the Release exists

Present this command:

```bash
gh release view <version> --web
```

Expected outcome: a browser tab opens showing the Release page with the notes attached and a "Latest release" banner if this is the newest tag.

If the page looks correct, the release is done.

## After this release

If this is the first successful release of the repository, note to the user:

> The release workflow has now been proven end-to-end. If you find yourself wanting to streamline the process, this skill could be converted into a slash command at `.claude/commands/release.md` — automating the steps that proved safe and boring while keeping confirmation gates on the destructive ones (tag push, release creation).
>
> That work is optional and best done after a few more releases, once the workflow's failure modes are well understood.

## Scope

- Does not write changelog content. The user authors that based on what actually shipped (the marts/dashboard/ingestion changes in the release).
- Does not run the pipeline or CI. Those are separate workflows; the user confirms they're green before releasing.
- Does not execute git, gh, or any state-modifying commands. Presents them for the user.
- Does not handle post-release promotion or distribution. The release notes file and the Release page are the deliverables; what happens with them outside the repo is out of scope.
