# CLAUDE.md

This file provides guidance for AI assistants working in this repository.

## Repository Overview

**hello_world** is an introductory repository. It is minimal by design and serves as a starting point for development.

- **Owner**: mciner17
- **Remote**: `origin` at `mciner17/hello_world`
- **Default branch**: `master`

## Repository Structure

```
hello_world/
├── README.md       # Project description
└── CLAUDE.md       # This file — AI assistant guidance
```

## Git Workflow

### Branches

- `master` — stable default branch; do not push directly without review
- Feature branches follow the pattern: `claude/<description>-<session-id>`

### Making Changes

1. Always work on a designated feature branch (never push directly to `master` unless explicitly authorized).
2. Commit with clear, descriptive messages summarizing *why* the change was made.
3. Push with tracking: `git push -u origin <branch-name>`
4. If push fails due to a network error, retry up to 4 times with exponential backoff (2s, 4s, 8s, 16s).

### Commit Message Style

- Use imperative mood: "Add feature" not "Added feature"
- Keep the subject line under 72 characters
- Reference issues or context in the body when relevant

Example:
```
Add user authentication module

Implements JWT-based login and session management.
Closes #42
```

## Development Conventions

Since this repository is currently in its early stage, conventions will grow as the project does. When adding code, follow these defaults:

- **Language**: Not yet determined — follow the language chosen when first source files are added
- **Formatting**: Use the standard formatter for whatever language is adopted (e.g., `prettier` for JS/TS, `black` for Python, `gofmt` for Go)
- **Testing**: Add tests alongside any new functionality; prefer co-located test files
- **Dependencies**: Document any new dependencies in the appropriate manifest file (e.g., `package.json`, `requirements.txt`, `go.mod`)

## Key Notes for AI Assistants

- This repo has no build system, test runner, or CI configuration yet. Do not assume any exist.
- Do not create files unless they are directly required for the task.
- Prefer editing existing files over creating new ones.
- Avoid over-engineering: match complexity to the current stage of the project.
- Check `README.md` for high-level project intent before making structural changes.
- When in doubt about scope or approach, ask before acting on assumptions.
