# Changelog

## 0.0.1 - 2026-07-15

- Script independente para incrementar `project.version`, criar o commit de versão e adicionar a tag Git correspondente com validação de árvore limpa.
- Painel administrativo com lista de rotas compacta e responsiva, campos simplificados e exclusão movida para uma zona de perigo no editor.
- Fluxo principal do Docker Compose simplificado: `docker compose up -d` agora recompila a imagem local antes de iniciar ou recriar o serviço.
- README reestruturado com apresentação profissional, instalação Docker-first, atualização, operação e avisos de segurança.
- Primeira beta pública do Fire2API by Markware sob Apache-2.0.
- Backend FastAPI, painel NiceGUI responsivo e rotas dinâmicas GET/POST/PUT/PATCH/DELETE.
- Editor SQL CodeMirror com parâmetros canônicos em maiúsculas e entrada case-insensitive.
- Autenticação administrativa, Access Keys por SHA-256, expiração de sessão revalidada por ação e proteção básica contra força bruta.
- Transações explícitas, cancelamento, timeout terminal, histórico, auditoria e idempotência sem armazenamento de payload ou resposta.
- Metastore SQLite inicial limpo em migration única; versões pré-beta não possuem caminho de upgrade.
- Imagem Docker sem estado incorporado, base fixada por digest, dependências apenas de runtime, SBOM e gates de secrets/CVEs.
- Instruções de produção com HTTPS obrigatório no reverse proxy para qualquer acesso externo.
