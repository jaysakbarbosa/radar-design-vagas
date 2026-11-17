# Radar Vagas 💼

Bot do Telegram que avisa, duas vezes por semana, **vagas remotas de design**, no Brasil e no mundo todo. Busca em português e inglês. Você também registra onde se candidatou e o bot lembra das datas de entrevista e prazos.

Cinco áreas, cada uma em seção própria da mensagem:

| Área | Seção | Exemplos de cargo |
|---|---|---|
| 🎨 | Design gráfico | Designer gráfico, brand designer, ilustrador, diretor de arte |
| 📱 | Design de produto digital (UI/UX) | UX/UI designer, product designer, UX researcher, design systems |
| 📦 | Design de produto (geral) | Designer de produto, designer industrial, CMF |
| 🪑 | Design de móveis | Designer de móveis, projetista de mobiliário (remoto ou freelance) |
| 🎬 | Motion design | Motion designer, animador, editor de vídeo |

Escopo da vaga:

| Ícone | Escopo |
|---|---|
| 🇧🇷 | Remoto aberto a quem mora no Brasil (empresas brasileiras ou que contratam no Brasil) |
| 🌍 | Remoto para outros países ou worldwide |

Dentro de cada área, as vagas do Brasil vêm primeiro e as mais recentes aparecem antes.

Fontes pesquisadas: LinkedIn, We Work Remotely, Remote OK, Remotive, Himalayas, Wellfound, Dribbble Jobs, Behance Job List, Working Nomads, Gupy, Trampos.co, Vagas.com, Revelo, Coodesh e páginas de carreiras das empresas.

---

## Instalação, passo a passo (celular ou computador)

1. **Crie o bot no Telegram.** Fale com @BotFather → `/newbot` → escolha nome e usuário (precisa terminar com `bot`). Ele mostra um **token**. Guarde só para você e nunca mande print com ele.
2. **Abra o seu bot novo** e mande `/start`. Sem isso o bot não consegue falar com você.
3. **Crie o repositório no GitHub** com o nome `radar-vagas` e suba os arquivos:
   - `bot.py`
   - `config.json`
   - `README.md`
   - `.github/workflows/radar.yml` (crie com **Add file → Create new file** e digite o caminho completo no nome; as pastas são criadas sozinhas)
4. **Cadastre os secrets** em Settings → Secrets and variables → Actions → New repository secret:

   | Nome | Valor |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | o token do bot novo |
   | `TELEGRAM_CHAT_ID` | o número do chat de quem vai usar o bot |
   | `GROQ_API_KEY` | chave do Groq (console.groq.com) |
   | `TAVILY_API_KEY` | chave da Tavily (tavily.com) |

5. **Descubra o `TELEGRAM_CHAT_ID`:** quem vai usar o bot manda `/start` para ele. Depois abra no navegador `https://api.telegram.org/botSEU_TOKEN/getUpdates` (troque `SEU_TOKEN` pelo token) e copie o número que vem depois de `"chat":{"id":`.
6. **Primeiro teste:** aba **Actions** → **Radar Vagas** → **Run workflow** → mode `full`. Rode **uma vez só** e **não cancele**. Leva uns 5 minutos. No fim a mensagem chega no Telegram.

Se a aba Actions estiver vazia, o `radar.yml` não está em `.github/workflows/radar.yml`. Confira o caminho.

---

## Quando o bot fala com você

| Quando | O que acontece |
|---|---|
| **Segunda e quinta, 12h (Natal)** | Varredura completa na web. Manda as vagas novas, separadas por área |
| **Outros dias, 12h** | Lembretes: avisa prazos de candidatura que estão perto e datas das suas candidaturas |
| **A cada 3 horas** | Lê os comandos que você mandou e responde |

Por causa desse intervalo, **a resposta aos comandos pode levar até ~3 horas**.

Os lembretes saem **7 e 2 dias antes** de cada data, e **no dia**.

O bot só manda a mesma vaga **uma vez**. Se não houver vaga nova, ele diz que está calmo.

---

## Como ler a mensagem

Cada vaga aparece assim:

```
🌍 #1 Senior Product Designer · Acme
🏠 Remoto (Worldwide) · sênior · 💰 US$ 90-120k
🕒 publicada em 14/11/2026 (há 3 dias) · ver vaga
```

- `#1` é o **número da vaga**. É ele que você usa nos comandos.
- 🏠 mostra o escopo remoto, o nível e o salário, quando a vaga divulga.
- 🕒 é a data de publicação. Quando a vaga tem **prazo de candidatura**, aparece ⏳ com a data e quantos dias faltam.
- `ver vaga` abre a página da vaga. **Confira sempre no link** se ela ainda está aberta e se é mesmo remota: o bot lê a web com IA e pode errar.

Vagas sem prazo informado ficam na lista por **30 dias** a partir da publicação e depois saem sozinhas.

---

## Comandos

Mande os comandos como mensagem normal no chat com o bot.

### Ver o que está aberto

**`/vagas`**
Lista todas as vagas abertas, com número, empresa e escopo. Use para achar o número que vai nos outros comandos.

**`/ajuda`** (ou `/start`)
Mostra a lista de comandos.

### Marcar o que você fez

**`/candidatei 12`**
Registra que você se candidatou à vaga #12. O bot cria um registro `S1`, `S2`… e passa a acompanhar essa candidatura.
- Também aceita uma vaga que não está na lista: `/candidatei Product Designer na Nubank`.
- As vagas não divulgam datas de entrevista, então você adiciona com `/data` quando marcarem.

**`/ignorar 12`**
Esconde a vaga #12 e não avisa mais dela. Use para vagas que não interessam ou que o bot errou.

### Acompanhar suas candidaturas

**`/minhas`**
Mostra todas as suas candidaturas (`S1`, `S2`…), com o status e as próximas datas.

**`/status S1 entrevista`**
Atualiza a candidatura `S1`. Os status são:
- `entrevista`: chamaram para entrevista
- `oferta`: veio proposta
- `aceito`: você foi contratada
- `rejeitado`: não passou
- `retirado`: você desistiu

Candidaturas `rejeitado` e `retirado` deixam de gerar lembretes.

**`/data S1 Entrevista 2026-12-01`**
Adiciona ou corrige uma data da candidatura `S1`. O formato é `/data S1 nome-da-data AAAA-MM-DD`. Serve para entrevista, teste técnico, prazo de resposta, início, e o bot lembra nos dias combinados.

**`/remover S1`**
Apaga a candidatura `S1`.

---

## Exemplo de uso do começo ao fim

1. Segunda chega a mensagem. Você vê `#2 UX/UI Designer Pleno · Loja Br` e se candidata no site.
2. Você manda `/candidatei 2`. O bot registra como `S1`.
3. Uma semana depois chamam para entrevista: `/status S1 entrevista`.
4. Marcaram para 1º de dezembro: `/data S1 Entrevista 2026-12-01`. O bot avisa 7 e 2 dias antes, e no dia.
5. Veio a proposta: `/status S1 oferta`. Depois que você aceitar: `/status S1 aceito`.
6. Uma vaga da lista que não serve: `/ignorar 9`.

---

## Personalizar

Tudo fica no `config.json`:
- `areas`: as 5 áreas, com a busca em inglês (`themes`) e em português (`pt_themes`). Para mudar o tipo de cargo (por exemplo só "sênior"), edite o texto desses temas.
- `boards_world`, `boards_br` e `boards_design`: os sites de vagas pesquisados. Para incluir outro site, adicione o nome na lista.
- `job_ttl_days`: por quantos dias uma vaga sem prazo fica na lista (padrão 30).
- `reminder_days`: dias antes do prazo em que o bot lembra (padrão 7 e 2).

## Observações

- **Cota do Groq e da Tavily:** os bots compartilham as mesmas chaves. O Radar Vagas faz uns 21 buscas por varredura, duas vezes por semana, e roda na segunda e quinta, longe dos outros bots (sexta). Se a cota mensal da Tavily (1.000 créditos) apertar, crie uma chave separada para este bot.
- **Não rode a varredura várias vezes no mesmo dia.** Isso gasta a cota à toa. Os lembretes e os comandos funcionam sozinhos.
- **Confira sempre a vaga no link.** O bot lê a web com IA: às vezes inclui vaga que já fechou, que não é 100% remota ou que não aceita candidatos do Brasil, e às vezes deixa passar vagas.
- **Segredos:** nunca cole o token ou as chaves em prints, mensagens ou arquivos. Se vazar, gere outro no @BotFather com `/revoke` e atualize o secret.
