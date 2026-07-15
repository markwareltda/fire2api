# Política de Segurança

Reporte vulnerabilidades de forma privada para `security@markware.com.br`. Não abra uma issue pública antes da correção coordenada. Inclua versão, impacto, pré-condições e uma reprodução mínima sem dados reais.

Versões da linha 2.x recebem correções de segurança. Credenciais expostas devem ser rotacionadas imediatamente; remover o arquivo do Git não elimina o segredo do histórico.

Operadores devem usar uma conta Firebird de menor privilégio, rede privada/TLS no perímetro, `ADMIN_API_KEY` aleatória com 32+ caracteres, CORS por allowlist, backups do volume do metastore e rotação periódica das Access Keys.
