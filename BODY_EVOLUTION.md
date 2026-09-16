# Evolucao corporal da Inspire

## Escopo

- Modulo `body_evolution`, disponivel somente para `inspire`.
- Entrada pelas abas desktop e pelo menu mobile; pagina propria em
  `/body-evolution.html?clinic=inspire`.
- Cadastro vinculado ao UUID do paciente na base **ja sincronizada** do Clinica
  Experts. Nao busca pacientes de outra clinica nem cria pacientes no sistema externo.
- Link privado por paciente: `/body-evolution.html?clinic=inspire&patient=<id>`.
  Exige login, acesso a Inspire e permissao de visualizacao, inclusive na API.
- Avaliacoes manuais, importacao com revisao obrigatoria, edicao versionada,
  graficos, medidas, historico de alteracoes e download do PDF original.
- Graficos e comparacoes usam data/hora do exame e separam origem/metodo.
  Medidas ausentes nao viram zero. Silhuetas sao ilustrativas, nao simulacoes
  anatomicas ou diagnosticos.
- A navegacao agrupa exames pela data local do laudo, em uma unica aba por dia.
  Bioimpedancia, calorimetria e registros manuais do mesmo dia aparecem juntos,
  mas conservam horarios, medidas, PDFs, edicao e exclusao individuais.
  Os indicadores de composicao priorizam a bioimpedancia do dia (a mais recente
  se houver mais de uma), com origem visivel. Nao ha media ou sobrescrita de pesos
  ou TMB de equipamentos diferentes. Resultados de outro dia nao sao apresentados
  como se fossem atuais. Os graficos incluem o dia selecionado inteiro.
- A calorimetria destaca RQ, utilizacao de gorduras, utilizacao de carboidratos
  e VO2 em ml/kg/min. Utilizacao de gordura nao e percentual de gordura corporal.
  Ausencias sao exibidas como "Nao informado". Nao ha migracao ou mescla no banco
  para o agrupamento por dia; registros existentes recebem a nova visualizacao.
- Visual aprovado: superficies claras, cabecalho verde, indicadores compactos
  com icones Lucide, silhueta perolada ilustrativa e faixa de calorimetria.
  O grafico mostra valores nos pontos quando ha espaco, mantendo leitura ao
  tocar, focar ou passar o mouse. Medidas completas ficam expansivas por exame.
  O novo bitmap e `static/body-mannequin-pearl.png`; a silhueta e estatica e nao
  simula alteracoes anatomicas a partir dos numeros do paciente.
- Composicao corporal: barras lado a lado de peso e massa muscular (eixo kg,
  sempre a partir de zero), linha de gordura corporal (eixo percentual proprio).
  Medidas corporais: abdomen e quadril em linhas no mesmo eixo cm. Gradientes
  verdes, salvia e oliva, sem misturar unidades ou empilhar peso com musculo.
  As datas sao categorias de exames em ordem cronologica; horarios repetidos
  permanecem separados. A legenda permite ocultar series. Mouse, toque e foco
  mostram data/hora, valores, unidades e metodo; setas navegam entre exames,
  Escape fecha a leitura. Lacunas nao viram zero nem sao interpoladas.
  Alteracao apenas de frontend, sem migracao de banco ou dependencia externa.

## Vinculo na anotacao do Clinica Experts

- Botao `Vincular no Clinica Experts` na ficha, apenas para quem tem
  `body_evolution.edit`. A rota POST `/api/body/experts-link` exige sessao,
  acesso a Inspire, visualizacao, edicao e protecao CSRF no backend.
- A previa consulta `GET https://api.clinicaexperts.com.br/api/v1/patients/{uuid}`
  com a chave ja configurada **da Inspire**. Compara o UUID do retorno, mostra
  nomes local/remoto, anotacao anterior e o texto a acrescentar. Nao grava ao
  cadastrar paciente nem ao importar exames.
- A confirmacao envia `annotation` e o `name` atual, sem modifica-lo, em `PUT`
  na mesma rota (a API em producao exige o nome mesmo nesta atualizacao) e verifica
  o texto por um novo GET. Nao cria atendimento nem entrada no prontuario.
  Contrato oficial: https://clinicaexperts.readme.io/reference/update-patient
- Definir `BODY_EVOLUTION_PUBLIC_ORIGIN=https://doc4docs.com.br` no servidor
  publicado (alternativamente `APP_BASE_URL`, se ja contiver essa origem).
  Nao usa Host, URL ou UUID enviados pelo cliente para escolher o destino.
  Dominios locais, HTTP e caminhos adicionais sao recusados. Nunca configurar
  uma demonstracao com chaves reais e IDs de pacientes ficticios.
- O texto anterior e preservado integralmente, inclusive espacos. O link exato
  ja existente nao e duplicado. Mudancas no nome, anotacao ou updated_at desde
  a previa interrompem a gravacao e exigem nova conferencia. Uma trava local
  evita requisicoes simultaneas do mesmo processo; a API nao documenta ETag /
  If-Match, portanto nao ha garantia atomica contra edicoes externas entre GET
  e PUT. Evitar editar o mesmo cadastro no Experts durante a confirmacao.
- Antes do PUT e criada/atualizada uma tabela nova e independente no banco da
  clinica: `body_experts_link_writes`, com copia anterior/posterior da anotacao,
  paciente, autor, data e status (`pending`, `verified`, `unconfirmed`). Nada
  e apagado das tabelas anteriores. O commit ocorre antes da chamada de rede.
  A auditoria central registra IDs, nao o texto da anotacao.
- Timeout ou resposta divergente nao provoca repeticao automatica nem rollback
  no Experts. Reabrir a previa consulta o estado real antes de nova tentativa.
- Testes: `python3 -m unittest tests.test_clinica_patient_link tests.test_body_evolution -v`.
  Os testes usam dados ficticios e transporte simulado. A confirmacao real e a
  apresentacao/clicabilidade do link no Experts precisam ser verificadas em
  cadastro autorizado antes de considerar a integracao validada em producao.

## PDFs

Leitura local com `pdfplumber==0.11.9`, sem envio para OpenAI ou outro servico.
Modelos validados: InBody120 e HandyMet, com camada de texto, ate 5 paginas e
8 MB. PDFs escaneados ou layouts diferentes sao recusados sem gravacao.
O processamento roda em subprocesso com limite de tempo e recursos.

Importar apenas preenche uma previa. O usuario confere paciente, data, unidades
e valores antes de salvar. O PDF original e a extracao original ficam preservados
mesmo apos uma correcao manual. Arquivos identicos nao podem ser importados duas
vezes na mesma clinica. Nomes abreviados do PDF nao sao usados para vinculo automatico.

Massa muscular esqueletica e massa livre de gordura sao campos diferentes.
A TMB estimada pelo InBody nao substitui a TMB informada pelo HandyMet. Este
ultimo laudo descreve TMB estimada como TMR menos 10%; GET e TMB prevista tambem
sao mantidos separadamente. O sistema nao faz recomendacoes de tratamento.

## Persistencia e permissoes

Cinco tabelas sao criadas de forma idempotente no primeiro acesso ao
modulo, no banco **existente da Inspire**, dentro do `DATA_DIR` configurado:

- `body_patients`: vinculo com Clinica Experts e nome de contingencia.
- `body_evaluations`: medidas, data do exame, origem, profissional e versao.
- `body_documents`: PDF original como BLOB, hash SHA-256 e extracao original.
- `body_revisions`: autor, momento e snapshots de criacao/correcao.
- `body_exclusions`: estado de exclusao recuperavel, autor e data. Nao altera
  as colunas de tabelas existentes; adicionada na atualizacao de exclusao.

Nenhuma tabela anterior e removida, renomeada ou limpa. Sincronizar o catalogo
nao apaga avaliacoes. O backup SQLite da Inspire deve incluir estas tabelas e
documentos; usar backup consistente do SQLite, incluindo o estado WAL.

Permissoes no catalogo central existente:
`body_evolution.view`, `.create`, `.edit`, `.delete`, `.export`.
Master tem acesso. Usuarios comuns existentes nao recebem liberacao automatica:
o Master deve habilitar as acoes na Inspire. O painel e os perfis existentes
utilizam o novo modulo sem uma segunda estrutura de autorizacao. Outras clinicas
nao o oferecem e suas rotas sao bloqueadas pelo backend.

## Exclusao recuperavel

O icone de lixeira aparece na avaliacao selecionada e no historico para quem
possui `.delete`. A confirmacao identifica paciente, exame e data. A avaliacao
excluida deixa de participar de contagens, graficos e comparacoes. O download
do PDF fica indisponivel ate a restauracao.

O painel `Avaliacoes excluidas` permite restaurar o mesmo registro, inclusive
quando nao existem mais avaliacoes ativas. PDF, medidas e auditoria permanecem
armazenados. Excluir/restaurar incrementa a versao para impedir sobrescrita
por outra tela aberta. Nao ha exclusao fisica de registros ou arquivos.
Usuarios existentes nao recebem `.delete` automaticamente; o Master pode
libera-la em `Excluir / restaurar` dentro de Evolucao corporal da Inspire.

## Testar localmente

Usar um ambiente com `requirements.txt` instalado. Nunca apontar a demonstracao
para bancos de producao.

```sh
python3 -m unittest tests.test_body_evolution -v
python3 -m unittest discover -s tests
node --test tests/test_body_evolution_dates.cjs
node --test tests/test_body_evolution_charts.cjs
python3 tests/serve_body_preview.py
```

O ultimo comando cria bancos **temporarios isolados**, pacientes e exames
ficticios, um usuario local `preview` e uma senha aleatoria. Exibe URL e credenciais
no terminal e em `/tmp/doc4docs-body-preview-info.json`. Escuta somente em
127.0.0.1, em porta livre, sem iniciar sincronizacao automatica. Nao usar este
script como servidor de producao. Encerrar com Ctrl+C quando nao for mais necessario.

Verificacoes: abrir a aba na Inspire; adicionar paciente pelo catalogo; cadastrar
medida retroativa; conferir ordem cronologica; editar e consultar o historico;
importar PDF, revisar e confirmar; tentar importar o mesmo PDF novamente;
abrir link privado sem sessao; testar usuario somente leitura; verificar celular.

## Arquivos

Backend: `body_evolution.py`, `body_exams.py`, hooks em `app.py`,
`access_policy.py`, `access_store.py`, `auth_http.py`, `requirements.txt`.

Frontend servido: `static/body-evolution.html`, `.css`, `.js`, `static/body-evolution-data.js`, `static/body-evolution-charts.js`,
`static/body-mannequin-pearl.png`, `static/body-icons.js` e sua licenca; integracao
em `static/index.html`, `static/app.js`, `static/login.js`, `static/session.js`.

Testes: `tests/test_body_evolution.py`, `tests/serve_body_preview.py`.

Publicar este modulo somente apos autorizacao. Alteracoes de outras tarefas,
como acompanhamento de orcamentos, devem permanecer separadas. Nao enviar
bancos locais, credenciais, PDFs de pacientes ou dados da demonstracao.
