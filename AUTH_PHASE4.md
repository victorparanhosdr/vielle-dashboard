# Fase 4 - Gerenciamento de usuarios

Implementacao local, sem commit, push ou deploy. Mantem Python puro, SQLite e
frontend servido de static. Nao implementa permissoes por clinica ou aba.

## Entrega

- Painel /master exclusivo de Master, com busca, filtro de status e paginacao.
- Lista nome, login, perfil, status, criacao e ultimo acesso.
- Cria usuarios comuns; edita nome/login; ativa/desativa; redefine senha.
- Nao permite promover usuarios por campos adicionais na API.
- Senhas nunca aparecem na listagem e continuam usando hash seguro.
- Alteracao de login, redefinicao de senha e desativacao revogam sessoes.
- O ultimo Master ativo nao pode ser desativado, inclusive em concorrencia.
- Interface responsiva com icones Lucide locais e licenca incluida.

## Arquivos desta fase

- auth_store.py: listagem publica, edicao e protecao do ultimo Master.
- master_api.py: endpoints administrativos e validacao de entrada.
- app.py: conexao do mixin administrativo ao Handler existente.
- static/master.html, static/master.css, static/master.js: painel e formularios.
- static/master-icons.js, static/master-icons.LICENSE.txt: icones locais.
- tests/test_user_admin.py: testes de armazenamento e concorrencia.
- tests/test_auth_http.py: testes HTTP administrativos.
- tests/test_master_bootstrap.py: ajuste de fixture para Master inativo.
- tests/preview_auth.py: preview administrativo sem integracoes externas.
- AUTH_PHASE4.md: este registro.

Existem arquivos ainda nao commitados das fases anteriores. Esta lista nao
significa que todas as alteracoes do working tree pertencem a esta fase.

## Banco

Sem nova migracao: schema central permanece na versao 2 (users e sessions).
Nenhum banco de clinica foi modificado. Em producao o banco central segue a
estrutura persistente DATA_DIR/volume ja implementada nas fases anteriores.

## Endpoints

- GET /api/master/users: search, status e page opcionais.
- POST /api/master/users: nome, login, password e status.
- POST /api/master/users/{id}/edit: nome e login.
- POST /api/master/users/{id}/status: active booleano.
- POST /api/master/users/{id}/password: password.

Todos exigem sessao Master. Escritas validam origem/cabecalho CSRF, JSON,
tamanho de corpo e campos permitidos. Nao existe recuperacao automatica.

## Teste local

Na pasta deste clone:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
python3 tests/preview_auth.py --port 8769 --auth-db "$PWD/doc4docs_auth.sqlite3"
```

Abra http://127.0.0.1:8769/master e use o Master criado na fase 3.
O preview usa esse banco local para usuarios/sessoes e bloqueia integracoes
reais. Crie um usuario, edite o nome/login, redefina a senha e desative/reative.
Recarregue a pagina para conferir persistencia. Usuario comum nao abre /master.

Validacao: 38 testes automatizados aprovados. Playwright/Chrome validou criacao,
edicao, erro de confirmacao e redefinicao de senha, desativacao e persistencia
apos reload em banco descartavel. Conferidos screenshots desktop/mobile e
ausencia de overflow horizontal em 320, 390, 820 e 1440 pixels; sem erros JS.

Parar aqui. A fase 5 depende de nova autorizacao.
