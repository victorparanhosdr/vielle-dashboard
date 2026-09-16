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

Quatro tabelas novas sao criadas de forma idempotente no primeiro acesso ao
modulo, no banco **existente da Inspire**, dentro do `DATA_DIR` configurado:

- `body_patients`: vinculo com Clinica Experts e nome de contingencia.
- `body_evaluations`: medidas, data do exame, origem, profissional e versao.
- `body_documents`: PDF original como BLOB, hash SHA-256 e extracao original.
- `body_revisions`: autor, momento e snapshots de criacao/correcao.

Nenhuma tabela anterior e removida, renomeada ou limpa. Sincronizar o catalogo
nao apaga avaliacoes. O backup SQLite da Inspire deve incluir estas tabelas e
documentos; usar backup consistente do SQLite, incluindo o estado WAL.

Permissoes no catalogo central existente:
`body_evolution.view`, `.create`, `.edit`, `.export`.
Master tem acesso. Usuarios comuns existentes nao recebem liberacao automatica:
o Master deve habilitar as acoes na Inspire. O painel e os perfis existentes
utilizam o novo modulo sem uma segunda estrutura de autorizacao. Outras clinicas
nao o oferecem e suas rotas sao bloqueadas pelo backend.

## Testar localmente

Usar um ambiente com `requirements.txt` instalado. Nunca apontar a demonstracao
para bancos de producao.

```sh
python3 -m unittest tests.test_body_evolution -v
python3 -m unittest discover -s tests
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

Frontend servido: `static/body-evolution.html`, `.css`, `.js`,
`static/body-mannequin.png`, `static/body-icons.js` e sua licenca; integracao
em `static/index.html`, `static/app.js`, `static/login.js`, `static/session.js`.

Testes: `tests/test_body_evolution.py`, `tests/serve_body_preview.py`.

Publicar este modulo somente apos autorizacao. Alteracoes de outras tarefas,
como acompanhamento de orcamentos, devem permanecer separadas. Nao enviar
bancos locais, credenciais, PDFs de pacientes ou dados da demonstracao.
