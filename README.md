# Relatorio de Leads Kommo

Aplicativo local para conectar no Kommo, sincronizar leads e gerar um painel que atualiza automaticamente.

## Como configurar no Kommo

1. No Kommo, va em **Configuracoes > Integracoes > Criar integracao**.
2. Preencha a integracao com um nome e descricao.
3. Em **URL de redirecionamento**, use:

```text
http://localhost:8080/auth/callback
```

4. Salve a integracao e copie:
   - **Integration ID** para `KOMMO_CLIENT_ID`
   - **Secret key** para `KOMMO_CLIENT_SECRET`
   - **Token de longa duracao** para `KOMMO_LONG_LIVED_TOKEN`
5. Em permissoes, permita acesso aos leads. Se quiser enriquecer depois, tambem podemos incluir contatos, empresas, tarefas e usuarios.

Para a primeira versao do relatorio, o caminho mais simples e usar o **token de longa duracao**. O OAuth com codigo de autorizacao continua no app para uma evolucao futura, mas o codigo expira em 20 minutos e nao e necessario para o teste inicial.

## Como rodar

1. Copie `.env.example` para `.env`.
2. Preencha os dados da sua conta Kommo.
3. Abra esta pasta no terminal e rode:

```bash
python3 app.py
```

Se o seu macOS pedir Command Line Tools ao usar `python3`, use o Python embutido do Codex:

```bash
/Users/victorparanhos/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 app.py
```

4. Acesse:

```text
http://localhost:8080
```

5. Clique em **Atualizar** para puxar os leads. Se voce nao preencher `KOMMO_LONG_LIVED_TOKEN`, use **Conectar Kommo** e autorize o acesso.

## Exemplo de `.env`

```text
KOMMO_SUBDOMAIN=suaempresa
KOMMO_CLIENT_ID=cole_o_id_de_integracao
KOMMO_CLIENT_SECRET=cole_a_chave_secreta
KOMMO_LONG_LIVED_TOKEN=cole_o_token_de_longa_duracao
KOMMO_REDIRECT_URI=http://localhost:8080/auth/callback
CLINICA_EXPERTS_TOKEN=cole_o_token_do_clinica_experts
SYNC_INTERVAL_MINUTES=30
APP_SECRET=troque_por_um_texto_grande_aleatorio
DASHBOARD_USER=admin
DASHBOARD_PASSWORD=troque_por_uma_senha_forte
MASTER_USER=master
MASTER_PASSWORD=troque_por_uma_senha_master
PORT=8080
```

Depois que o app estiver rodando, voce tambem pode alterar Kommo, Clinica Experts, usuario/senha do dashboard e senha master pela pagina **Configuracoes** no topo do sistema.

## Observacao importante

Para usar em producao, a URL de redirecionamento precisa ser uma URL HTTPS publica, por exemplo em um servidor proprio, Render, Railway, Fly.io, Cloudflare Tunnel ou similar. O `localhost` funciona para desenvolvimento e validacao inicial.

## Proximos passos naturais

- Traduzir IDs de status, funil e responsavel para nomes reais.
- Adicionar filtros por periodo, origem, responsavel e funil.
- Cruzar os leads com a segunda API quando voce passar o nome do sistema e os campos em comum.
- Publicar em um servidor para sincronizar sem depender do computador ligado.
