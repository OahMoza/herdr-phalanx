# Issue Tracker: GitHub

Issues and specs for this repository live in GitHub Issues. Use the `gh` CLI from this clone.

## Conventions

- Create an issue with `gh issue create --title "..." --body "..."`.
- Read an issue with `gh issue view <number> --comments`.
- Apply a label with `gh issue edit <number> --add-label "..."`.
- Close an issue with `gh issue close <number>`.
- Infer the repository from `git remote -v`; `gh` resolves it automatically in this clone.

## Pull Requests

Pull requests are not a request or triage surface for this repository.

## Wayfinding

- A Wayfinder map is a GitHub issue labelled `wayfinder:map`.
- Its decision tickets are GitHub sub-issues.
- Use native GitHub issue dependencies for blocking when available.
- Claim a ticket by assigning it to the active developer before work starts.
