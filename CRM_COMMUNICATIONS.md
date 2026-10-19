# CRM: Telefonia e Campanhas

Piloto exclusivo do Master da Vielle. As clinicas e os institutos nao compartilham filas, clientes ou gravacoes.

## Telefonia

- O botao de telefone no atendimento abre a ligacao. Ficha do cliente > Ligacoes mostra historico, atendente, origem, destino, horario de Sao Paulo, duracao, resultado e notas.
- Todas as chamadas desta fase sao de saida pelo navegador. Nao ha recebimento de ligacoes, discador automatico, transcricao ou IA de voz nesta entrega.
- O cliente ouve o aviso e precisa pressionar 1. A gravacao so e iniciada depois disso. Se iniciar a gravacao falhar, a conversa nao e conectada.
- IDs do cliente, contato, doutor, paciente do Experts, numero e atendente sao congelados no registro. Alteracoes posteriores nao reescrevem essa identidade historica.
- Webhooks exigem assinatura Twilio, conta correta e vinculo ao CallSid. Um token de navegador vale 120 segundos e permite somente a tentativa preparada. O cliente nao escolhe o numero de destino por parametro.
- O numero de origem precisa pertencer a conta Twilio e ter capacidade Voice. Somente destinos brasileiros sao aceitos nesta fase.
- Limites padrao: uma chamada ativa por atendente, cinco simultaneas no piloto, 100 tentativas/dia e 20 minutos por ligacao. Os limites nao representam garantia de custo maximo: minutos, numeros, gravacao e armazenamento podem ser cobrados pela Twilio.

### Configuracao final no servidor

As credenciais nao entram no JavaScript, Git ou chat. Configure diretamente nas variaveis protegidas da Railway:

```
TWILIO_ACCOUNT_SID
TWILIO_AUTH_TOKEN
TWILIO_API_KEY_SID
TWILIO_API_KEY_SECRET
TWILIO_TWIML_APP_SID
CRM_VOICE_PUBLIC_URL=https://doc4docs.com.br
CRM_VOICE_ENABLED=0
CRM_VOICE_MAX_SECONDS=1200
CRM_VOICE_DAILY_LIMIT=100
```

`CRM_VOICE_LINES_JSON` e uma lista de numeros de voz por doutor, separados dos canais de WhatsApp:

```json
[{"id":"atendimento-victor","doctor_id":"ID_INTERNO_DO_DOUTOR","name":"Atendimento Dr. Victor","number":"+55DDDNUMERO"}]
```

Configure o Voice Request URL do TwiML App, metodo POST, para:
`https://doc4docs.com.br/api/crm-voice-webhook/twiml`

Os callbacks de status, aviso, consentimento e gravacao sao preenchidos pelo servidor. Nao exigem login de navegador, mas nunca aceitam requisicoes sem assinatura valida.

So depois de aprovar/comprar o numero, conferir custos e configurar as credenciais, habilite `CRM_VOICE_ENABLED=1`. A chave API deve ser da mesma conta. Validar uma chamada consentida em desktop e no dispositivo de uso da equipe antes de liberar para operacao.

### Arquivo privado das gravacoes

- Metadados: `crm/vielle/crm.sqlite3`, tabela `voice_calls`.
- Copia local MP3: `crm/vielle/voice-recordings/<id>.mp3`, fora de `static`, diretorio privado e arquivos 0600.
- A fila de download roda em uma thread separada, independente do worker WhatsApp. Falhas de rede tem ate cinco tentativas, com espera progressiva; arquivos interrompidos sao recuperados depois de reiniciar.
- Callback ausente pode ser reconciliado pelo RecordingSid conhecido. Falhas definitivas aparecem como gravacao a conferir, nao como audio disponivel.
- A reproducao passa por sessao Master e verificacao de doutor; suporta Range, no-store e auditoria de acesso. Nao expomos RecordingUrl nem credenciais do provedor.
- Nao ha exclusao automatica. Definir politica de retencao, base legal, avisos, controle de acesso, protecao do volume, backups restauraveis e custos com o responsavel da clinica antes de uso real. A mensagem de consentimento nao substitui essa avaliacao.
- O backup deve incluir tanto o banco CRM quanto a pasta de gravacoes. Nao basta manter somente um deles. Monitorar espaco do volume e pendencias de download.

## Campanhas

- Criacao em tres etapas: mensagem/expediente, publico, revisao. A campanha e salva como rascunho; nunca e iniciada pelo salvamento.
- A selecao e calculada no servidor para todos os resultados (ate 5000 oportunidades por campanha), nao apenas os primeiros 1000 cards. Funil, fase e filtros financeiros/agendamentos do CRM sao respeitados.
- A visao Lista dos funis permite selecionar por oportunidade, pagina ou todos os resultados do filtro, com exclusoes preservadas entre paginas. Criar disparo transfere exatamente essa selecao para o assistente; nao inicia envios.
- Lista e assistente usam a mesma consulta paginada de candidatos (50 por pagina). Contagens por funil/fase, busca e motivos de impedimento aparecem antes da revisao, inclusive quando nenhum contato esta apto. Esvaziar a selecao de fases significa nenhum resultado, nao todas as fases.
- Os cabecalhos ordenam cliente, funil/fase, total comprado, orcamentos pendentes e disponibilidade de envio. Valores numericos comecam pelo maior; outro clique inverte a ordem. A ordenacao e global, anterior a paginacao, preserva as selecoes e deixa valores ausentes/parciais no final em ambas as direcoes.
- Os funis preparados pelo bootstrap sao apenas uma copia de nomes; nao ha importacao implicita de leads antigos do Kommo.
- Politica solicitada pelo Master em 19/10/2026: contatos atuais e novos ficam disponiveis para mensagens WhatsApp por padrao, sem autorizacao individual obrigatoria, tanto em campanhas quanto no atendimento manual. `consent_at` e `consent_source` continuam sendo evidencia opcional; nao sao preenchidos, inferidos nem migrados automaticamente. A permissao operacional nao e apresentada como consentimento do paciente.
- O servidor remove repeticoes por contato e exclui descadastrados e cadastros ambiguos das campanhas. Uma amostra de ate 100 destinatarios pode ser conferida e reduzida na revisao. SAIR/PARAR/STOP/CANCELAR e o descadastro manual continuam bloqueando envios em todos os doutores da clinica; cadastrar outra oportunidade ou receber uma nova mensagem nao reativa o contato.
- Essa mudanca nao ativa campanhas, nao inicia filas, nao retoma mensagens antigas marcadas como nao enviadas e nao altera o consentimento de gravacao de chamadas VoIP.
- A selecao revisada expira em 30 minutos. Mudancas de telefone, nome, autorizacao, identidade ou oportunidade exigem nova revisao. Repetir o salvamento da mesma revisao nao cria outra campanha.
- Intervalo entre 30 segundos e 24 horas; dias selecionados; horario entre 08:00 e 20:00 em Sao Paulo; inicio agendado; etiqueta interna aplicada somente depois de o conector aceitar o envio.
- Toda mensagem inclui nome do usuario autenticado e instrucao de descadastro. Nao ha alteracao de etiquetas ou bots no Kommo nem escrita no Clinica Experts.
- A central mostra progresso, campanhas por status, volume diario e destinatarios com busca/paginacao. Aceita pelo conector nao significa entregue/lida: essas metricas nao sao inventadas.
- Envios desconhecidos pausam a campanha e nao sao reenviados automaticamente. Telefone alterado ou descadastro antes do envio retira o contato da fila. A pausa ou cancelamento nao pode recolher uma mensagem ja em envio.
- O Master habilita campanhas na propria central. Essa ativacao pausa filas antigas e nao inicia campanhas; cada inicio ou retomada exige revisao e confirmacao na tela. Nao habilitar nem iniciar durante uma publicacao automatica.
- O worker segue no servidor com navegador fechado. O limite diario de cada numero e respeitado e compartilhado entre suas campanhas. Atendimento manual nao consome essa cota.
- Limite solicitado pelo Master em 19/10/2026: 200 mensagens de campanha por dia por numero WhatsApp, com virada do dia em America/Sao_Paulo. Numeros novos usam 200 por padrao (pode-se escolher uma cota menor). A migracao eleva uma unica vez as cotas antigas inferiores a 200, sem zerar o consumo, alterar horarios, ativar campanhas ou reabrir descadastros.
- Nenhum intervalo ou limite garante evitar bloqueios numa integracao WhatsApp nao oficial. Nunca enviar mensagens nao solicitadas.

## Fontes

- https://www.twilio.com/docs/voice/sdks/javascript/twiliodevice
- https://www.twilio.com/docs/voice/twiml/number
- https://www.twilio.com/docs/voice/api/recording
- https://www.twilio.com/docs/usage/security

SDK JavaScript oficial vendorizado: `@twilio/voice-sdk` 2.18.5, com licenca em `static/twilio-voice.LICENSE.md`. SDK Python fixado em requirements.txt. Nenhuma chamada ou envio real faz parte dos testes automatizados.
