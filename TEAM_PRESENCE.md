# Presenca E Colaboracao Da Equipe

- Widget lateral fixo nos computadores, nas telas autenticadas de uma clinica selecionada. Acompanhamento entre abas, paginas e dispositivos sem somar tempo em dobro na mesma clinica.
- Colegas veem somente nome, estado atual (atividade, ocioso ou offline), duracao do estado e pedidos pessoais de ajuda. Sem login, e-mail, relatorio ou tempos acumulados na API de presenca.
- Relatorio em `/team.html?clinic=vielle`, com dia, sete ou trinta dias, jornada e entradas/saidas. Requer a permissao `team.view` da clinica em todas as rotas, inclusive na chamada direta da API. Master tem acesso; demais gestores devem receber **Gestao da equipe > Visualizar** no cadastro de usuarios. Permissoes existentes nao sao ampliadas na migracao.
- Os pedidos de ajuda sao persistentes, pessoais e limitados a colegas online da mesma clinica. Podem ser aceitos, dispensados e encerrados; novas solicitacoes aparecem tambem no sino. Nao compartilham automaticamente a ficha de pacientes nem a conversa em atendimento.

## Contagem

O navegador envia um sinal a cada 30 segundos e somente a idade da ultima interacao confiavel (clique, tecla, toque ou rolagem). Nao envia conteudo digitado, URLs visitadas, imagens da tela ou eventos brutos. O servidor usa seu proprio horario e identifica usuario e sessao pelo cookie HttpOnly, nunca pelo corpo da requisicao.

Apos cinco minutos sem interacao, o estado passa a ocioso. Uma aba sem sinal por 90 segundos fica offline. Sair da conta encerra imediatamente a presenca daquela sessao, inclusive em outras abas. Sessoes em outros dispositivos continuam independentes. Ao fechar a pagina, um sinal de saida tenta encerrar a aba; se nao chegar, vale o limite de 90 segundos.

Nao existe estado nem registro de pausa. No almoco deve-se sair da conta. Intervalos desconectados nao entram no tempo logado. Sessoes revogadas, redefinicao de senha e desativacao encerram a presenca. O historico inicia com a publicacao: nenhum tempo anterior e inventado.

Ociosidade e ausencia de interacao no sistema, nao medida de produtividade ou controle de ponto. Navegadores suspensos e quedas de rede podem encerrar a presenca; a precisao normal depende do intervalo de sinal. Nao use estes dados isoladamente para avaliar desempenho.

## Armazenamento E Isolamento

Quatro tabelas aditivas no banco persistente de autenticacao: `team_clients`, `team_presence`, `team_intervals` e `team_help`. As transacoes de escrita consolidam intervalos contiguos. A chave da contagem e clinica + usuario, nunca aba. Clientes expirados sao descartados apos dois dias sem apagar os intervalos historicos. Nenhum banco, lead, oportunidade ou mensagem do CRM e removido.

Os relatórios nao exibem o texto dos pedidos pessoais de ajuda. Cada API exige clinica explicita, associacao atual e protecao de mesma origem para mutacoes. O catalogo de permissoes inclui Gestao da equipe para cadastro de novos perfis; os perfis ja salvos nao recebem acessos novos automaticamente.

## Verificacao Local

`python3 -m unittest tests.test_team_presence tests.test_team_http`

`node --test tests/team_presence.test.js`

`python3 tests/preview_team.py` inicia uma previa isolada em `http://127.0.0.1:8769`, com usuarios sinteticos `gestor`, `ana` e `bruno` e senha de teste `TesteLocalDOC42026!`. Nunca inicia integracoes externas nem usa dados de clientes. Interrompa o servidor ao finalizar a verificacao.
