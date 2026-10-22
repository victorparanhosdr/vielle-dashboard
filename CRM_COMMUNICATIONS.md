# CRM: Telefonia e Campanhas

CRM da Vielle liberado por `crm.view` e vinculo a clinica, conforme configuracao do Master. As clinicas e os institutos nao compartilham filas, clientes ou gravacoes. Operacoes de atendimento usam o nome do usuario autenticado; configuracoes de integracao continuam exclusivas do Master.

## Telefonia

- O botao de telefone no atendimento abre a ligacao. Ficha do cliente > Ligacoes mostra historico, atendente, origem, destino, horario de Sao Paulo, duracao, resultado e notas.
- Todas as chamadas desta fase sao de saida pelo navegador. Nao ha recebimento de ligacoes, discador automatico, transcricao ou IA de voz nesta entrega.
- A ligacao manual segue a disponibilidade de contato da clinica: nao exige um registro individual em `leads.consent_at`. Descadastros, vinculos em revisao e telefones invalidos continuam bloqueados, inclusive na revalidacao anterior a discagem. Essa disponibilidade operacional nao cria evidencia de consentimento nem autoriza a gravacao.
- No fluxo solicitado pelo Master em 19/10/2026 (`CRM_VOICE_NOTICE_MODE=staff`, padrao), a chamada entra diretamente, sem robo, pedido de tecla ou gravacao automatica. O colaborador informa sobre a gravacao e pede a concordancia verbal. Somente durante uma conversa conectada, o proprio atendente pode marcar a confirmacao e clicar em Iniciar gravacao. O audio comeca nesse momento, sem recuperar o trecho anterior.
- A confirmacao e individual por ligacao, nunca pre-marcada nem copiada de autorizacoes de contato/WhatsApp. O registro guarda fonte `staff_verbal`, atendente e horario; nao e apresentado como confirmacao direta do paciente por teclado. Falha ou timeout ao iniciar a gravacao nao derruba a conversa nem dispara outra tentativa automaticamente.
- O modo `automated` conserva o aviso por voz e a confirmacao por tecla 1. As chamadas antigas mantem esse modo; nenhuma autorizacao historica e reescrita. Em ambos os modos, o audio so pode ser arquivado depois da confirmacao registrada naquela chamada.
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
CRM_VOICE_NOTICE_MODE=staff
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
- A reproducao passa por sessao com CRM autorizado e verificacao de doutor; suporta Range, no-store e auditoria de acesso. Nao expomos RecordingUrl nem credenciais do provedor.
- Nao ha exclusao automatica. Definir politica de retencao, base legal, avisos, controle de acesso, protecao do volume, backups restauraveis e custos com o responsavel da clinica antes de uso real. A mensagem de consentimento nao substitui essa avaliacao.
- O backup deve incluir tanto o banco CRM quanto a pasta de gravacoes. Nao basta manter somente um deles. Monitorar espaco do volume e pendencias de download.

## Campanhas

### Local de Atendimento

- Campo persistido no contato unico do paciente, compartilhado entre funis e doutores da Vielle. Nao e escrito no Clinica Experts.
- Predefinicao operacional solicitada: DDD 33/38 = Capelinha, 31 = Belo Horizonte, 11 = Sao Paulo, apenas para telefones brasileiros completos. Nao representa comprovacao de residencia.
- Outros DDDs e telefones internacionais ficam sem local, com pendencia de definicao. A ficha permite escolher um dos locais ou reaplicar a regra do DDD; escolhas manuais nao sao sobrescritas por sincronizacao.
- Cartao compacto, filtro compartilhado por funis/Lista, atendimento e publico de campanhas, e contador de pendencias por funil. Alterar o local invalida revisoes de publico anteriores, sem iniciar ou cancelar campanhas existentes.

- Criacao em tres etapas: mensagem/expediente, publico, revisao. A campanha e salva como rascunho; nunca e iniciada pelo salvamento.
- Duplicar abre o assistente com texto, numero, intervalo, expediente, etiqueta e contatos anteriores para revisao. A copia nao herda status, IDs de mensagem, resultados nem inicio agendado. Salvar cria outro rascunho; nao retoma a original. O publico anterior e limitado a oportunidades ainda disponiveis e revalidado para descadastros, telefones e envios incertos no numero escolhido.
- Editar rascunho usa revisao otimista e substitui atomicamente apenas a fila nunca iniciada. Campanhas pausadas, iniciadas ou encerradas devem ser duplicadas para alterar texto/publico. Inicio com uma revisao antiga e rejeitado, evitando disparar um texto diferente do que foi conferido.
- A selecao e calculada no servidor para todos os resultados (ate 5000 oportunidades por campanha), nao apenas os primeiros 1000 cards. Funil, fase e filtros financeiros/agendamentos do CRM sao respeitados.
- A visao Lista dos funis permite selecionar por oportunidade, pagina ou todos os resultados do filtro, com exclusoes preservadas entre paginas. Criar disparo transfere exatamente essa selecao para o assistente; nao inicia envios.
- Somente o Kanban usa paginas de 20 oportunidades por fase. Os 20 cartoes ficam expostos, sem altura fixa nem rolagem vertical interna; a pagina cresce e carrega mais ao chegar ao fim da coluna. Contadores, valores e radar consideram todo o funil filtrado, sem limite de mil oportunidades. O cursor valida doutor, funil, fase, filtros, ordem e revisoes; se a fase mudar durante a navegacao, solicita atualizacao em vez de repetir ou omitir cartoes. Respostas de um contexto anterior sao descartadas. A atualizacao automatica nao reinicia colunas enquanto a equipe navega nelas. Lista, selecao de publico e disparos conservam a paginacao anterior.
- Lista e assistente usam a mesma consulta paginada de candidatos (50 por pagina). Contagens por funil/fase, busca e motivos de impedimento aparecem antes da revisao, inclusive quando nenhum contato esta apto. Esvaziar a selecao de fases significa nenhum resultado, nao todas as fases.
- Os cabecalhos ordenam cliente, funil/fase, total comprado, orcamentos pendentes e disponibilidade de envio. Valores numericos comecam pelo maior; outro clique inverte a ordem. A ordenacao e global, anterior a paginacao, preserva as selecoes e deixa valores ausentes/parciais no final em ambas as direcoes.
- Os funis preparados pelo bootstrap sao apenas uma copia de nomes; nao ha importacao implicita de leads antigos do Kommo.
- A migracao explicita de Organico Paranhos esta em `Importar Kommo`: uma previa completa atualiza os pacientes, le o funil e os contatos principais diretamente da API e conserva um relatorio paginado de pendencias. A confirmacao cria backup privado e aplica uma transacao atomica. Telefones completos e UUID de paciente evitam repeticoes; IDs de origem tornam novas execucoes idempotentes. A oportunidade mais recente da origem representa contatos repetidos, sem somar valores. Oportunidades existentes, descadastros e escolhas manuais nao sao sobrescritos. Sem telefone, fase ou identidade segura, o registro fica pendente. Nao importa conversas, anexos nem tarefas, nao envia mensagens e nao escreve no Kommo/Clinica Experts.
- As fotos do WhatsApp sao avatares, nunca evidencia de identidade para vincular pacientes. A busca acontece apenas para avatares visiveis, por contato/numero de atendimento/doutor, com cache diario e limite de concorrencia. A imagem e reduzida para 160 px, sem metadados, e servida por rota autenticada; URLs assinadas e chaves nao chegam ao navegador. Ausencia de foto conserva as iniciais. Remocao confirmada invalida o cache; falhas temporarias nao bloqueiam o atendimento. A lista do ponto de atendimento usa uma linha de 18 px para classificacao, Local de Atendimento e orcamentos pendentes do doutor, sem alterar o estado de leitura.
- Politica solicitada pelo Master em 19/10/2026: contatos atuais e novos ficam disponiveis para mensagens WhatsApp por padrao, sem autorizacao individual obrigatoria, tanto em campanhas quanto no atendimento manual. `consent_at` e `consent_source` continuam sendo evidencia opcional; nao sao preenchidos, inferidos nem migrados automaticamente. A permissao operacional nao e apresentada como consentimento do paciente.
- O servidor remove repeticoes por contato e exclui descadastrados e cadastros ambiguos das campanhas. Uma amostra de ate 100 destinatarios pode ser conferida e reduzida na revisao. SAIR/PARAR/STOP/CANCELAR e o descadastro manual continuam bloqueando envios em todos os doutores da clinica; cadastrar outra oportunidade ou receber uma nova mensagem nao reativa o contato.
- A ficha oferece `Reativar contato` somente para descadastrados. O Master informa o motivo e confirma que o cliente deseja voltar a receber contato. A alteracao individual vale para toda a clinica e registra autor, data e motivo nas oportunidades vinculadas, preservando o historico anterior. Revalida a revisao da ficha e invalida selecoes de campanha antigas; nao modifica evidencias anteriores de consentimento nem autorizacao de gravacao. Mensagens antigas permanecem nao enviadas, campanhas nao sao reiniciadas e nenhum envio e criado pela reativacao.
- Essa mudanca nao ativa campanhas, nao inicia filas, nao retoma mensagens antigas marcadas como nao enviadas e nao altera o consentimento de gravacao de chamadas VoIP.
- A selecao revisada expira em 30 minutos. Mudancas de telefone, nome, autorizacao, identidade ou oportunidade exigem nova revisao. Repetir o salvamento da mesma revisao nao cria outra campanha.
- Intervalo entre 30 segundos e 24 horas; dias selecionados; horario entre 08:00 e 20:00 em Sao Paulo; inicio agendado; etiqueta interna aplicada somente depois de o conector aceitar o envio.
- Toda mensagem inclui nome do usuario autenticado e instrucao de descadastro. Nao ha alteracao de etiquetas ou bots no Kommo nem escrita no Clinica Experts.
- A central mostra progresso, campanhas por status, volume diario e destinatarios com busca/paginacao. Aceita pelo conector nao significa entregue/lida: essas metricas nao sao inventadas.
- Envios desconhecidos pausam a campanha e nao sao reenviados automaticamente. Telefone alterado ou descadastro antes do envio retira o contato da fila. A pausa ou cancelamento nao pode recolher uma mensagem ja em envio.
- Reconectar conserva a campanha pausada e libera a revisao de retomada. Continuar processa apenas `queued`; mensagens `sent` nunca voltam a fila. Se houver `unknown`, a equipe deve confirmar que eles ficarao fora da retomada. A exclusao guarda autor/data (`automation_excluded_at/by`), mantem o status incerto e nao inventa entrega nem aceite. A fila pode terminar mesmo com esses registros incertos excluidos. O mesmo destinatario continua protegido contra repeticao automatica em outra campanha; atendimento manual de mensagens diferentes permanece independente.
- A central diferencia a conexao atual do motivo historico da pausa: depois da reconexao, exibe Numero conectado e identifica a desconexao como anterior, sem apagar o registro nem iniciar a fila. O botao Conferir conexao reconsulta somente o estado no conector; nao reconecta, nao gera QR e nao retoma envios. Falha na consulta nao confirma conexao nem libera impedimentos de envio.
- Quando a fila acabou, Concluir sem repetir permite resolver a pendencia incerta sem criar novos envios. Campanhas canceladas continuam canceladas. Envios em processamento, numero desconectado, worker indisponivel e pendencias de outra campanha recebem motivos explicitos na central; nunca sao ignorados para forcar retomada.
- O Master habilita campanhas na propria central. Essa ativacao pausa filas antigas e nao inicia campanhas; cada inicio ou retomada exige revisao e confirmacao na tela. Nao habilitar nem iniciar durante uma publicacao automatica.
- O worker segue no servidor com navegador fechado. O limite diario de cada numero e respeitado e compartilhado entre suas campanhas. Atendimento manual nao consome essa cota.
- Contencao temporaria do SQLite (`SQLITE_BUSY`/`SQLITE_LOCKED`) nao encerra o worker: o ciclo aguarda de 4 a 30 segundos, com parada interrompivel, e tenta novamente. Nao redefine mensagens em processamento nem incertas, nao retoma campanhas e conserva a reconciliacao existente. Outros erros de banco nao sao mascarados; o heartbeat expira se o worker nao consegue progredir.
- Limite solicitado pelo Master em 19/10/2026: 200 mensagens de campanha por dia por numero WhatsApp, com virada do dia em America/Sao_Paulo. Numeros novos usam 200 por padrao (pode-se escolher uma cota menor). A migracao eleva uma unica vez as cotas antigas inferiores a 200, sem zerar o consumo, alterar horarios, ativar campanhas ou reabrir descadastros.
- Nenhum intervalo ou limite garante evitar bloqueios numa integracao WhatsApp nao oficial. Nunca enviar mensagens nao solicitadas.

## Respostas Externas

- `messages.upsert` com `fromMe=true` e `send.message` sao capturados como saidas no mesmo contato/numero. Origem exibida como WhatsApp / sistema externo; nao inventamos o nome do usuario que respondeu fora do DOC4DOCS.
- O worker atualiza os eventos do webhook dos numeros conectados ao iniciar, sem gerar outro QR nem desconectar. Mantem o mesmo destino HTTPS e segredo; inclui SEND_MESSAGE.
- A abertura da conversa busca automaticamente ate os 200 registros mais recentes que a Evolution armazenou e recupera apenas as saidas ausentes daquele contato. Ha botao de sincronizacao e intervalo minimo de 30 segundos no servidor; repeticoes sao ignoradas pelo ID do provedor.
- O historico anterior depende do que estiver salvo no provedor. Nao habilitamos syncFullHistory nem prometemos recuperar mensagens que a Evolution nao possui. Os anexos comuns podem ser carregados pelo atendimento conforme descrito abaixo.
- Mensagens externas com data segura atualizam o status respondido somente para as entradas anteriores, naquele numero. Mensagens novas continuam pendentes; uma saida antiga nao resolve perguntas posteriores. Ausencia de data nao e tratada como prova de resposta.
- Ecos de envios DOC4DOCS usam o ID do provedor e a unica saida em processamento para evitar duplicacao. Campanhas proprias nao sao tratadas como resposta manual; pedidos de SAIR continuam bloqueados. A importacao nao envia mensagens nem cria campanhas.

## Midias No Atendimento

- Modelos de mensagens sao compartilhados pela equipe da Vielle (permissao CRM existente), com nome, texto e atalho unico que ignora acentos/caixa. O menu Modelos permite criar/editar, arquivar e restaurar. Edicoes guardam o usuario/data e usam revisao para impedir sobrescrita de outra sessao. Atalhos digitados no campo, como /endereco ou /endereço, abrem sugestoes; Enter/Tab ou clique insere somente no rascunho. {nome} usa o primeiro nome do contato aberto e {doutor} o doutor selecionado. Nao inventamos endereco nem criamos modelos clinicos iniciais. Nada e enviado sem o comando Enviar, e a assinatura autenticada continua aplicada pelo servidor. Limite de 500 modelos ativos/3000 caracteres; textos expandidos respeitam o limite atual da legenda. Auditoria nao inclui o conteudo da mensagem.

- A lupa no cabecalho busca em todo o texto de mensagens ja salvo naquela conversa e numero, com filtro Paciente/Equipe/Todos. A busca ignora caixa e acentos, navega em lotes de 50 sem limite de mil registros e abre ate 20 mensagens anteriores/posteriores ao resultado. Abrir/buscar nao resolve nao lidas; nao consulta o provedor, nao transcreve audio nem recupera historico que ainda nao foi sincronizado. Fechar a busca retorna ao trecho recente.

- O clipe permite selecionar imagem, video, documento ou audio do dispositivo. O microfone grava com permissao explicita do navegador, ate cinco minutos, com parar, ouvir e descartar. Selecionar/gravar faz somente um upload privado de rascunho; exige clicar em enviar para criar a mensagem.
- Limites por arquivo: imagem 10 MB, audio/video 16 MB, documento 25 MB. Validacao de extensao e assinatura de conteudo no servidor; nenhum URL externo fornecido pelo navegador e baixado. Um anexo por mensagem, legenda opcional, ate 1000 caracteres para imagem/video/documento.
- Imagens, videos e documentos levam a assinatura do atendente na legenda. Audio e enviado como nota de voz, precedido de uma mensagem assinada do atendente e da legenda opcional. A nota so entra na fila apos confirmacao do texto; apenas a nota aceita resolve as mensagens pendentes. Falhas incertas bloqueiam o canal, sem repeticao automatica.
- A legenda contem somente o texto digitado e a assinatura. Sem legenda, vai apenas a assinatura, sem rotulo de tipo ou nome de arquivo acrescentado ao texto. O nome original continua no anexo; a lista de conversas mostra o tipo de midia apenas na interface. Mensagens ja enviadas nao sao alteradas.
- Anexos recebidos e enviados externamente tem metadados privados vinculados ao contato e numero. `Carregar anexo` consulta o conector somente para aquela mensagem, confere o telefone e armazena o arquivo. Imagens possuem previa, audios e videos possuem player; documentos sao baixados como anexo. Abrir ou ouvir nao marca como respondida.
- Registros antigos do tipo [Imagem]/[Audio]/[Video]/[Documento] usam busca pelo ID exato no provedor. A sincronizacao enriquece entradas ja existentes sem importar entradas antigas novas nem multiplicar nao lidas. Arquivos expirados no WhatsApp podem ficar indisponiveis. Midias de visualizacao unica nao sao arquivadas.
- Arquivos em `crm/vielle/media/<uuid>`, fora de static, com diretorio 0700/arquivos 0600, rota autenticada com permissao CRM, verificacao de doutor, cache privado no-store e suporte Range. O navegador nunca recebe chaves de midia, URLs do provedor ou caminhos do servidor. Incluir a pasta e o SQLite nos backups privados.
- Rascunhos nao enviados expiram depois de 24 horas na proxima inclusao. Ha cota de 100 MB de rascunhos por usuario e 1 GB total para o piloto, com erro explicito se lotar; arquivos vinculados ao historico nao sao apagados automaticamente. Definir retencao e acompanhar o volume antes de ampliar a operacao.
- Os testes usam arquivos sinteticos e gateway simulado; nao enviam mensagens reais nem ativam microfone do usuario. Videos e audios devem usar codecs reproduziveis no navegador da equipe.

## Fontes

- https://www.twilio.com/docs/voice/sdks/javascript/twiliodevice
- https://www.twilio.com/docs/voice/twiml/number
- https://www.twilio.com/docs/voice/api/recording
- https://www.twilio.com/docs/usage/security
- https://github.com/evolution-foundation/evolution-api/blob/main/src/api/routes/chat.router.ts
- https://github.com/evolution-foundation/evolution-api/blob/main/src/api/services/channel.service.ts
- https://github.com/evolution-foundation/evolution-docs/blob/main/docs/02-Configuration/Webhooks.md

SDK JavaScript oficial vendorizado: `@twilio/voice-sdk` 2.18.5, com licenca em `static/twilio-voice.LICENSE.md`. SDK Python fixado em requirements.txt. Nenhuma chamada ou envio real faz parte dos testes automatizados.
