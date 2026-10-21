# Site comercial DOC4DOCS

## Entradas

- `/inicio`: apresentacao publica da plataforma, sempre disponivel.
- `/planos`: configuracoes Essencial, Conectado e Institutos, sob consulta.
- `/login`: acesso existente, sem alterar autenticacao ou permissoes.
- `/`: visitantes sem sessao veem o site; usuarios autenticados mantem a entrada operacional. Parametros internos de clinica continuam protegidos.
- `/master/commercial.html`: pedidos de demonstracao e proposta, somente Master.

## Interessados

`POST /api/public/demo` valida nome, organizacao, e-mail, telefone internacional,
interesse, mensagem opcional e ciencia da politica. O retorno publico nunca revela
registros ou identificadores. Reenvios com a mesma chave e dados sao idempotentes.

Os pedidos ficam em `marketing_leads`, no banco central de autenticacao. Nao criam
pacientes, contatos ou oportunidades nos CRMs das clinicas. O Master acompanha as
etapas Novo, Em contato, Demonstracao, Proposta, Contratado e Encerrado, com notas,
busca e paginacao. Atualizacoes usam revisao otimista e auditoria de mudanca de etapa.

UTMs source, medium, campaign, content e term acompanham a navegacao comercial na
sessao do navegador. A origem do pedido e a versao da politica tambem sao gravadas.
Nao ha pixels publicitarios ou notificacoes externas automaticas neste fluxo.

A protecao basica inclui limite de corpo, validacao no servidor, honeypot, origem
da requisicao e limites em memoria por contato e por par de rede direto. O limite
de rede e mais amplo para nao confundir varios visitantes atras de um proxy.
Os limites sao locais a cada processo e nao substituem um WAF para grandes campanhas.

## Conteudo e verificacao

As imagens `marketing-*.png` mostram a interface real com dados inteiramente
ficticios, produzidos pelo ambiente de testes. Os planos nao publicam valores,
limites, promessas de resultado ou contratacao automatica. O escopo definitivo e
os custos de provedores externos dependem de proposta.

Cobertura: validacao/persistencia, idempotencia, paginacao, atribuicao, auditoria,
controle Master, rotas publicas versus privadas e revisao visual desktop/mobile.
