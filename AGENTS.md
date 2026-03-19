# tl;dm - AGENTS.md

## Development Guidelines

- Always use uv instead of python or pip
- After making changes, run `uv run ruff format` and `uv run ruff check --fix` to format and lint the code.

## Style Guide

- Do not use f-strings for logging. Use %s instead.
- Add docstrings for public methods with Args, Returns, and Raises sections (if applicable).
- Do not add docstrings for private methods.

## Testing

- Use pure pytest for testing.
- Do not use mockeypatch. Use pytest.fixture and patch from unittest.mock instead.
- Do not use MagicMock. Use unittest.mock.Mock instead.
- Keep tests concise: prefer fewer test cases with maximum coverage over many granular tests.
