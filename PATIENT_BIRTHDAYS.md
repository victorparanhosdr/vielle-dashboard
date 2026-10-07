# Aniversários dos pacientes

A aba `birthdays.html?clinic=<clínica>` usa a base de pacientes já sincronizada
do Clínica Experts. Abre no mês atual de São Paulo e permite trocar mês/ano,
buscar pacientes e filtrar compras ou presentes enviados. O módulo é separado
por clínica e não altera o cadastro ou o prontuário no sistema de origem.

## Dados e critérios

- Nascimento: campo `date_birth` do paciente; aceita data ISO ou `dd/mm/aaaa`.
  Datas ausentes, inválidas ou futuras geram aviso, sem inventar aniversários.
- Compras: vendas acumuladas na base sincronizada, vinculadas pelo UUID do
  paciente. Usa o valor final da venda; exclui orçamentos, cancelamentos,
  exclusões e vínculos conflitantes. Não associa pacientes por nome.
- Valores ausentes: preserva a contagem da venda e indica total parcial.
- A idade exibida é a idade no aniversário do ano selecionado.
- 29/02: agrupado em 28/02 nos anos não bissextos, mantendo indicação de 29/02.
- Os indicadores e gráficos cobrem o mês inteiro; busca e filtros refinam a
  lista, cujo contador explicita quantos pacientes estão visíveis.
- Exportação XLSX inclui toda a lista filtrada, não apenas a página atual.

O botão Atualizar tudo reutiliza a sincronização existente, incluindo pacientes.
O histórico depende da cobertura disponível nessa base, não representa uma
promessa de acesso a vendas ainda não importadas.

## Presentes e acesso

`birthday_gifts` registra um presente por paciente/ano, com data do envio,
descrição, observação, usuário, horário e revisão. `birthday_gift_events`
preserva inclusões, edições e correções de envio. Desfazer exige motivo e não
apaga o histórico. A revisão evita sobrescrever silenciosamente outro usuário.

Permissões: `birthdays.view`, `birthdays.create`, `birthdays.edit` e
`birthdays.export`. Master tem acesso. Usuários já cadastrados precisam receber
o módulo no Master; permissões existentes não são ampliadas automaticamente.

APIs autenticadas e com clínica obrigatória:

- GET `/api/birthdays`, `/api/birthdays/patient`, `/api/birthdays/export`
- POST `/api/birthdays/gift`, `/api/birthdays/gift/undo`

## Verificação local

`python3 -m unittest tests.test_patient_birthdays tests.test_access_permissions
tests.test_auth_http tests.test_tasks` valida critérios, dados de origem,
exportação segura, histórico anual, concorrência, autenticação e isolamento.

`python3 tests/serve_birthdays_preview.py` abre uma prévia local com dados
fictícios. A opção `--source-dir <diretório>` copia somente pacientes e vendas
do cache local para um banco temporário. Nunca grava no banco original,
importa chaves ou chama APIs externas. Os presentes de teste ficam nessa cópia.
