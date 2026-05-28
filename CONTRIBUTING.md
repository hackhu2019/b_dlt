# Contributing

## Scope

Contributions should stay within the project scope defined in [AGENTS.md](./AGENTS.md).

This repository is intentionally narrow:

- Keep the workflow focused on `manifest -> audio -> subtitle/asr -> summarize -> index`
- Do not add UI, deployment, vector database, or unrelated integrations without discussion

## Rules

1. Do not add features outside the current workflow stages without discussion.
2. Do not commit generated data, logs, databases, cookies, tokens, or local environment files.
3. Update documentation before code when changing structure, workflow, or validation commands.
4. Keep changes minimal and directly traceable to the task.
5. Prefer extending existing scripts over adding parallel abstractions that duplicate behavior.
6. If a change touches external API behavior, compare it against official docs or a working reference.

## Local Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

## Local Validation

Run the agreed checks before opening a pull request:

```bash
make verify
```

If a check cannot run, explain why in the pull request.

## Documentation Expectations

Update `README.md` when you change:

- setup steps
- required dependencies
- auth behavior
- output paths
- validation commands
- the recommended workflow

## Secret Hygiene

Never include any of the following in issues, commits, PRs, screenshots, or logs:

- Bilibili cookies
- raw `Cookie` headers
- OpenAI API keys
- personal browser profile paths tied to private data

Sanitize command output before sharing it publicly.

## Pull Request Notes

A good PR for this repository usually includes:

- one concrete problem
- one minimal fix
- matching docs update when behavior changes
- exact validation commands and results
