# Relatorio de competencia

O relatorio fica no fim da aba Financeiro, sem substituir os componentes de
fluxo de caixa. Consulta somente a base sincronizada da clinica selecionada.

## Criterios

- Intervalo diario inclusivo: De e Ate. Para um unico dia, preencher ambos com
  a mesma data.
- Usa a data de competencia do titulo quando fornecida. Na base atual do
  Experts, usa `emission_date` quando nao existe um campo de competencia.
  Nao usa pagamento, compensacao, vencimento ou criacao como substitutos.
- Conta cada titulo uma vez, sem somar novamente suas parcelas. Inclui titulos
  em aberto, mas ignora cancelados e saldos iniciais.
- Valor bruto: `final_amount`. Valor liquido: `net_amount`; quando ausente,
  calcula bruto menos `fees_amount` somente se ambos estiverem informados.
- Classifica Venda como receita; Conta a pagar como despesa e Conta a receber
  como receita. Tambem considera campos explicitos de direcao e parcelas
  recebidas. Conta sem indicacao de receita segue a convencao de despesa da
  integracao atual. Tipos desconhecidos e direcoes contraditorias sao excluidos.
- O filtro por profissional exige identificacao explicita ou vinculo unico
  com a venda (paciente, emissao e valor). Nao distribui custos sem vinculo.
- Titulos sem data, valores ou vinculo seguro geram aviso de resultado parcial.
- Contato, categoria, tipo de titulo e busca filtram tabela e totais. Os botoes
  Receitas/Despesas/Total mudam as linhas exibidas, preservando os tres totais.
- A tabela pagina em 50 linhas; Excel exporta todas as linhas filtradas,
  incluindo os identificadores e os criterios usados.

Os testes automatizados usam dados ficticios. A reconciliacao dos totais com
um extrato de competencia real do Experts deve usar os mesmos filtros e o
mesmo instante de sincronizacao, inclusive verificando contas manuais.

## Seguranca e persistencia

As rotas GET `/api/financial-competence` e
`/api/financial-competence/export` validam sessao, clinica e `financial.view`.
A exportacao tambem exige `financial.export`. Restricoes existentes por
profissional continuam aplicadas no backend.

Nao ha migracao, nova tabela ou alteracao nos dados. O relatorio apenas le
`clinica_bills`, `clinica_parcels` e, quando necessario, `clinica_sales`.

## Testes locais

```sh
python -m unittest tests.test_financial_competence tests.test_financial_receipts tests.test_chart_export
python -m unittest tests.test_auth_http.HttpAuthTests.test_competence_report_permissions_and_export tests.test_auth_http.HttpAuthTests.test_report_endpoint_projects_only_authorized_view tests.test_auth_http.HttpAuthTests.test_excel_export_permissions_and_response tests.test_access_permissions
node --check static/financial-competence.js
node --check static/app.js
```

`tests/serve_competence_preview.py` inicia uma previa isolada com dados
ficticios, sem acessar integracoes ou os bancos reais. A URL e impressa no
terminal. Abrir `/__preview__`, entrar no Financeiro e testar:

1. Datas iguais nos dois campos e linhas somente desse dia.
2. Profissional, contato, categoria, tipo, busca e ordenacao.
3. Receitas, despesas, saldo e navegacao entre paginas.
4. Excel com todas as linhas, nao somente a pagina atual.
5. Filtros, valores e rolagem da tabela no celular.
