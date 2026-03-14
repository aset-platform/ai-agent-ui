# Project Tooling Notes

## Python Virtualenv
- Location: `~/.ai-agent-ui/venv`
- Lint tools installed: black, isort, flake8 (installed this session)
- pytest installed this session

## GitHub CLI
- `gh` installed via brew but NOT authenticated
- User needs `gh auth login` before PR creation

## E2E
- Demo creds: admin@demo.com/Admin123!, test@demo.com/Test1234!
- Run: `cd e2e && npx playwright test --ui`
- Output: `/tmp/e2e-test-results`

## Lint Commands
```bash
source ~/.ai-agent-ui/venv/bin/activate
python -m black backend/ auth/ stocks/ scripts/ dashboard/
python -m isort backend/ auth/ stocks/ scripts/ dashboard/ --profile black
python -m flake8 backend/ auth/ stocks/ scripts/ dashboard/
```
