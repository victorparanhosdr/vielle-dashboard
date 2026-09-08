# Fase 5 - Usuarios e clinicas

Somente implementacao local. Sem commit, push ou deploy. Sem permissoes de abas,
perfis ou acoes. O projeto original e os bancos das clinicas foram preservados.

## Comportamento

- Master escolhe clinicas ao criar/editar usuarios. A lista resume os acessos.
- Chaves existentes: vielle, inspire, carla. Nomes preservados como no sistema.
- Master tem acesso a todas, sem depender dos vinculos de usuarios comuns.
- Usuario com uma clinica vai automaticamente ao fluxo dela.
- Usuario com varias ve somente os cartoes permitidos no seletor existente.
- Usuario sem clinicas recebe mensagem e pode sair da conta.
- Os codigos legados e o Modo Equipe continuam existentes, aguardando a fase 8.
  Portanto a selecao automatica ainda pode pedir o codigo existente da clinica.
  Um codigo correto nunca concede acesso fora dos vinculos do usuario.
- Remocoes valem na proxima requisicao, inclusive em sessoes ja abertas.

## Seguranca

O guard verifica usuario ativo + clinica antes de abrir contexto/banco clinico.
Protege APIs de dados, sincronizacoes, widget, exportacao, inicio OAuth e URLs
de pagina que indicam clinic. Sem clinic, endpoints clinicos continuam usando
vielle, mas agora exigem permissao para vielle. Chaves desconhecidas, vazias ou
duplicadas sao recusadas, sem fallback silencioso.

/api/clinic-access valida tambem clinic_id do JSON antes de emitir cookie.
Callbacks de integracao e webhook mantem autenticacao propria por assinatura,
segredo ou estado OAuth; nao foram transformados em rotas de login humano.

/api/auth/me informa somente as clinicas permitidas. Criar/editar vinculos
exige Master e a mesma protecao CSRF ja existente. Sessao e cookies legados nao
armazenam a lista de permissoes: a decisao vem do banco a cada requisicao.

## Banco e migracao

Schema central: versao 2 -> 3, via AuthStore.initialize(), transacional e
idempotente. Nova tabela:

```sql
CREATE TABLE user_clinics (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    clinic_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, clinic_key)
);
```

Nenhuma liberacao automatica para usuarios comuns existentes: o Master deve
marcar as clinicas. Usuarios, hashes e sessoes existentes sao preservados.
Persistencia continua em DATA_DIR/volume no Railway, no banco central
doc4docs_auth.sqlite3. Nao existe duplicacao de usuarios nos bancos clinicos.

Foi feito backup SQLite local antes de atualizar a previa da fase 4. As previas
antigas desta tarefa foram encerradas para nao continuarem com guards antigos.

## Arquivos desta fase

- clinic_catalog.py: catalogo de chaves/nomes e validacao.
- auth_store.py: schema 3, vinculos atomicos, consulta de acessos.
- auth_http.py: guard de clinica em requisicoes autenticadas.
- app.py: catalogo compartilhado, resposta de sessao e defesa no codigo legado.
- master_api.py: cadastro/edicao de clinic_keys e catalogo para o Master.
- static/master.html, static/master.js, static/master.css: checkboxes e resumo.
- static/index.html, static/app.js, static/session.js: seletor filtrado,
  carregamento inicial, falta/revogacao de acesso e protecao de respostas antigas.
- static/styles.css: cartoes ocultos e contencao de largura no fluxo mobile.
- tests/test_clinic_memberships.py: persistencia, migracao, rollback e Master.
- tests/test_auth_http.py: isolamento, chaves invalidas, revogacao, codigos legados.
- tests/test_auth_store.py: expectativa atualizada para schema/tabela novos.
- AUTH_PHASE5.md: este registro.

O working tree inclui fases anteriores ainda nao commitadas. Nao publicar todo
o diff sem revisar e separar as fases.

## Como testar

A previa esta em http://127.0.0.1:8769/master com o mesmo Master da fase 3.
As integracoes externas permanecem bloqueadas nessa previa de autenticacao.

1. Crie usuario e marque uma, varias ou nenhuma clinica.
2. Reabra a edicao e recarregue a pagina para verificar persistencia.
3. Em janela anonima, entre com esse usuario em http://127.0.0.1:8769/.
4. Confira selecao automatica, seletor filtrado ou mensagem de falta de acesso.
5. Tente /?clinic=carla com usuario que nao tenha Carla: deve retornar 403.
6. Remova os vinculos pelo Master e recarregue a janela do usuario: nao pode
   continuar acessando as clinicas removidas, mesmo mantendo a sessao.

Para iniciar novamente, na pasta deste clone:

```sh
python3 tests/preview_auth.py --port 8769 --auth-db "$PWD/doc4docs_auth.sqlite3"
python3 -m unittest discover -s tests -v
```

46 testes automatizados aprovados, sem trafego externo. Testes de navegador
usam banco descartavel e verificam edicao/persistencia de vinculos, os tres
fluxos de selecao, recusa de URL e remocao em sessao existente. Screenshots
conferidos para o painel e seletor mobile/desktop.

Parar aqui. A fase 6 e apenas mapeamento de abas e depende de autorizacao.
