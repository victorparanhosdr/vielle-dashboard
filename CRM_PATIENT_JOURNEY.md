# Jornada da clinica por paciente

## Escopo aprovado

Um cartao por contato em cada funil da clinica. Novas negociacoes sao registros
filhos na ficha, nao novos pacientes/cartoes. Quem ja contratou permanece em
Pacientes quando surge uma nova negociacao. RegenCode, cursos e institutos nao
recebem este modelo. Nenhuma analise paga de IA integra a migracao.

Etapas: Novo contato, Em atendimento, Avaliacao agendada, Consulta realizada,
Negociacao de Orcamento e Pacientes. Encerrado sem contratacao e saida alternativa;
Nao comercial e uma classificacao separada. O tipo legado de armazenamento de
etapa terminal nao define sozinho perda: a disposicao nao comercial fica fora
do numerador e denominador de conversao, perdas e valores previstos do CRM.
Vendas reais da base clinica e historico de atendimento nao sao apagados.

## Encerramentos

Motivos comerciais: valor, pagamento, desistencia, outra clinica declarada,
agenda, localizacao, servico nao oferecido, falta de indicacao validada pelo
profissional, sem retorno apos tentativas e outro. Outro, sem retorno e falta
de indicacao exigem observacao; os dois ultimos exigem confirmacao especifica.
Nao comercial: equipe/contato interno, fornecedor/vendedor e parceiro/prestador.
Uma mesma pessoa pode ter outro contexto comercial valido; a marcacao e por
cartao/funil, nunca um bloqueio global por nome, profissao ou conta de usuario.

## Migracao explicita, sem replay

Master gera previa por funil na acao Modelo da clinica. Previa vincula autor,
profissional, revisoes e plano de movimento; expira em quinze minutos. Aplicacao
revalida o plano sob BEGIN IMMEDIATE, salva backup privado completo e mantem
originais por linha em clinic_migration_rows. Recibo permite retry idempotente.
As etapas antigas sao mantidas fisicamente e arquivadas no catalogo ativo.
IDs de pacientes/cartoes, datas originais, conversas, consentimentos, lembretes,
orcamentos, vendas e eventos anteriores permanecem. Lixeira continua lixeira.

Regras de mapeamento usam etapa anterior e evidencias seguras do profissional:
venda valida -> Pacientes; orcamento pendente -> Negociacao; avaliacao/consulta
confirmada ou realizada identificavel -> etapa correspondente. Agendamentos
genericos, historico parcial ou identidade ambigua nao viram fatos inventados.
Virgem -> Novo contato. BOT, Lead Morto e objecoes sem evidencia suficiente ->
Em atendimento, com revisao pendente e etapa anterior preservada. Perda legada
sem motivo tem classificacao historica pendente, nao recusa fabricada.

Robos ativos antigos do funil ficam pausados, execucoes antigas sao interrompidas
e eventos antigos consumidos sem novos disparos. Campanhas nao sao iniciadas,
retomadas ou trocadas por esta migracao. Entradas WhatsApp vinculadas ao funil
sao direcionadas ao Novo contato, sem alterar numero ou estado da conexao.
Formularios publicados com IDs arquivados entram na nova etapa inicial;
importacoes posteriores reutilizam o modelo, sem recriar colunas antigas.

## Oportunidades internas

Tabela aditiva patient_opportunities ligada ao lead unico; eventos proprios,
revisao otimista, autoria, criacao explicita e request_id idempotente. Sem criar
mensagem, venda financeira, XP ou gatilho de movimento. O contexto comercial do
cartao anterior e preservado uma vez como legacy_card; nao equivale a importar
uma nova transacao financeira. Vendas e orcamentos Experts continuam em suas
abas de origem. Estimativas CRM nao devem ser somadas como faturamento.
Encerrar uma oportunidade filha nao encerra o paciente nem outras negociacoes.
Marcacao nao comercial exclui previsao, sem apagar filhos ou historico; criacao
de nova negociacao exige reclassificar o contexto como comercial.

## Permissoes e recuperacao

Operacao de migracao exclusiva do Master com CSRF. Leitura e edicao de ficha,
motivos e oportunidades seguem escopo de clinica, profissional, funil e instituto.
Restaurar backup inteiro sobre novas escritas nao e um rollback seguro. A base
original e os snapshots de linha permitem preparar uma reversao especifica,
com manutencao e revisao das alteracoes posteriores, sem apagar mensagens novas.

## Verificacao

Testes unitarios sinteticos em tests/test_crm_clinic_journey.py; fronteiras HTTP
em tests/test_auth_http.py. Fluxo real de interface em
tests/check_crm_patient_journey.cjs: previa/aplicacao, oportunidade nova e encerrada,
cartao conservado em Pacientes, classificacao nao comercial, curso preservado e
capturas desktop/mobile. Preview usa bancos temporarios e envios desabilitados.
Publicacao e aplicacao real exigem evidencia separada, nao apenas testes ou push.
