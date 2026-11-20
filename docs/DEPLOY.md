# Colocar o Prova Pronta no ar

Objetivo: um endereço curto que os professores abrem de qualquer computador ou celular, **sempre no ar e sem custo**, com os dados guardados num banco de dados de verdade.

## As combinações possíveis

| Peça | Serviço | Por quê |
|---|---|---|
| **Servidor (A)** | Heroku, plano **Basic**, pago com o crédito do **GitHub Student Developer Pack** (US$ 13/mês por 24 meses) | Fica ligado 24 h e não exige empresa. O Basic custa até US$ 7/mês (512 MB): com provas reais da escola o app chegou a ~340 MB. Pede cartão cadastrado e só cobra o que passar do crédito. |
| **Servidor (B)** | Oracle Cloud *Always Free* — máquina Ampere (ARM) em **São Paulo** ou **Vinhedo** | Gratuito e com vários GB de memória, mas em novembro de 2026 o cadastro gratuito para o Brasil só aceitava cliente **corporativo** (nome da empresa). Serve também qualquer outro servidor Ubuntu. |
| **Banco de dados** | Neon (PostgreSQL) gratuito, na mesma região do servidor (**AWS US East** para o Heroku, **AWS São Paulo** para o caminho B) | Gerenciado, sem cartão, 1 GB por projeto, com histórico para recuperação. Se o servidor um dia mudar, os dados continuam lá. |
| **Endereço** | Heroku: `provamadre-xxxx.herokuapp.com` · Caminho B: DuckDNS, ex.: **provamadre.duckdns.org** | O DuckDNS só aponta para um IP fixo, então serve apenas no caminho B. No Heroku dá para usar um domínio próprio (o pacote de estudante dá um por 1 ano). O HTTPS é automático nos dois. |

Por que não as alternativas mais “simples”: no Render gratuito o site **dorme** depois de 15 minutos (o primeiro acesso demora ~1 min) e a máquina tem só 0,1 CPU, o que deixa a leitura de fotos e o PDF lentos; o Fly.io e o Railway não têm mais plano grátis contínuo; o Hugging Face passou a restringir apps Docker no plano gratuito. O arquivo [`render.yaml`](../render.yaml) continua disponível para quem quiser o caminho pago mais simples no futuro (Render Starter).

> Os planos gratuitos mudam com o tempo. O app roda em qualquer lugar que aceite Docker; trocar de servidor é rodar o mesmo script em outra máquina, com o mesmo banco.

---

## 1. Banco de dados (Neon) — 5 minutos

1. Crie a conta em <https://neon.com> (entre com Google ou GitHub; não pede cartão).
2. **New project** → nome `prova-pronta` → região **AWS US East (N. Virginia)** se o servidor for o Heroku, ou **AWS South America (São Paulo)** no caminho B → Postgres mais recente. Banco e servidor na mesma região deixam cada salvamento mais rápido.
3. Em **Connect**, copie a *connection string* (começa com `postgresql://` e termina com `sslmode=require`). Guarde: ela é a `DATABASE_URL`.

## Caminho A: Heroku com o pacote de estudante — 20 minutos

1. Com o pacote aprovado, abra <https://education.github.com/pack/offers> → **Heroku** → *Get access by connecting your GitHub account on Heroku*. Crie a conta Heroku, ative a verificação em duas etapas e cadastre o cartão (exigido para receber o crédito).
2. No painel do Heroku: **New → Create new app** → nome `provamadre` → região **United States**.
3. O app usa a imagem Docker do repositório ([`heroku.yml`](../heroku.yml)). Mude a *stack* para contêiner uma única vez, com o [Heroku CLI](https://devcenter.heroku.com/articles/heroku-cli): `heroku login` e depois `heroku stack:set container -a provamadre`.
4. **Settings → Config Vars**: `DATABASE_URL` (Neon), `ACCESS_CODE`, `JOB_WORKERS=1` e, para enviar à impressão, `MAIL_MODE=smtp`, `SMTP_USERNAME` e `SMTP_PASSWORD` (senha de app, veja abaixo).
5. **Deploy → GitHub** → conecte o repositório → **Enable Automatic Deploys** na `main` e clique em **Deploy Branch**. A primeira montagem leva alguns minutos.
6. **Resources** → troque o tipo de dyno para **Basic** (o Eco dorme quando ninguém usa).
7. Abra o endereço mostrado em **Open app** e confira `/api/health`.

Atualizações: cada envio para a `main` publica sozinho (depois que o CI passar, se marcar *Wait for CI to pass*).

## Caminho B, passo 2: endereço curto (DuckDNS) — 2 minutos

1. Entre em <https://www.duckdns.org> (login com Google/GitHub).
2. Crie o subdomínio, por exemplo `provamadre` → o site será `provamadre.duckdns.org`.
3. Deixe a página aberta: no passo 3 você vai colar ali o IP do servidor.

> Mais tarde, se a escola quiser um endereço próprio (ex.: `provamadre.com.br`, cerca de R$ 40/ano no registro.br), basta apontar o domínio para o mesmo IP e trocar `SITE_ADDRESS` no `.env`.

## Caminho B, passo 3: servidor (Oracle Cloud Always Free) — 20 minutos

1. Crie a conta em <https://signup.cloud.oracle.com/> (não confunda com o perfil do site oracle.com). No Brasil o formulário só oferece cliente *Corporate*. Escolha a **região inicial São Paulo ou Vinhedo** (não dá para mudar depois). O cadastro pede um cartão só para verificar identidade; recursos *Always Free* não são cobrados.
2. **Compute → Instances → Create instance**:
   - Image: **Ubuntu 24.04**.
   - Shape: **Ampere → VM.Standard.A1.Flex** com **2 OCPU e 12 GB** (marcado como *Always Free-eligible*). Se aparecer “out of capacity”, tente outro *availability domain* ou mais tarde.
   - Rede: deixe criar a VCN padrão, com **IP público**.
   - Chave SSH: baixe a chave privada sugerida (guarde bem).
3. Torne o IP fixo: na instância, **Attached VNICs → IPv4 addresses → Edit → Reserved public IP**.
4. Libere a web: **Networking → Virtual cloud networks → (sua VCN) → Security Lists → Default** → *Add Ingress Rules*: origem `0.0.0.0/0`, TCP, portas **80** e **443**.
5. Cole o IP no DuckDNS (campo *current ip*) e clique **update ip**.
6. **Evite que a máquina seja recolhida por ociosidade:** a Oracle pode desligar instâncias gratuitas muito ociosas. Em **Billing → Upgrade and Manage Payment** faça o *upgrade* para **Pay As You Go**: os recursos *Always Free* continuam gratuitos e a regra de ociosidade deixa de valer. Crie também um **Budget** de R$ 1 com alerta por e-mail, por segurança.

## Caminho B, passo 4: instalar o Prova Pronta no servidor — 10 minutos

No seu computador, conecte-se ao servidor (troque o caminho da chave e o IP):

```bash
ssh -i caminho/da/chave.key ubuntu@IP_DO_SERVIDOR
```

No servidor:

```bash
git clone https://github.com/LuisPassosRamos/testMaker.git && cd testMaker
```
```bash
bash deploy/setup-server.sh
```

O script instala o Docker, abre as portas, cria a memória de troca se precisar e pergunta:

- **Endereço do site** → `provamadre.duckdns.org`
- **Código da escola** → algo fácil de dizer, ex.: `madre2026` (os professores digitam uma vez por aparelho; maiúsculas, acentos e espaços não importam)
- **DATABASE_URL** → a string do Neon
- **Gmail que envia os e-mails** e a **senha de app** (veja abaixo) — pode deixar vazio para testar primeiro

A primeira montagem leva alguns minutos. Depois, abra `https://provamadre.duckdns.org`.

> Repositório privado? Use `git clone https://SEU_TOKEN@github.com/...` com um *personal access token* de leitura.

### Senha de app do Gmail (para enviar à impressão)

Use uma conta Google da escola com verificação em duas etapas, crie uma senha de app em <https://myaccount.google.com/apppasswords> e informe-a no script (ou no `.env`, campos `SMTP_USERNAME`, `SMTP_PASSWORD` e `MAIL_MODE=smtp`). Os e-mails vão para **escolamadre2impressoes@gmail.com** (`MAIL_TO`).

## 5. Entregar aos professores

- Endereço: **<https://provamadre-e34ad34b056f.herokuapp.com>** (caminho B: `provamadre.duckdns.org`).
- Código da escola: o que você definiu.
- Dica: imprima um cartaz para a sala dos professores com o endereço e um QR Code dele.

Para trocar o código (por exemplo, no início do ano): no Heroku, edite `ACCESS_CODE` em **Settings → Config Vars** (o app reinicia sozinho); no caminho B, edite o `.env` do servidor e rode `bash deploy/update.sh`. Todos os aparelhos precisarão digitar o novo código.

---

## Manutenção

| Tarefa | Como |
|---|---|
| Atualizar para a versão mais nova | `cd testMaker && bash deploy/update.sh` |
| Ver se está tudo bem | Abra `https://SEU_ENDERECO/api/health` — mostra banco, OCR, PDF e e-mail. |
| Ver registros | `sudo docker compose --env-file .env -f deploy/docker-compose.yml logs -f app` |
| Reiniciar | `sudo docker compose --env-file .env -f deploy/docker-compose.yml restart` |
| Mudar configurações | Edite `.env` e rode `bash deploy/update.sh` |

**O que fica guardado e por quanto tempo:** provas em andamento, arquivos enviados e recortes ficam no banco por `RETENTION_HOURS` (72 h por padrão) desde a última alteração e depois são apagados; os modelos de cabeçalho ficam para sempre. PDFs e prévias são gerados de novo quando necessário. Com esse limite, o 1 GB do Neon comporta folgadamente o uso de uma escola.

**Segurança:** o site exige o código da escola em todas as rotas da API, limita tentativas de login e envios por hora, usa HTTPS e guarda senhas apenas no `.env` do servidor (permissão só do dono). Não há contas individuais: quem tem o código usa o sistema.

## Alternativa paga mais simples (futuro)

Se a escola preferir não administrar um servidor: crie um *Blueprint* no Render a partir deste repositório (ele lê o [`render.yaml`](../render.yaml)), informe `DATABASE_URL`, `ACCESS_CODE` e o Gmail, e escolha o plano **Starter** (sem dormir). O endereço fica `provamadre.onrender.com`, e o mesmo banco Neon continua sendo usado.
