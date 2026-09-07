# Contributing

Contributions that improve correctness, testing, documentation, or usability are
welcome.

## Local checks

```bash
python -m pip install -e .
python -m compileall -q src tests
python -m unittest discover -s tests -v
python -m market_signal_lab demo
```

Keep changes focused and include tests for calculation or behavior changes. Do
not commit downloaded market-data archives, generated reports, local development
settings, or credentials.
