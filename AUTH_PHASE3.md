# DOC4DOCS - Fase 3: primeiro Master e rotas administrativas

Trabalho na pasta isolada outputs/doc4docs-auth-phase1. Sem commit ou deploy.
Nao inclui o Painel de Gerenciamento de Usuarios da Fase 4.

## Entrega

- /master: pagina inicial administrativa, com identificacao da conta,
  retorno ao sistema e logout. Sem cadastro, edicao ou listagem de usuarios.
- is_master e consultado no banco central a cada requisicao administrativa.
  Usuario comum recebe 403; sem sessao recebe redirecionamento ao login ou 401
  na API. Usuario inativo ou sessao revogada nao passa pela autenticacao.
- /master, /master.html, aliases /static/, GET e HEAD estao protegidos.
  O arquivo resolvido tambem e conferido para bloquear aliases codificados.
- /settings.html, /settings.js, /api/settings, /api/sync-all, /api/clear-data
  e /api/reset-kommo exigem a sessao de um Master.
- As antigas credenciais X-Master-User e X-Master-Password nao concedem mais
  acesso. Configuracoes usa a sessao atual e carrega os campos automaticamente.
- Links Master e Configuracoes so aparecem para Master. A protecao real fica
  no backend, independentemente dos links.
- Login preserva somente o destino permitido /master, sem redirecionamento
  arbitrario para enderecos externos.
- Nao muda os codigos das clinicas, modo equipe ou permissoes de abas.

## Primeiro Master

Foi criado explicitamente um Master LOCAL, login master, nome Administrador
Master, no doc4docs_auth.sqlite3 desta pasta. A senha foi gerada aleatoriamente
e entregue na conversa, sem ser gravada em texto aberto no codigo ou documentos.
Somente o hash esta no banco. Este banco e ignorado pelo Git.

Nenhum usuario foi criado no Railway. Um deploy de codigo nao leva esse usuario
local para producao. Quando autorizado, o Master de producao precisara ser
provisionado explicitamente no banco central do volume persistente.

create_master.py e um comando local/manual, nunca chamado ao iniciar app.py.
Usa getpass (senha nao aparece no terminal), confirma a senha e cria somente o
primeiro Master. Nao sobrescreve usuarios ou promove contas existentes.
Mesmo um Master inativo impede repetir esse bootstrap. A transacao impede
duas execucoes simultaneas de criarem dois primeiros Masters.

```sh
DATA_DIR=/caminho/absoluto/do/volume python3 create_master.py
```

Nao executar novamente no banco local que ja possui o Master. O comando recusara.
O metodo create_first_master tambem pode ser usado por uma rotina administrativa
local confiavel; nao existe endpoint HTTP para criar ou promover Master.

## Banco

Nenhuma nova tabela ou versao de schema: continua user_version = 2.
Foi inserido apenas o registro Master no banco central local. Os bancos das
clinicas e os registros preexistentes de usuarios nao foram alterados.

## Testar

```sh
cd /Users/victorparanhos/Documents/Codex/2026-07-07/queri/outputs/doc4docs-auth-phase1
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

31 testes cobrem sessoes, Master, acesso negado, aliases de arquivos,
cabeçalhos antigos, bootstrap repetido e concorrencia. Dados temporarios,
servidores HTTP localhost e integracoes substituidas por fixtures.

Previa do Master com o banco local provisionado, sem executar integracoes:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/preview_auth.py --port 8767 --auth-db "$PWD/doc4docs_auth.sqlite3"
```

Abra http://127.0.0.1:8767/master. Entre com master e a senha entregue na
conversa; confira a identificacao, o retorno ao sistema e o logout.
As APIs de integracoes ficam bloqueadas nessa previa, mesmo para Master.

Para testar um usuario comum em banco temporario separado:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/preview_auth.py --port 8768
```

Login teste / senha TesteLocalDOC42026!. Depois acesse /master ou /settings.html:
o backend deve retornar 403. Esse utilitario so cria o usuario de teste quando
nao recebe --auth-db, e somente dentro de um diretorio temporario.

## Arquivos da Fase 3

- auth_store.py, create_master.py: bootstrap explicito.
- auth_http.py, app.py: validacao Master e rota protegida.
- static/master.html: pagina inicial Master.
- static/index.html, static/session.js, static/styles.css: links administrativos.
- static/login.js, static/login.css: retorno ao Master e estilo do link.
- static/settings.html, static/settings.js: sessao substitui credencial antiga.
- tests/test_auth_http.py, tests/test_master_bootstrap.py: testes.
- tests/preview_auth.py: opcao de banco local ja provisionado.
- AUTH_PHASE3.md: este documento.

As fases anteriores ainda nao foram commitadas. Preservar essa separacao na
revisao futura e nao incluir o trabalho antigo da pasta kommo-report-app.
