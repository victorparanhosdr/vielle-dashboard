# DOC4DOCS - Fase 2: login e sessao

Continua na pasta isolada outputs/doc4docs-auth-phase1. Sem deploy, sem commit
e sem reaproveitar o bloco antigo incompleto da pasta kommo-report-app.
AUTH_PHASE1.md documenta a entrega anterior; este documento descreve a evolucao.

## Comportamento

- /login: login e senha com identidade DOC4DOCS. Sem recuperacao de senha.
- POST /api/auth/login: valida usuario ativo, cria sessao e atualiza last_login.
- GET /api/auth/me: retorna usuario autenticado, nunca password_hash ou token.
- POST /api/auth/logout: revoga a sessao e limpa cookies das clinicas.
- Paginas sem sessao redirecionam para /login. APIs retornam 401 e
  code=session_required. HEAD tambem passa pela verificacao.
- Os arquivos publicos sao somente a tela de login e seus recursos explicitamente
  permitidos. Caminhos /static/ nao permitem contornar a autenticacao.
- Sair aparece na selecao de clinicas, no dashboard (inclusive modo equipe)
  e nas configuracoes. O frontend reconhece expiracao de sessao, atualiza o nome
  e comunica logout a outras abas via BroadcastChannel.
- Os codigos compartilhados das clinicas e o master antigo das configuracoes
  permanecem. Nao ha permissao nova por clinica, aba, acao ou is_master.

## Sessoes e banco

Migracao automatica e transacional de schema 1 para 2 em doc4docs_auth.sqlite3:
adiciona tabela sessions (token_hash, user_id, created_at, expires_at), com
indices por usuario e expiracao. Usuarios e bancos clinicos sao preservados.
Nao ha usuario padrao, Master automatico ou nova credencial em producao.

Token aleatorio de 32 bytes, apenas seu SHA-256 fica no banco. Cookie HttpOnly,
SameSite=Lax, Path=/, validade absoluta de 12 horas; Secure no Railway ou quando
APP_BASE_URL utiliza HTTPS. Sessao sobrevive a reinicializacao do processo desde
que o volume seja preservado. Login substitui o token anterior do navegador;
logout, desativacao e troca de senha revogam sessoes no banco. Toda consulta
verifica expiracao e status ativo. Respostas usam Cache-Control: no-store.

POSTs do navegador e GETs de sincronizacao exigem X-DOC4DOCS-Request: 1 e
verificacao de origem. static/session.js adiciona o cabecalho nas chamadas
existentes, preservando os cabecalhos do master antigo. Nao ha CORS liberado.
Tentativas de login limitadas em memoria: 10 por login e 100 por endereco remoto
em 10 minutos. Reinicio limpa esses contadores, mas nao as sessoes persistidas.
O endereco remoto pode ser o proxy do Railway; nao se confia cegamente em
X-Forwarded-For fornecido por clientes.

## Integracoes

O webhook Clinica Experts continua fora do login interativo e exige seu segredo.
O callback OAuth Kommo continua usando seu state. O webhook de revogacao Kommo
permanece acessivel ao provedor, mas agora verifica assinatura ANTES de apagar
o token. /auth/start e /kommo-widget exigem sessao do usuario.
Nao ha alteracoes em relatorios, sincronizacao, PDF ou regras dos acompanhamentos.
O teste de callbacks e local; nao foi executado um OAuth real nem webhook real.

## Testar

```sh
cd /Users/victorparanhos/Documents/Codex/2026-07-07/queri/outputs/doc4docs-auth-phase1
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Os testes HTTP usam o Handler real em localhost com bancos temporarios e
integracoes substituidas por fixtures. E necessario permitir portas localhost.
Nenhum teste acessa dados reais ou servicos externos.

Previa interativa, sem integracoes e sem dados reais:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/preview_auth.py --port 8766
```

Abra http://127.0.0.1:8766/login, login teste, senha TesteLocalDOC42026!.
Esse usuario e criado SOMENTE pelo utilitario de preview em TemporaryDirectory.
Ele nao existe no banco do sistema e nao e criado por app.py.
Teste senha incorreta, login correto, atualizacao da pagina, Sair e acesso
direto a / ou /api/report depois do logout. A selecao de clinicas aparece apos
o login; a previa bloqueia operacoes das integracoes intencionalmente.

Para testar o app completo com um banco local escolhido, crie explicitamente
um usuario comum pela API Python da Fase 1. Nao ha formulario de cadastro nesta fase:

```sh
DATA_DIR=/caminho/absoluto/para/dados-locais python3 - <<'PY'
from pathlib import Path
from getpass import getpass
from auth_store import AuthStore, auth_database_path
store = AuthStore(auth_database_path(Path.cwd()))
store.initialize()
store.create_user(input('Nome: '), input('Login: '), getpass('Senha: '))
print('Usuario comum criado.')
PY
```

Depois use o mesmo DATA_DIR ao iniciar app.py. O inicio normal do app agenda as
sincronizacoes existentes; use a previa acima para testar somente o login.
Nenhuma mudanca deve ser publicada sem autorizacao. Antes de publicar, providenciar
um usuario ativo no banco central persistente para evitar ficar sem acesso.

## Arquivos da fase

- app.py, auth_store.py: integracao e migracao das sessoes.
- auth_http.py: guarda de acesso, cookies e endpoints de login/logout/me.
- static/login.html, static/login.css, static/login.js: tela de entrada.
- static/session.js: sessao e logout no frontend existente.
- static/index.html, static/settings.html, static/styles.css: controles de conta.
- tests/test_auth_store.py: atualiza expectativa para schema 2.
- tests/test_auth_sessions.py, tests/test_auth_http.py: testes novos.
- tests/preview_auth.py: previa local isolada.
- AUTH_PHASE2.md: documentacao.

Os arquivos da Fase 1 ainda nao foram commitados. A revisao futura deve distinguir
essas duas fases e excluir o trabalho antigo de autenticacao da pasta original.
