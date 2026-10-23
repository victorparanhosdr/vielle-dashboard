# Transcricao de audio no Atendimento

## Ativacao e escopo

Recurso do CRM Vielle por profissional, incluindo seus numeros. Inicia
desligado e nao varre historico na publicacao ou ativacao. Numeros > Transcricao
de audios abre configuracao exclusiva do Master. Ativacao exige confirmacao
de envio da fala a OpenAI e consumo da API. Usa configuracao privada existente,
incluindo OPENAI_TRANSCRIBE_MODEL. Nao cria credenciais nem amplia permissoes.

Apos ativacao, cadastra audios recebidos por eventos vivos do WhatsApp, audios
externos enviados e anexos aceitos pelo conector no CRM. Sincronizacao historica
e importacao nao cadastram transcricoes. Duplicatas de webhook nao criam outro
trabalho. Rascunhos e campanhas somente publicadas nao geram processamento.

## Leitura e monitoria

Audio original permanece disponivel. Texto automatico expansivel aparece junto
a mensagem, com aviso de possiveis erros e botao copiar. Preserva autoria,
direcao e horario dos metadados; nunca identifica pessoas pela voz.

Leitura reutiliza permissoes de clinica, profissional, funil, instituto e
conversa. Atendente nao recebe acesso a notas, painel da monitoria, configuracao,
lotes ou tentativas pagas. Abrir conversa nunca chama OpenAI. Cache privado por
anexo, hash e modelo e compartilhado com a monitoria nos dois sentidos. Avaliacao
da monitoria continua manual, com autorizacao e consumo de texto separados.

## Fila e consumo

Worker separado dos envios WhatsApp, reutilizando FFmpeg e provider existentes.
Limites: dez minutos por arquivo e dezesseis MB no CRM. Oito audios e limite do
recorte da monitoria, nao da fila de atendimento. Confere arquivo, hash, mensagem,
conversa e escopo antes de processar.

Limite diario por profissional: sessenta minutos por padrao, editavel de um a
mil quatrocentos e quarenta. Dia em America/Sao_Paulo. Duracao real do WAV,
arredondada em segundos, e reservada atomicamente antes da chamada. Cache
concluido nao usa reserva. Falhas e chamadas incertas mantem a reserva; painel
mostra minutos reservados, nao fatura em dinheiro nem uso confirmado pelo
fornecedor. O limite nao abrange analises manuais da monitoria.

Saldo diario restante insuficiente aguarda proximo dia sem chamada paga. Audio
maior que o teto diario completo exige revisao humana. Falha, timeout, arquivo
ausente e trabalho interrompido nunca repetem automaticamente. Master pode
autorizar nova tentativa, inclusive novo consumo. Cache impede chamadas
concorrentes para a mesma versao de audio.

Salvar configuracao ou desligar cancela pendencias e trabalhos em andamento,
sem apagar transcricoes concluidas. Resultado tardio apos cancelamento ou
revogacao nao e aproveitado; chamada que ja saiu pode ter consumo. Autorizador
precisa continuar Master ativo. Mudanca de dono/escopo invalida a pendencia.
Transcricao nao envia mensagens ao paciente nem movimenta funil.

## Historico separado

Master escolhe periodo e lote de ate cem audios. Previa informa quantidade e
limite superior de minutos antes de confirmar; nao baixa arquivos nem chama
OpenAI. IDs congelados por dez minutos, vinculados ao autor, profissional e
revisao da configuracao. Aplicar exige confirmacao especifica de consumo, e e
idempotente. Alvos sao revalidados e respeitam o mesmo teto diario. Audio que ja
possui trabalho nao e duplicado; falhas usam nova tentativa.

## Verificacao

Somente dados sinteticos e providers simulados nos testes. Fixture local
`tests/serve_crm_preview.py --attendance-audio-fixture` usa WAV sintetico e
transcritor deterministico, sem worker real ou processamento pago.
Testes cobrem opt-in, limites, cache, permissoes, escopo, cancelamento,
historico separado, estados e escape do texto. Precisao semantica precisa de
calibracao posterior autorizada; nomes, valores e termos clinicos podem exigir
conferencia no audio original.
