# Running llmcode in GitHub Actions

The repo ships a composite action (`.github/llmcode-action.yml`) plus
three template workflows under `.github/templates/`. Together they
let you wire llmcode into CI for PR reviews, issue triage, or any
custom prompt the workflow hands it.

## Prerequisites

1. **GitHub Actions secret.** The workflow needs an API key for
   whichever model you call. Add it under
   `Settings → Secrets and variables → Actions` with the same name
   you reference in your workflow (`LLMCODE_API_KEY` is the default
   in the templates).
2. **Pin a version (optional).** The action defaults to the latest
   stable `llmcode-cli` from PyPI. Set the `llmcode_version` input to
   pin a known-good release (e.g. `2.6.0`).
3. **PR / issue write permissions.** When the workflow posts comments
   back to the PR or issue, the job needs `pull-requests: write` or
   `issues: write` set in `permissions`.

## Quick start: PR review

Copy `.github/templates/pr-review.yml` into `.github/workflows/`:

```bash
cp .github/templates/pr-review.yml .github/workflows/llmcode-pr.yml
```

The workflow triggers on `pull_request` and posts a 600-word review
comment using the `glm-5.1` model. To change models, edit the `model:`
input.

## Quick start: issue triage

```bash
cp .github/templates/issue-triage.yml .github/workflows/llmcode-triage.yml
```

The triage workflow asks llmcode to classify each new issue as
`bug`/`feature`/`question`/`docs`/`duplicate` and add the label
list. It uses the structured JSON output schema (see below) so the
post-step can parse the model's response without regex.

## Quick start: custom prompt

```bash
cp .github/templates/custom.yml .github/workflows/llmcode-custom.yml
```

Trigger it manually via `Actions → llmcode-custom → Run workflow`
and supply a prompt. Useful for scheduled audits, doc generation,
release notes, etc.

## Output schema

The action's `result` output is the JSON object from
`llmcode --headless`. The schema:

```json
{
  "output": "string",
  "tool_calls": [{"name": "string", "id": "string"}],
  "tokens": {"input": 0, "output": 0},
  "exit_code": 0,
  "error": null
}
```

`exit_code` values:

| code | meaning             | recommended action                         |
|------|---------------------|--------------------------------------------|
| 0    | success             | use `output` directly                      |
| 1    | tool error          | log failure; re-run if transient           |
| 2    | model/provider err  | check model availability + token quota     |
| 3    | auth error          | rotate the API key in GitHub secrets       |
| 4    | user cancel         | pre-flight cancellation; nothing to retry  |

The action also exposes `exit_code` as a separate output so workflow
steps can branch on the value without parsing JSON twice.

## Secrets & safety

* The composite step exports `auth_secret` via env var, never via
  argv, so the key never appears in command logs.
* Use `auth_env_var` to override the env-var name when the model
  expects something other than `LLM_API_KEY` (e.g.
  `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`).
* Pin the action to a specific tag in production
  (`uses: yourorg/llmcode-cli/.github/llmcode-action.yml@v2.6.0`)
  so a malicious commit to `main` can't upgrade your CI.
* Cache `~/.cache/pip` between runs to shave the ~30s pipx install:

  ```yaml
  - uses: actions/cache@v4
    with:
      path: ~/.cache/pip
      key: pip-${{ runner.os }}-llmcode
  ```

## Local testing with `act`

You can dry-run the templates with [`act`](https://github.com/nektos/act)
before pushing:

```bash
act -W .github/templates/custom.yml -e .github/templates/event.json \
    -s LLMCODE_API_KEY=fake-key
```

`act` doesn't run the actual model call; it confirms the workflow
shape is valid YAML and the action surface composes cleanly.
