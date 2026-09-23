# Clinica Brandao

- Chave interna: `brandao`; nome exibido: Clinica Brandao (com acentos na interface).
- Entrada: `/?clinic=brandao`; configuracoes: `/settings.html?clinic=brandao`.
- Banco proprio: `DATA_DIR/kommo_report_brandao.sqlite3`, inicializado pelo servidor.
- Sem copiar dados, tokens, pacientes, acompanhamentos ou credenciais de outras clinicas.
- Nenhuma migracao de schema nova. Os bancos existentes permanecem separados.
- Master possui acesso; usuarios comuns precisam de clinica e permissoes explicitamente liberadas.
- Modulos: painel geral, comercial, financeiro, orcamentos, trafego e avaliacao WhatsApp.
- Acompanhamento de Paciente permanece exclusivo da Vielle; Evolucao corporal, da Inspire.
- Kommo e Clinica Experts ficam sem conexao ate a configuracao posterior pelo Master.
- Variaveis especificas usam o prefixo `BRANDAO_`; nenhum segredo foi adicionado ao codigo.

## Verificacao

`python -m unittest tests.test_brandao tests.test_auth_http -v`

`node tests/test_master_clinic_modules.cjs`

Os testes usam bancos temporarios e bloqueiam requisicoes externas. Incluem o reset de
inicializacao do Kommo: os valores padrao sao isolados por clinica antes de qualquer
retorno antecipado, evitando que uma clinica nova herde credenciais globais.

No navegador, entrar como Master, abrir o seletor, acessar Brandao e conferir os
campos de integracao vazios. No Master, conferir a nova clinica e suas abas disponiveis.

Publicacao depende de autorizacao. O `app.py` contem tambem alteracoes locais de
orcamentos, alheias a este cadastro, que nao devem entrar no mesmo commit.
