# Monitoria nativa: piloto manual

## Escopo da publicacao

Entrega isolada da monitoria, sem as evolucoes locais de oportunidades,
encerramentos ou follow-up. A publicacao do codigo nao autoriza analises:
a configuracao por profissional inicia desligada e exige confirmacao do Master.
O piloto aprovado usa ate 20 analises novas por dia e profissional, no fuso
America/Sao_Paulo. Nenhuma analise e iniciada ao importar, publicar, receber
mensagens ou fechar conversas. Selecao e processamento automaticos nao fazem
parte desta versao; o backend rejeita sua ativacao.

## Regua aprovada

Dezessete criterios das capturas do formulario. Nota inicial 100, piso zero,
descontando cada criterio marcado uma vez:

- Gravissimas, 100: prontidao; continuidade; ortografia e profissionalismo.
- Graves, 50: confidencialidade; cordialidade; empatia em reclamacoes;
  conhecimento do servico; resolucao; expectativas; pontualidade no follow-up.
- Moderadas, 30: informacao completa; simpatia; clareza; scripts;
  personalizacao; eficiencia; flexibilidade.

Tempo de resposta e resolucao numerica permanecem desativados. Nao penalizar
quantidade de mensagens ou turnos, nem usar essa contagem como substituto de
eficiencia. Conhecimento e scripts dependem de referencias da clinica.

Precos, valores, taxas, descontos, creditos, isencoes e elegibilidade financeira
ficam fora de todos os criterios. Campanhas variam e podem ser isentas. Essa
exclusao prevalece sobre referencias antigas. Se apenas o aspecto financeiro
for avaliavel, usar na. Cordialidade e clareza continuam avaliaveis por evidencias
proprias nao financeiras. Sem contexto suficiente, usar na, sem desconto ou
acerto inventado. Uma regua inteiramente na nao recebe nota 100.

## Fonte e autoria

Recorte nativo dos ultimos sete dias, ate 250 mensagens e 45 mil caracteres,
por profissional, numero e contato. Repeticoes reais sao preservadas.
Somente mensagens humanas aceitas pelo conector e com usuario identificado
sao atribuidas ao atendente. Bots, campanhas, cliente e autoria desconhecida
nao sao atribuicoes humanas. Aceite nao comprova entrega ou leitura.
Audios e anexos nao sao analisados; contexto ausente nao comprova falha.

A base legada Amanda foi adaptada pelo gestor para referencia de atendimento
humano do profissional de origem, sem herdar a identidade da assistente,
roteiros literais ou cadencias antigas. Em retornos e continuidade de tratamento,
nao reiniciar o discurso de captacao. Referencias de procedimentos sao somente
comerciais: nao validam diagnostico, prescricao, indicacao ou eficacia clinica.
Nao aplicar automaticamente a outros profissionais.

## Solicitacao, processamento e revisao

O Master habilita o piloto no profissional desejado e escolhe a conversa.
Cada solicitacao exige confirmacao do processamento dos textos pela OpenAI
e consumo da API. A configuracao reutiliza chave e modelo ja existentes da
clinica; segredos nao sao armazenados na fila.

Nomes e telefones da ficha nao compoem o payload. Autores e IDs sao pseudonimos;
formatos comuns de telefone/email sao reduzidos no texto. Texto livre ainda
pode conter dados pessoais ou clinicos: nao e anonimato completo.
Mensagens, scripts e referencias sao dados nao confiaveis, nunca comandos.

O servidor valida regua, autoria e evidencias antes de calcular nota. Todo
desconto precisa de mensagem humana do proprio atendente. Falha ou resposta
invalida gera erro sem nota e sem retry automatico. Tentativa com falha conta
no limite diario; retry exige confirmacao nova e identificador idempotente.
Repetir solicitacao do mesmo contexto nao duplica processamento.

Fila SQLite duravel, sem transacao durante chamada externa. Revogacao do
Master, alteracao da regua, transferencia do numero ou consolidacao da
identidade cancelam resultados desatualizados. Chamadas externas ja iniciadas
nao podem ser desfeitas. Trabalhos interrompidos falham sem repeticao automatica.

Notas sao provisorias ate revisao do Master. Pode-se manter ou desconsiderar
descontos com justificativa obrigatoria, sem alterar o original. Revisoes tem
controle de concorrencia e auditoria. Apenas notas revisadas entram na media.
A media do piloto manual nao e amostra representativa da equipe e nao e prova
de produtividade. Nao ha decisao automatica de remuneracao ou emprego.

## Acesso

Painel e API exclusivos do Master. Clinic, profissional, sessao e origem sao
validados no servidor. A versao nativa atual esta restrita ao CRM Vielle.

Promocao de outro usuario a Master ocorre em rota especifica com confirmacao,
login esperado e Master ativo validados novamente na transacao. Acesso total
a todas as clinicas e configuracoes. Promocao auditada, sem trocar senha ou
identidade, encerra as sessoes anteriores do promovido e o classifica como
Gestao. Editar usuario ou criar conta comum nao promove implicitamente.

## Verificacao

- tests/test_crm_quality.py: score, autoria, evidencias, regua, exclusoes,
  permissoes, revogacao, idempotencia, limite e nenhum processamento automatico.
- tests/test_crm_quality_ui.cjs: leitura sem efeito, escape, respostas tardias,
  painel manual, consumo diario e media revisada.
- tests/test_user_admin.py e tests/test_auth_http.py: promocao explicita,
  origem, conta alvo, auditoria e encerramento de sessoes.
- tests/serve_crm_preview.py --quality-fixture: somente dados ficticios e provider
  deterministico, sem chamadas pagas ou mensagens externas.

Precisao semantica real depende de calibracao posterior com amostra autorizada.
Teste sintetico comprova contratos do software, nao a precisao clinica da IA.
