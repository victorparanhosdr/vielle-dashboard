# Fase 6 - Mapeamento das abas

Auditoria do frontend realmente servido de static e do Handler em app.py.
O usuario autorizou concluir as fases restantes em conjunto em 08/09/2026.
Este mapeamento foi feito antes de implementar as permissoes das fases 7-11.

| Aba | Elemento HTML / data-view | Chave estavel | Funcoes principais | Endpoints |
| --- | --- | --- | --- | --- |
| Painel geral | generalView | dashboard | renderGeneralPanel, renderGeneralDoctorFilter | /api/report, /api/monthly-goal |
| Comercial | commercialView | commercial | renderDailyChart, renderClinicaExperts, renderDoctorCross, renderStatusColumnChart | /api/report |
| Financeiro | financialView | financial | renderFinancial | /api/report |
| Acompanhamento de Paciente | patientFollowupView | patient_followup | renderPatientFollowup, renderPatientFollowupList, savePatientFollowupContact | /api/report, /api/patient-followup-contact, /api/patient-followup-status |
| Acompanhamento de Orcamentos | quoteFollowupView | budget_followup | renderQuoteFollowup, renderQuoteFollowupList, saveQuoteFollowupContact | /api/report, /api/quote-followup-contact, /api/quote-followup-status |
| Trafego pago | trafficView | paid_traffic | renderPaidTraffic, syncTrafficNow | /api/report, /api/sync-traffic |
| Avaliacao WhatsApp | whatsappAuditView | whatsapp_review | renderWhatsappAudit, saveWhatsappAuditReview, evaluateWhatsappAuditWithAi | /api/report, /api/whatsapp-audit-review, /api/whatsapp-audit-ai |

As tres clinicas possuem todas as abas, exceto patient_followup, exclusiva da
Vielle. Configuracoes e administracao de integracoes sao controles separados,
nao uma das sete abas. Nao existe modulo separado de Agenda neste frontend.

## Carregamento e menu mobile

loadReport -> buildQuery -> /api/report -> render distribui os dados para os
renderizadores. syncFilterState atualiza os filtros comuns.

Desktop e mobile usam os mesmos botoes .tabBtn[data-view] em .viewTabs.
#mobileTabsToggle abre/fecha esse mesmo menu; nao ha uma lista de abas duplicada.
applyActiveViewState seleciona o painel .viewPanel.active.

## Risco encontrado

/api/report devolvia dados de varias abas em uma unica resposta, incluindo
financial e general_panel. Esconder o botao Financeiro nao seria suficiente.
As fases seguintes precisam autorizar a view e projetar somente o conjunto de
dados correspondente. Os flags include_followup, include_quote_followup e
include_whatsapp_audit tambem precisam de validacao.

## Semantica de visualizacao

dashboard.view permite os indicadores ja presentes no Painel geral, inclusive
resumos de receitas/despesas. financial.view libera o painel financeiro
detalhado. commercial.view inclui as estatisticas comerciais de vendas e
agendamentos ja existentes na aba Comercial. Nao sao permissoes por campo.

Exportacao compartilhada: exportPdf / scheduleAutoPrint; permissao a verificar
conforme a aba atual. Integracoes globais e configuracoes continuam Master-only.

Esta fase de mapeamento nao exige migracao de banco.
