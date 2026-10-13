# Contributing to SelfClaw

Thank you for your interest in contributing to SelfClaw! This document outlines the process for contributing.

## How to Contribute

### Reporting Bugs

- Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md)
- Include: Python version, OS, steps to reproduce, expected vs actual behavior
- Check existing issues first to avoid duplicates

### Suggesting Features

- Use the [feature request template](.github/ISSUE_TEMPLATE/feature_request.md)
- Describe the use case and expected behavior

### Pull Requests

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Make your changes
4. Test locally (`python gateway.py` + `streamlit run DS/main.py`)
5. Commit with a clear message following [Conventional Commits](https://www.conventionalcommits.org/)
6. Push and open a PR using the [PR template](.github/PULL_REQUEST_TEMPLATE.md)

### Development Setup

```bash
git clone https://github.com/Alanzj/SelfClaw.git
cd SelfClaw
pip install -r requirements.txt
cp keys.env.example keys.env  # then fill in your API keys
python gateway.py
# In another terminal:
streamlit run DS/main.py --server.port 8502
```

### Code Style

- Python: follow PEP 8
- Use type hints where practical
- Keep functions focused and documented
- Comments in Chinese or English are both welcome

### Areas We Need Help

- **Model Provider integrations** — adding new LLM providers
- **UI/UX improvements** — Streamlit component enhancements
- **Documentation** — tutorials, deployment guides
- **Testing** — unit tests for gateway routing and fallback logic
- **Self-IDE extension** — VS Code extension features

## Code of Conduct

Please read our [Code of Conduct](CODE_OF_CONDUCT.md) before contributing.
