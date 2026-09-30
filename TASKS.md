# Tarefas por clínica

Acesse a aba **Tarefas** ou `/tasks.html?clinic=vielle` (também `inspire`, `carla` e `brandao`).
O Master possui acesso. Para a equipe, libere Tarefas no Painel Master, individualmente em cada clínica:

- Visualizar: quadro, lista, calendário, detalhes, histórico e download dos anexos.
- Criar: novas tarefas.
- Editar: dados, status, responsáveis, checklist, progresso, comentários e anexos.
- Excluir / restaurar: arquivar e restaurar. O histórico e os arquivos são preservados.

Responsáveis precisam ser usuários ativos com acesso a Tarefas na clínica. Permissões existentes não são ampliadas automaticamente.
Cada tarefa pode ter vários responsáveis, início, vencimento e prioridade. O checklist calcula o progresso; tarefas concluídas têm 100%.
Alterações usam revisão para impedir sobrescrita silenciosa por outro usuário. Reabra os detalhes quando houver conflito.

## Persistência

Banco e arquivos em `<diretório do auth.db>/tasks/<clinic_key>/tasks.sqlite3` e `files/`.
O diretório acompanha o volume persistente de autenticação. Inclua essa pasta nos backups do volume.
As tabelas são criadas automaticamente no primeiro acesso autorizado. Nenhum banco das integrações é alterado.
Arquivos têm limite de 10 MB cada, 50 anexos e 200 MB por tarefa. São servidos somente por rota autenticada.
Listagem e histórico usam páginas de 100 registros; o botão de carregar mais preserva o acesso ao restante.

## Teste local

`python3 -m unittest discover -s tests -p 'test_tasks.py'`

`python3 tests/serve_tasks_preview.py` inicia uma prévia local com dados fictícios e login automático no link mostrado pelo terminal.
