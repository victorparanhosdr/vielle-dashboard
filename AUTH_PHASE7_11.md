# Fases 7 a 11 - Entrega local integrada

Concluidas na copia outputs/doc4docs-auth-phase1, mantendo Python puro,
ThreadingHTTPServer, SQLite e frontend HTML/CSS/JS em static.
Sem commit, push ou deploy. A versao Railway e o projeto original nao foram
alterados. A autorizacao do usuario para concluir as fases restantes nao foi
interpretada como autorizacao de publicacao.

## Fase 7 - Abas por usuario e clinica

As permissoes ficam no banco central, por usuario + clinic_key + permission_key.
Master ve todas as abas tecnicamente disponiveis em cada clinica. Na edicao de
usuarios comuns, cada clinica tem seu proprio conjunto de abas e acoes.

Frontend filtra os mesmos botoes no desktop e menu mobile, escolhe uma aba
permitida e mostra mensagem quando nenhuma estiver liberada.
Backend verifica o acesso em APIs e URLs com view. /api/report agora projeta
somente o conjunto da aba selecionada; os flags de acompanhamentos nao podem
ser combinados para contornar a restricao. Requisicoes antigas do frontend sao
descartadas ao trocar de aba/clinica. Revogacoes valem na proxima requisicao.

Ver AUTH_PHASE6.md sobre a semantica dos dados: Painel geral ainda contem seus
indicadores financeiros agregados. A aba Financeiro contem o detalhe. Nao foi
criada uma restricao nova por campo dentro do Painel geral.

## Fase 8 - Modo Equipe

Recomendacao adotada: Modo Equipe e apenas um atalho visual para o acompanhamento
de pacientes da Vielle, com a mesma sessao individual e as mesmas permissoes.
Nao concede acesso extra nem oculta outros modulos que o Master tenha liberado.

O codigo compartilhado da clinica deixou de ser exigido ou aceito como fonte
de autorizacao. A rota legada /api/clinic-access permanece apenas por
compatibilidade, validando a sessao e o vinculo da clinica; nao emite cookies
de codigo. Cookies antigos sao ignorados e limpos no login/logout.
Nenhum contato, estado de paciente, orcamento ou banco clinico foi migrado.
As configuracoes antigas de codigos permanecem armazenadas, mas nao autorizam
acesso. O formulario antigo nao e mais aberto pelo fluxo de navegacao.

## Fase 9 - Acoes

- dashboard: view, edit (metas), export.
- commercial e financial: view, export.
- patient_followup e budget_followup: view, create (contato), edit (status), export.
- paid_traffic: view, edit (atualizar trafego), export.
- whatsapp_review: view, edit (avaliacao manual/IA), export.

Toda acao exige tambem view. Exclusao nao foi inventada para telas que nao
possuem essa operacao. A arquitetura permite acrescentar delete/create/edit
ao catalogo quando houver endpoint correspondente, sem mudar o schema.

O botao de exportacao consulta /api/export-authorize antes de imprimir. Rotas
de exportacao e print=1 tambem verificam a permissao. Nao e possivel impedir
captura de tela ou impressao manual pelo navegador de dados ja visualizados.

Configuracoes continuam exclusivas do Master. Conexao Kommo e sincronizacoes
globais tambem foram restringidas ao Master por seu impacto em toda a
integracao, nao sendo permissoes de abas.
Callbacks OAuth e webhooks preservam validacao independente de estado/segredo.

## Fase 10 - Perfis

Perfis iniciais: Administrador da clinica, Gerente, Comercial, Financeiro,
Recepcao. So utilizam modulos que existem neste sistema; nao inventam Agenda.
O Master pode criar/editar perfis em /master, aplicar um perfil por clinica,
selecionar/remover todas as permissoes, copiar de outra clinica e ajustar
qualquer checkbox. Pacientes nao aparece para Inspire/Carla.

Perfis sao modelos copiados, nao uma heranca dinamica. Alterar o modelo nao
modifica acessos anteriormente concedidos nem transforma usuario em Master.
Ao aplicar um modelo, o formulario retorna a Personalizado apos ajustes.

## Fase 11 - Auditoria

Registros centrais de login, login negado (inclusive limite de tentativas),
logout, criacao/edicao de usuario, ativacao/desativacao, redefinicao de senha,
mudanca de clinicas/permissoes e criacao/edicao de perfis.
Escritas administrativas e seus logs ocorrem na mesma transacao SQLite.
Logs exibem data, autor, alvo e detalhes antes/depois quando aplicavel.
O painel tem filtro por acao e paginacao. Nao ha API publica ou de exclusao
de logs. Senhas, hashes, tokens e chaves de integracao nao sao registrados.
Nao foi inventado historico retroativo de eventos anteriores a esta fase.

## Migracao e persistencia

Schema do banco central: 3 -> 4, via AuthStore.initialize(), transacional e
idempotente. Novas tabelas:

- user_clinic_permissions (FK composta para user_clinics, exclusao em cascata).
- access_profiles (nome, permissoes JSON, datas).
- audit_log (data, autor, alvo, acao, detalhes JSON).

Usuarios da fase 5 mantem o acesso completo que ja tinham nas clinicas
atribuidas. Isso acontece somente na migracao de schema < 4. Depois, initialize
nao reatribui permissoes removidas. Usuarios novos pela interface comecam sem
permissoes nas clinicas ate o Master marcar as abas ou aplicar um perfil.
Todos os bancos clinicos foram preservados. DATA_DIR/volume continua sendo a
fonte do caminho persistente do banco central no Railway.

## Arquivos desta entrega

- access_policy.py: catalogo de modulos/acoes, validacao e projecao de dados.
- access_store.py: permissoes, perfis, logs e schema complementar.
- auth_store.py: integracao transacional, migracao e auditoria.
- auth_http.py: verificacoes por aba/acao/URL.
- master_api.py: endpoints de permissoes, perfis e logs.
- app.py: projecao do relatorio, autorizacao de exportacao e fim do codigo compartilhado.
- static/app.js, static/session.js: navegacao, acoes e atualizacao de acessos.
- static/index.html, static/styles.css: menu compartilhado acessivel e ocultacao.
- static/master.html, static/master.js, static/master.css, static/master-access.js:
  editor de permissoes, perfis e auditoria responsivos.
- tests/test_access_permissions.py, tests/test_auth_http.py,
  tests/test_auth_store.py, tests/test_clinic_memberships.py: regressao e seguranca.
- tests/preview_auth.py, tests/ui_access_smoke.cjs: QA local sem integracoes reais.
- AUTH_PHASE6.md e AUTH_PHASE7_11.md: mapeamento e registro da entrega.

## Testar localmente

1. Abra http://127.0.0.1:8769/master com o Master local ja criado.
2. Crie usuario, marque clinicas, aplique perfil e personalize permissoes.
3. Use janela anonima para conferir as clinicas/abas permitidas.
4. Teste uma URL de aba proibida: deve retornar 403; modulo inexistente, 400.
5. Remova uma permissao com a sessao comum aberta e atualize a pagina.
6. Confira o evento em Auditoria. Teste tambem redefinicao de senha.

A previa local e de autenticacao/administracao; nao consulta dados clinicos
reais. As integracoes estao deliberadamente bloqueadas nela.

```sh
python3 -m unittest discover -s tests -v
python3 tests/preview_auth.py --port 8769 --auth-db "$PWD/doc4docs_auth.sqlite3"
```

O teste Playwright tests/ui_access_smoke.cjs exige um banco descartavel com
fixtures master/limited/readonly/empty e --sample-reports. Nao rodar contra o
banco de uso do Master. Ele verifica editor, perfis, auditoria, abas desktop e
mobile, leitura sem escrita, negacao por API, revogacao e ausencia do codigo
legado. Relatorios sinteticos sao apenas para exercitar o frontend.

Validacao final: 57 testes Python aprovados; smoke Playwright aprovado com
perfis, auditoria, leitura sem escrita, mudanca de clinica, menu mobile,
negacao por API, revogacao em sessao aberta e nenhum erro JavaScript.
Sintaxe JavaScript e git diff --check tambem aprovados. Screenshots do editor,
auditoria e menu restrito foram conferidos. Nao foram chamadas APIs reais
Kommo, Clinica Experts, Meta ou OpenAI durante esta validacao.

Antes de publicar: revisar todo o diff acumulado desde a fase 1, separar de
outras tarefas, validar o volume Railway e provisionar o Master no ambiente
de destino. O Master local nao sera enviado ao Git nem criado automaticamente
em producao. Nenhuma publicacao foi feita nesta entrega.
