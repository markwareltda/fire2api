# Contribuindo

Obrigado por contribuir com o Fire2API. Use Python 3.11–3.13, crie uma branch curta e mantenha mudanças de schema acompanhadas por uma revisão Alembic revisada.

Antes de abrir um pull request:

```bash
python -m compileall app
pytest --cov
ruff check .
mypy app
alembic check
pip-audit -r requirements.txt
```

Não inclua bancos, `.env`, SQL de clientes, dados reais, chaves ou logs. Testes devem usar schemas e dados sintéticos. Mudanças em autenticação, envelopes HTTP, política SQL ou migrações destrutivas precisam ser descritas explicitamente no PR.
