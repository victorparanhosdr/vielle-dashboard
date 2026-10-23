# Follow up do CRM

Menu dedicado por profissional/funil clinico. Cursos, institutos e RegenCode
nao recebem esta regua. Uma unica politica por funil, ate vinte tentativas;
rascunho inicial com quatro. Nenhuma politica e criada ou ligada por leitura,
publicacao, importacao ou migracao. O produto e entregue desligado.

## Configuracao

Textos, ordem e intervalos editaveis; campos {nome} e {clinica}. Sem rodape
fixo ou assinatura que simule autoria humana. Autoria de automacao permanece
registrada no historico do CRM. Pedidos de parada e consentimento continuam
obrigatorios. Nenhuma promessa de evitar bloqueios por definir intervalos.

Intervalos entre tentativas: 2h, 22h, 2 dias e 4 dias. Referencias acumuladas
minimas de 2h, 24h, 3 dias e 7 dias. Se uma tentativa atrasar por expediente,
quota ou fila, o intervalo seguinte parte do aceite real, sem comprimir atraso.
Uma a vinte tentativas, quinze minutos a trinta dias por intervalo e no maximo
180 dias no conjunto. Vinte e capacidade de configuracao, nao publico ou volume
de envio automaticamente autorizado.

Dias da semana, inicio/fim e pausa diaria no fuso America/Sao_Paulo. Intervalo
de automacoes por numero configuravel em minutos, padrao 3. Previa de tres
horarios e somente simulacao; nao reserva fila ou inclui pacientes reais.

Master edita e liga/desliga com confirmacao e revisao otimista. Equipe com
acesso ao funil pode consultar. Regua ligada precisa ser desligada antes de
editar. Desligar cancela tentativas pendentes; ligar novamente nao recupera
historico ou filas interrompidas. Auditoria preserva configuracao e autor.

## Entrada explicita e contexto

Somente nova resposta manual nativa com Acompanhar se ficar sem resposta pode
iniciar um ciclo depois do aceite do conector. A partir da revisao de pausa
individual, a opcao ja vem marcada nos atendimentos elegiveis. A equipe pode
desmarcar para uma resposta; depois do envio o padrao e restaurado. O controle
aparece apenas se a regua estiver ligada, a etapa for elegivel e o contato
nao estiver pausado. Sem metadados de pausa carregados, nao presume liberacao.
Mensagens de campanha, robo, provedor externo ou historico importado nao
iniciam ciclos. Anexos nao iniciam acompanhamento generico. Uma resposta de
despedida com a opcao desmarcada nao dispara follow-up. Marcacao nao substitui
autorizacao de contato documentada na ficha.

Pausar follow-ups fica na ficha lateral do atendimento e na ficha completa.
Pausa persistente por contato, compartilhada pelos funis clinicos, com revisao
otimista e auditoria. Nao descadastra a pessoa, nao bloqueia respostas manuais
ou campanhas e nao altera consentimento. Contato compartilhado com escopos
fora do acesso exige Master; o controle nao concede novas permissoes.

Pausar interrompe os ciclos e suas mensagens ainda na fila. Remover a pausa
nao retoma ciclos antigos nem inclui respostas humanas enfileiradas antes da
alteracao: reply_requests congela tambem a revisao da preferencia. Uma nova
resposta do cliente permite um novo turno elegivel. Claim e preflight conferem
a pausa novamente, inclusive quando a interface do atendente esta desatualizada.
Mensagem que ja iniciou chamada externa pode nao ser recolhida.

Unicidade por contato/turno e um ciclo ativo por contato impedem concorrencia
entre funis e profissionais. Fragmentos humanos do mesmo turno nao zeram as
tentativas. Um ciclo concluido nao reinicia pelo mesmo silencio. Pacientes em
tratamento nao voltam de etapa nem recebem cobranca generica de novo plano.

Resposta do cliente, descadastro, encerramento, mudanca de etapa/escopo/telefone,
consolidacao e conexao removida interrompem. Desconexao pausa sem retomada por
reconexao. Consentimento ausente, identidade pendente, multiplos contextos,
agenda/retorno combinado, tarefa pendente, midia, reclamacao, sinal clinico ou
envio incerto pedem equipe. Deteccao textual e conservadora, nao e IA nem
interpretacao clinica completa. Nao ha chamadas pagas de IA.

## Fila e durabilidade

Reutiliza messages, claim e worker existentes; nao ha segundo conector. Uma
reserva por ciclo/tentativa. Claim e preflight revalidam permissao atual do
Master ativador e do atendente, telefone, contexto, politica e janela.
Automacoes compartilham quota e next_send_at do numero; resposta manual tem
prioridade e nao herda o intervalo. Quando ha politicas ligadas no profissional,
vale o maior intervalo aplicavel entre elas e a automacao atual, por conexao.
Uma tentativa incerta pausa seu contato, sem repetir ou bloquear outras
conversas manuais; outro contato segue respeitando espacamento e quota.

Na abertura do expediente, pendencias usam claim serializado em SQLite e
next_send_at: por exemplo 09:00, 09:03, 09:06. Nao ha rajada de retomada.
Timeout ou claim abandonado continua unknown, sem retry automatico. Savepoint
isola planejamento posterior do aceite real do provider. Uma resposta recebida
antes do ultimo preflight cancela a tentativa; depois de iniciar a chamada
externa nao e possivel prometer recolher mensagem ja aceita pelo WhatsApp.

Nao move fases, cria vendas financeiras, altera cursos, retoma campanhas ou
importa pacientes. Permissoes e CSRF sao verificados no servidor. Ativacao real
depende de canal configurado, consentimento e regras aplicaveis do WhatsApp;
fora da janela da API oficial, modelos aprovados continuam necessarios.

## Arquivos e validacao

crm_followup_settings.py, crm_followup_settings_api.py, crm_followup_runtime.py,
crm_store.py, crm_worker.py, crm_inbox.py, static/crm-followups.js e CSS.
Testes sinteticos de configuracao, concorrencia, ativacao, cancelamento,
preflight, incerteza, espacamento, retomada de expediente, autoria e permissao.
Provedores simulados; nenhum envio real e necessario para testar a entrega.

Validacao de entrega em 23/10/2026: 560 testes Python do CRM e 67 testes HTTP
aprovados. Trinta suites JavaScript do CRM passaram; a assercao textual antiga
de test_crm_facebook_ui.cjs falha tambem na revisao-base, sem alteracao desse
comportamento nesta entrega. Sintaxe JavaScript e diff sem erros. Teste visual
no navegador em desktop e larguras de 320 e 390 pixels, sem overflow; salvar,
limite de vinte, simulacao e ligar/desligar conferidos com dados sinteticos.
O teste Playwright legado separado requer dependencia nao instalada; a
verificacao visual foi realizada pela sessao de navegador controlada.

Revisao de pausa individual: 571 testes CRM e 68 HTTP aprovados; 31 suites
JavaScript do CRM aprovadas (mesma assercao legada de Facebook excluida).
Previa sintetica desktop/mobile: default marcado, desmarcacao pontual, retorno
ao padrao na proxima mensagem, pausa persistente apos recarregar, ficha completa
e lateral sincronizadas, sem overflow em 390 pixels. Worker e provedor reais
ausentes da previa. Nenhuma regua de producao e ativada por esta atualizacao.
