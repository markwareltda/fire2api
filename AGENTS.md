# AGENTS.md - Fire2API

## Objetivo

Fire2API é um backend open source FastAPI + NiceGUI que transforma comandos Firebird parametrizados em APIs HTTP autenticadas. A distribuição oficial é código-fonte e Docker Compose.

## Arquitetura

- `app/main.py`: aplicação, middlewares, health/readiness, startup e montagem da UI.
- `app/admin`: Admin API oculta do OpenAPI.
- `app/core`: metastore, migrations, autenticação, loader dinâmico, política SQL, execução, idempotência e auditoria.
- `app/ui.py`: painel NiceGUI integrado ao mesmo processo FastAPI.
- `migrations`: schema inicial da 0.0.1 e revisões Alembic futuras.
- `tests`: schemas e dados exclusivamente sintéticos.

Uma instância usa uma única conexão Firebird. SQLite é apenas o metastore de configuração e controle.

## Invariantes de compatibilidade

1. Preserve `/api/base/admin/*`, `/api/<route_path>`, `/health`, `/ready`, `/docs` e `/openapi.json`.
2. `/` serve o painel NiceGUI; `/admin` redireciona para `/`.
3. Admin permanece fora do OpenAPI; rotas dinâmicas e endpoints públicos permanecem visíveis.
4. GET mantém `data` como lista e `meta.count/execution_id`.
5. Escritas mantêm `data.rows`, `data.affected_rows` e `meta.execution_id`.
6. Rotas podem compartilhar caminho quando os métodos são diferentes; a unicidade é `(route_path, method)`.
7. Reload remove somente rotas dinâmicas antigas, preserva `/api/base/*` e registra a UI por último.

## Autenticação

- `Authorization: Bearer <ADMIN_API_KEY>` protege Admin API.
- Consumidores usam Access Keys do metastore; armazene somente SHA-256 e prefixo.
- Chaves manuais precisam de pelo menos 32 caracteres; a chave completa aparece somente na criação.
- A sessão NiceGUI guarda apenas marcador assinado HttpOnly/SameSite, nunca a chave admin.
- `ADMIN_API_KEY` é a única chave estática. Não reintroduza `ROUTES_API_KEY`, licença, ativação ou autenticação remota.

## SQL, métodos e parâmetros

- GET: `SELECT` ou CTE terminada em `SELECT`.
- POST: `INSERT` ou `EXECUTE PROCEDURE`.
- PUT/PATCH: `UPDATE`, `MERGE`, `UPDATE OR INSERT` ou procedure.
- DELETE: `DELETE` ou procedure.
- Bloqueie DDL, múltiplas instruções, controle transacional e `EXECUTE BLOCK`.
- Preserve o SQL exatamente como cadastrado; não aplique `upper()` nem reformatação destrutiva.
- Valores usam binds do driver. Nunca interpole entrada livre no SQL.
- `LIMIT`, `OFFSET` e `ORDER_BY` exigem validação Firebird-aware estrita.
- Parâmetros aceitos: `string`, `integer`, `float`, `boolean`, `date` e `datetime`.
- Origens aceitas: `path`, `query` e `body`; body é objeto JSON plano e rejeita extras.

O detector do editor considera `{NOME}` no caminho e `:NOME` no SQL, ignorando comentários, literais, nomes internos e extras reservados. Nomes são armazenados e documentados em maiúsculas; entrada HTTP, placeholders e binds são comparados sem diferenciar maiúsculas/minúsculas. `{id}`, `{ID}`, `:id` e `:ID` compartilham uma única definição `ID`. A sincronização visual é aditiva: cria rascunhos ausentes, força placeholders como path obrigatório, preserva configurações existentes e nunca remove parâmetros automaticamente.

O editor de rotas mantém configuração e parâmetros no mesmo modal. Todas as linhas permanecem apenas em memória até `Salvar rota`; nesse ponto query e snapshot completo de parâmetros são validados e gravados em uma única transação SQLite. IDs existentes devem ser preservados, linhas removidas pelo usuário são excluídas fisicamente e qualquer falha exige rollback integral. O loader deve recusar rota ativa com placeholder ou bind sem definição.

## Transações e idempotência

- Toda leitura e escrita usa transação explícita.
- Escrita só faz commit após execução e verificação de cancelamento.
- Erro, teste administrativo ou cancelamento sempre faz rollback.
- `Idempotency-Key` é opcional em escritas e dura 24 horas. Armazene somente hashes SHA-256 da chave, payload normalizado e resposta, mais estado e execution ID; nunca armazene o conteúdo.
- Chave já processada, payload diferente, concorrência ou resultado indisponível retorna 409 e nunca autoriza replay automático.

## Metastore e migrations

- Todas as conexões SQLite devem usar foreign keys, WAL e `busy_timeout`.
- Exclusões de query, parâmetro e Access Key são físicas; FKs fazem cascata.
- Execute `alembic upgrade head` antes de carregar serviços, rotas ou UI.
- Migrations são idempotentes e recusam schema desconhecido com mensagem clara. A 0.0.1 começa somente em metastore vazio e não oferece upgrade de versões pré-beta.
- Autogere revisions somente no desenvolvimento, revise o arquivo e versione-o.
- Nunca autogere migration no startup nem crie alteração destrutiva implícita.
- Mudança em modelo exige revisão Alembic e `alembic check` limpo.

## NiceGUI e identidade visual

- Use dark mode nativo e a paleta declarada em `app/ui.py`.
- Inputs, labels, placeholders, menus, tabelas e diálogos precisam ter contraste legível.
- Use CodeMirror do NiceGUI para SQL; não adicione Node, npm ou frontend separado.
- Criação/edição de rota e parâmetros pertencem ao mesmo fluxo visual.
- Testes de rota usam campos tipados, nunca entrada JSON manual, e exibem rollback explicitamente.
- Preserve layout responsivo e fonte Inter vendorizada.

## Respostas, logs e segurança

- Prefira `success_response`, `error_response` e `error_json_response`.
- Não registre SQL, parâmetros, bodies, respostas, chaves ou segredos.
- Auditoria administrativa registra ação, recurso, resultado e request ID sem conteúdo sensível.
- Preserve limite de body, CORS por allowlist, headers de segurança e request ID.
- A conta Firebird deve seguir menor privilégio; validação SQL não substitui permissões.
- `.env.example` é o inventário canônico. Toda nova variável exige atualização correspondente.
- Nunca versione `.env`, bancos, WAL/SHM, logs, caches, dados reais ou chaves.

## Dependências e documentação

- `requirements.txt` fixa o ambiente completo de desenvolvimento; `requirements-runtime.txt` fixa somente o runtime instalado na imagem.
- README é a documentação central de apresentação, instalação, API, operação e desenvolvimento.
- Atualize README, `.env.example`, changelog e testes quando alterar comportamento público.

## Controles preventivos de release

- O build Docker parte de checkout limpo e deve excluir `data/`, bancos aninhados, WAL/SHM, logs e qualquer estado runtime em todas as camadas. `/app/data` precisa estar vazio na imagem publicada e um volume novo deve iniciar sem rotas, chaves ou auditoria preexistentes.
- Valide o conteúdo do artefato, não apenas o working tree: inspecione filesystem/camadas, gere SBOM e faça o scan de vulnerabilidades e secrets sobre a imagem exata que será publicada.
- Fixe a imagem-base por digest. Não publique imagem com CVE alta/crítica sem correção ou waiver documentado com análise de alcance, compensações e prazo.
- O scan Gitleaks deve cobrir histórico completo, refs e tags de um clone novo. Segredo confirmado exige revogação/rotação e saneamento do histórico antes de qualquer publicação.
- `.env.example` usa um valor administrativo deliberadamente inválido. O startup deve rejeitar exatamente o placeholder público conhecido.
- Qualquer exposição à Internet exige TLS no perímetro e nunca deve publicar a porta HTTP do Fire2API diretamente. Documente proxy suportado, bind interno, redirecionamento e headers do perímetro.
- Ações administrativas NiceGUI revalidam assinatura e expiração imediatamente antes de cada leitura ou mutação; uma página aberta não prolonga autorização expirada.
- Login, Admin API e autenticação de consumidores precisam de throttling e auditoria de falhas sem registrar o token.
- Nunca registre a representação bruta de exceções SQLAlchemy/Firebird. Logs de falha contêm apenas classe/código sanitizado, execution ID e request ID. O path concreto pode permanecer no log e em `last_used_path` para diagnóstico da instância interna.

## Integridade e testes de regressão

- A revision inicial 0.0.1 representa somente banco vazio. Depois de publicada, não a edite: mudanças futuras usam nova revision e preservam registros, origens, IDs e relacionamentos.
- Rotação de `ADMIN_API_KEY` não participa da idempotência e deve preservar a garantia de não repetição.
- Depois do commit Firebird, falha ao persistir o hash/estado idempotente mantém o registro bloqueante de resultado indisponível; nunca o remova para permitir replay automático.
- Estados terminais de execução são imutáveis. Timeout ou cancelamento não podem ser sobrescritos por conclusão tardia do worker.
- Valide `ORDER_BY`, `LIMIT` e `OFFSET` antes de criar o worker; entrada inválida retorna 4xx e não produz falha Firebird.
- Testes de regressão dos controles implementados incluem fault injection após commit, rotação da chave administrativa, worker não cancelável, expiração da sessão durante callback, sentinelas em exceções de banco e parâmetros com case misto.
- `/ready` compara a revision instalada com o head Alembic obtido programaticamente; não fixe o identificador da migration no código.
- Toda variável documentada precisa ser efetivamente consumida e testada. Não mantenha limite ou controle de segurança que produza falsa expectativa operacional.

## Verificação obrigatória

```bash
python -m compileall app main.py migrations
python -m ruff check .
python -m mypy app
python -m pytest --cov --cov-report=term-missing
python -m alembic check
python -m pip_audit -r requirements.txt
```

Também valide:

- `/health` e `/ready`;
- CRUD admin e reload automático;
- todos os métodos dinâmicos;
- parâmetros path/query/body e detecção automática;
- commit, rollback, cancelamento e idempotência;
- login, logout, sessão, tema escuro e UI desktop/mobile;
- `/admin`, Swagger e ausência de `/api/base/report`;
- gitleaks e scan da imagem antes de publicação.

## Não fazer

- Não reintroduzir licença, Stripe, report, permissão web, Node, PyInstaller ou instaladores.
- Não alterar contratos de autenticação, envelopes ou rotas sem aprovação explícita.
- Não expor SQL sensível ou segredos em listagens, erros, logs ou auditoria.
- Não executar DDL Firebird por rota dinâmica.
- Não editar migrations já publicadas; crie uma nova revisão.
- Não publicar o histórico Git privado legado.
