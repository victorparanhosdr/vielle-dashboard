# Total vendido e total recebido

- Painel geral: o antigo Faturamento total passa a Total vendido.
- Graficos/ranking/exportacoes que usam vendas passam a nomes de vendas.
- Novo Total recebido (faturamento liquido): parcelas de receitas com status
  recebido/quitado, sem somar o titulo inteiro e sem duplicar parcelas.
- Valor em centavos da API: net_amount, que ja desconta taxas. Somente quando
  indisponivel, usa final_amount menos fees_amount explicitamente informados.
  Nunca assume taxa zero nem utiliza o bruto como liquido por falta de dados.
- Data: compensation_date primeiro, depois datas explicitas de liquidacao,
  recebimento ou execucao. Nunca vencimento ou calc_compensation_date prevista.
- A origem financeira usada sao parcelas de Venda/sale/receita. Outras contas,
  despesas, saldo inicial e lancamentos manuais de outros tipos nao entram.
- Receita de venda antiga recebida no mes entra no mes do recebimento.
- Compensacoes futuras nao entram no recebido, mesmo dentro do mes selecionado.
- Profissional: seller do titulo, quando fornecido; senao cruzamento exato de
  paciente + data de emissao/venda + valor final do titulo/venda. Todos os
  candidatos precisam apontar ao mesmo seller. Nao usa apenas paciente, nome,
  valor isolado ou a coincidencia de data isolada.
- Sem vinculo seguro: inclui no total da clinica, mas nao em um profissional.
  O painel avisa sobre parcelas sem atribuicao, data ou valor liquido. O aviso
  de data abrange a base, pois sem data nao e possivel saber o periodo correto.
- Metas, projecao, margens e vendido menos saidas continuam usando vendas.
  O novo indicador nao substitui silenciosamente calculos da aba Financeiro.
- Sem migracao. Leitura das tabelas atuais, sem modificar bancos/integracoes.

## Validacao local

`python -m unittest tests.test_financial_receipts tests.test_commercial_report tests.test_chart_export`

`node tests/test_general_receipts.cjs`

`python tests/serve_receipts_preview.py`

A previa imprime um endereco localhost e usa dados ficticios: profissional A,
10.000 vendidos e 9.700 recebidos; B, 5.000 vendidos e 4.820 recebidos. O link
automatico de sessao existe somente nesse servidor isolado de testes.

Esta alteracao nao foi publicada automaticamente.

Validacao em 23/09/2026: suite de 199 testes Python aprovada, teste JS de
renderizacao e sintaxe aprovados. Previa desktop (1280) e mobile (390) conferida,
incluindo troca real de profissional: A = 10.000/9.700, B = 5.000/4.820.
Na copia local de agosto/2026, a soma dos seis profissionais coincidiu com o
total liquido da clinica, sem parcelas do periodo sem atribuicao. Essa copia
historica foi consultada somente em leitura, sem sincronizar ou modificar dados.

Arquivos desta entrega: financial_receipts.py, app.py (importacao, chamada do
calculo e campo receipts), static/index.html, static/app.js, static/styles.css,
chart_export.py, tests/test_financial_receipts.py, tests/test_commercial_report.py,
tests/test_general_receipts.cjs, tests/serve_receipts_preview.py, NET_RECEIPTS.md.
O app.py tem outras alteracoes locais preexistentes de orcamentos: nao incluir
esses outros trechos numa futura publicacao desta entrega.
