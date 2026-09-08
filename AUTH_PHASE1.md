# DOC4DOCS - Fase 1: banco central de usuarios

Base: commit publicado cb92c38. Trabalho isolado em outputs/doc4docs-auth-phase1,
branch phase1-auth-database. A pasta original kommo-report-app permanece intacta,
incluindo o trabalho antigo incompleto. Nao publicar esse trabalho antigo junto.

## Escopo

- Banco central doc4docs_auth.sqlite3, sem vinculo com uma clinica.
- Tabela users: id, nome, login, password_hash, status, is_master, created_at,
  updated_at e last_login. Status: active/inactive. Datas em UTC ISO 8601.
- Nenhum usuario e criado automaticamente. is_master existe apenas como dado;
  esta fase nao cria Master nem atribui privilegios a rotas.
- Sem telas, endpoints, sessoes, tokens ou permissoes novas.
- A navegacao e os controles de acesso antigos continuam como estavam.

## Persistencia e inicializacao

O caminho usa DATA_DIR, depois RAILWAY_VOLUME_MOUNT_PATH, depois a pasta do
projeto para execucao local. No Railway (RAILWAY_PROJECT_ID presente), a ausencia
de ambos os caminhos e um erro. A configuracao precisa apontar para um volume
persistente real; um diretorio gravavel por si so nao garante persistencia.

O inicio de app.py inicializa o banco antes de iniciar as clinicas, sincronizacao
e servidor HTTP. Erros no caminho de autenticacao interrompem a inicializacao,
sem criar uma copia alternativa na pasta da aplicacao. O comportamento existente
dos bancos clinicos nao foi modificado.

Schema inicial: PRAGMA user_version = 1, tabela users e indice UNIQUE de login.
A inicializacao e idempotente e nao apaga usuarios. Nao importa usuarios da
implementacao antiga e nao altera bancos das clinicas. SQLite utiliza WAL,
synchronous=FULL, timeout de 60 segundos e conexao independente por operacao.

## Senhas e operacoes

PBKDF2-HMAC-SHA256 com 600.000 iteracoes e salt aleatorio de 32 bytes por senha.
O hash registra algoritmo, iteracoes, salt e digest. Comparacao com compare_digest.
Novas senhas: minimo de 12 caracteres e maximo de 1024 bytes UTF-8.
Login e normalizado com NFKC, trim e casefold, sem espacos internos; duplicatas
geram sqlite3.IntegrityError, sem sobrescrever usuarios existentes.

API Python em auth_store.py:

- AuthStore(path).initialize()
- create_user(nome, login, password, status='active', is_master=False): retorna id.
- get_user_by_login(login): retorna dict interno ou None, incluindo password_hash.
  Esse dict nao deve ser devolvido diretamente por uma futura API HTTP.
- validate_password(login, password): retorna bool; inativos sao rejeitados.
- update_last_login(user_id): registra acesso para usuario ativo; retorna bool.
- set_user_active(user_id, active): ativa/desativa; retorna bool.
- set_password(user_id, password): substitui hash; nao reativa o usuario; retorna bool.

validar senha nao atualiza last_login automaticamente. A futura camada de login
devera atualizar esse campo somente quando o login for concluido. As funcoes sao
internas e ainda nao substituem a autorizacao que sera implementada nas proximas fases.

## Testar localmente sem tocar em dados reais

Na pasta outputs/doc4docs-auth-phase1, execute:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Os testes usam TemporaryDirectory e nao importam app.py, nao carregam credenciais,
nao iniciam servidor e nao chamam integracoes. Verificam criacao, unicidade,
hash, troca de senha, inatividade, ultimo login, reabertura, concorrencia,
persistencia configurada, schema e preservacao de arquivos clinicos de teste.

Para inicializar apenas o banco vazio no diretorio configurado, sem subir o app:

```sh
python3 -c 'from pathlib import Path; from auth_store import AuthStore, auth_database_path; AuthStore(auth_database_path(Path.cwd())).initialize()'
```

Esse comando cria somente o banco central. Nao e necessario executa-lo para rodar
os testes. Nao executar app.py apenas para testar autenticacao: o inicio normal
do sistema tambem executa a inicializacao das clinicas e agenda sincronizacoes.

## Publicacao

Esta fase nao foi publicada. Revisar apenas app.py, auth_store.py, .gitignore,
tests/test_auth_store.py e este documento antes de um commit autorizado.
Nao versionar bancos nem arquivos auxiliares WAL/SHM/journal.
