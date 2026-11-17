#!/usr/bin/env python3
"""Radar Vagas: avisa no Telegram sobre vagas REMOTAS de design (gráfico, UI/UX, produto, móveis e motion),
no Brasil e no mundo, e acompanha suas candidaturas (entrevista, proposta, resposta).

Modos:
  python bot.py                 # varredura completa (Groq + busca na web) + lembretes + comandos
  python bot.py --reminders     # só lembretes e comandos
  python bot.py --poll          # só lê e responde seus comandos do Telegram
  python bot.py --dry-run       # imprime em vez de enviar; não grava estado
  python bot.py --mock f.json   # usa itens de um arquivo em vez do Groq (teste)
  python bot.py --say "/ajuda"  # simula um comando seu (teste local)
  python bot.py --get-chat-id   # descobre seu chat_id depois de mandar /start pro bot

Somente biblioteca padrão do Python 3.9+.
"""
import argparse
import datetime as dt
import html
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
STATE_FILE = Path(os.environ.get("RADAR_STATE", ROOT / "state.json"))
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
TAVILY_URL = "https://api.tavily.com/search"
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
TODAY = dt.date.today()
CATS = {a["key"]: a for a in CFG["areas"]}
CAT_KEYS = set(CATS)
MS_DAYS = CFG.get("milestone_reminder_days", [14, 7, 2, 0])
QRANK = {"A1": 0, "A2": 1, "A3": 2, "A4": 3, "B1": 4, "B2": 5, "B3": 6, "B4": 7, "B5": 8, "C": 9}
INACTIVE = ("rejeitado", "retirado")
STATUS_ALIAS = {
    "submetido": "submetido",
    "revisao": "em revisão", "revisão": "em revisão", "revisado": "em revisão", "emrevisao": "em revisão",
    "aceito": "aceito", "aceita": "aceito", "aprovado": "aceito",
    "rejeitado": "rejeitado", "rejeitada": "rejeitado", "negado": "rejeitado", "recusado": "rejeitado",
    "retirado": "retirado", "retirei": "retirado", "desisti": "retirado",
    "entrevista": "em entrevista", "entrevistas": "em entrevista", "oferta": "oferta", "proposta": "oferta", "contratada": "aceito", "contratado": "aceito",
}

SCHEMA = """Retorne SOMENTE um array JSON (sem texto antes ou depois), com objetos neste formato:
{
  "name": "título da vaga, ex: Product Designer",
  "company": "empresa",
  "acronym": "título e empresa, ex: Product Designer · Nubank",
  "kind": "brasil" (remoto aberto a quem mora no Brasil, inclusive vagas de empresas brasileiras) | "mundo" (remoto para outros países ou worldwide),
  "category": "grafico" | "uiux" | "produto" | "moveis" | "motion" (área principal do design),
  "level": "estágio | júnior | pleno | sênior | lead | null",
  "location": "escopo remoto, ex: Remoto (Brasil), Remoto (Worldwide), Remoto (EUA), Remoto (Europa)",
  "salary": "faixa salarial como divulgada, ou null",
  "posted": "YYYY-MM-DD (data de publicação da vaga) ou null",
  "deadline": "YYYY-MM-DD (prazo para se candidatar, SÓ se estiver escrito) ou null",
  "topics": ["tags curtas"],
  "url": "URL da vaga (ou da página de carreiras da empresa)"
}"""


def pick(*opts):
    return random.choice(opts)


def esc(s):
    return html.escape(str(s))


# ---------------------------------------------------------------- HTTP helpers
def http_json(url, payload=None, headers=None, timeout=240, retries=4):
    data = json.dumps(payload).encode() if payload is not None else None
    hdrs = {"Content-Type": "application/json", "User-Agent": "radar-vagas/1.0 (+https://github.com)", "Accept": "application/json", **(headers or {})}
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:500]
            last = f"HTTP {e.code}: {body}"
            if e.code not in (429, 500, 502, 503, 504):
                break
            if e.code == 429:
                m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", body)
                wait = (int(m.group(1) or 0) * 60 + float(m.group(2))) if m else 0
                if wait > 90 or "per day" in body or "Request too large" in body:
                    break  # limite diário: esperar não adianta
                if wait:
                    time.sleep(wait + 1)
                    continue
        except Exception as e:  # rede, timeout
            last = repr(e)
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(last)


# ---------------------------------------------------------------- busca (Groq)
def extract_json_array(text):
    text = re.sub(r"```(?:json)?", "", text)
    i, j = text.find("["), text.rfind("]")
    if i == -1 or j <= i:
        return []
    try:
        return json.loads(text[i : j + 1])
    except json.JSONDecodeError:
        return []


_BAD_MODELS = set()
_ANNOUNCED = set()
SYSTEM_MSG = (
    "Você é um assistente de pesquisa de vagas de emprego remotas de design. Use a busca na web e "
    "confie apenas em páginas de vagas reais (site da empresa, LinkedIn, Gupy, Remote OK etc.). Nunca invente datas ou URLs. "
    "Só informe um prazo se a data aparecer escrita nos resultados da busca; nunca use a data de hoje como prazo "
    "e nunca estime pelo ano anterior. Se o prazo não estiver claro, deixe o campo deadline como null. "
    "Ignore edições cujo prazo já passou."
)


def _missing(e):
    t = str(e)
    return "model_not_found" in t or "does not exist" in t or "per day" in t or "tokens per day" in t


def _announce(label):
    if label not in _ANNOUNCED:
        print(f"usando: {label}", file=sys.stderr)
        _ANNOUNCED.add(label)


def _chat(model, prompt, key, browser=False, url=None):
    payload = {
        "model": model,
        "temperature": 0.1,
        "messages": [{"role": "system", "content": SYSTEM_MSG}, {"role": "user", "content": prompt}],
    }
    url = url or GROQ_URL
    payload["max_tokens" if url == MISTRAL_URL else "max_completion_tokens"] = 950 if model.startswith("qwen/") else 3500
    if model.startswith("openai/gpt-oss"):
        payload["reasoning_effort"] = "low"
    if browser:
        payload["tools"] = [{"type": "browser_search"}]
    res = http_json(url, payload, headers={"Authorization": f"Bearer {key}"})
    return extract_json_array(res["choices"][0]["message"]["content"] or "")


def tavily_context(query):
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        return None
    res = http_json(
        TAVILY_URL,
        {"query": query[:380], "max_results": 12, "search_depth": "basic"},
        headers={"Authorization": f"Bearer {key}"},
        timeout=60,
    )
    rows = []
    for n, r in enumerate(res.get("results", []), 1):
        rows.append(f"[{n}] {r.get('title', '')}\n{r.get('url', '')}\n{(r.get('content') or '')[:450]}")
    return "\n\n".join(rows)


def groq_search(prompt, query=None):
    """1) sistemas com busca embutida (compound); 2) Tavily + modelo aberto; 3) modelo com browser_search."""
    key = os.environ["GROQ_API_KEY"]
    last = None
    for model in CFG.get("models", [CFG["model"]]):
        if model in _BAD_MODELS:
            continue
        try:
            out = _chat(model, prompt, key)
        except RuntimeError as e:
            if _missing(e):
                print(f"modelo {model} indisponível (sem acesso ou cota diária esgotada), tentando o próximo", file=sys.stderr)
                _BAD_MODELS.add(model)
                last = e
                continue
            raise
        _announce(model)
        return out

    if query and os.environ.get("TAVILY_API_KEY"):
        ctx = tavily_context(query)
        if ctx:
            p2 = (
                prompt
                + "\n\nUse APENAS os resultados de busca abaixo como fonte (não invente nada fora deles; "
                "se não houver data confirmada, omita o item):\n\n" + ctx
            )
            mkey = os.environ.get("MISTRAL_API_KEY")
            for model in CFG.get("mistral_models", []) if mkey else []:
                tag = "mistral:" + model
                if tag in _BAD_MODELS:
                    continue
                try:
                    out = _chat(model, p2, mkey, url=MISTRAL_URL)
                except RuntimeError as e:
                    print(f"Mistral {model} falhou ({str(e)[:120]}), tentando o próximo", file=sys.stderr)
                    _BAD_MODELS.add(tag)
                    last = e
                    continue
                _announce(f"Mistral {model} + Tavily")
                return out
            for model in CFG.get("rag_models", []):
                if model in _BAD_MODELS:
                    continue
                try:
                    out = _chat(model, p2, key)
                except RuntimeError as e:
                    if _missing(e):
                        print(f"modelo {model} indisponível (sem acesso ou cota diária esgotada), tentando o próximo", file=sys.stderr)
                        _BAD_MODELS.add(model)
                        last = e
                        continue
                    raise
                _announce(f"{model} + Tavily")
                return out

    for model in CFG.get("browser_models", []):
        if model in _BAD_MODELS:
            continue
        try:
            out = _chat(model, prompt, key, browser=True)
        except RuntimeError as e:
            if _missing(e):
                _BAD_MODELS.add(model)
                last = e
                continue
            raise
        _announce(f"{model} + browser_search")
        return out
    raise last or RuntimeError("nenhum modelo disponível")


def theme_prompt(theme):
    return (
        f"Hoje é {TODAY.isoformat()}. Pesquise vagas de emprego REMOTAS abertas agora sobre: {theme}. Liste o MÁXIMO possível (pelo menos 10).\n"
        f"Só vagas 100% remotas, publicadas nos últimos 30 dias e ainda abertas. Considere páginas em português e em inglês. "
        f"Informe empresa, título, nível, escopo remoto (Brasil, Worldwide ou país), salário se divulgado e data de publicação. "
        f"Só inclua vagas com URL real da vaga.\n\n{SCHEMA}"
    )


event_prompt = theme_prompt


def watch_prompt(names):
    return (
        f"Hoje é {TODAY.isoformat()}. Pesquise nestes sites de vagas as vagas REMOTAS de design abertas agora e publicadas nos "
        f"últimos 30 dias: {', '.join(names)}.\nInforme empresa, título, nível, escopo remoto, salário e data de publicação. "
        f"Só vagas com URL real.\n\n{SCHEMA}"
    )


def _head(t):
    return re.split(r"[(:]", t)[0].strip()


def collect_items(mock=None):
    if mock:
        return json.loads(Path(mock).read_text(encoding="utf-8"))
    jobs = []  # (prompt, consulta curta, categoria)
    rot = TODAY.timetuple().tm_yday
    bo, bb = CFG["boards_world"], CFG["boards_br"]
    go = [bo[k : k + 3] for k in range(0, len(bo), 3)]
    gb = [bb[k : k + 3] for k in range(0, len(bb), 3)]
    for a in CFG["areas"]:
        for t in a["themes"]:
            jobs.append((theme_prompt(t), f"{a['q_en']} remote jobs hiring", a["key"]))
        for t in a["pt_themes"]:
            jobs.append((theme_prompt(t), f"vagas remotas {a['q_pt']} contratando", a["key"]))
        g = go[rot % len(go)]
        jobs.append((watch_prompt(g), f"{' '.join(g)} remote {a['q_en']} jobs", a["key"]))
        g = gb[rot % len(gb)]
        jobs.append((watch_prompt(g), f"{' '.join(g)} vagas remoto {a['q_pt']}", a["key"]))
    g = CFG["boards_design"]
    jobs.append((watch_prompt(g), f"{' '.join(g)} remote design jobs", None))
    items = []
    for n, (p, q, cat) in enumerate(jobs, 1):
        try:
            got = groq_search(p, q)
            for g_ in got:
                if isinstance(g_, dict) and cat and g_.get("category") not in CAT_KEYS:
                    g_["category"] = cat
            print(f"[{n}/{len(jobs)}] {len(got)} itens", file=sys.stderr)
            items.extend(got)
        except Exception as e:
            print(f"[{n}/{len(jobs)}] falhou: {e}", file=sys.stderr)
        time.sleep(5)
    return items


def lookup_milestones(name, url):
    """Vagas não têm datas públicas pós-candidatura: você adiciona com /data."""
    return []


# ---------------------------------------------------------------- normalização
def norm_key(it):
    base = f"{it.get('company') or ''} {it.get('name') or it.get('acronym') or ''}".lower()
    base = re.sub(r"[^a-z0-9]+", "", base)
    return f"{base}|{(it.get('level') or '')[:3].lower()}"


def _date(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def clean(items):
    out = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        url = str(it.get("url") or "")
        if not url.startswith("http") or not (it.get("name") or it.get("acronym")):
            continue
        dl, posted = _date(it.get("deadline")), _date(it.get("posted"))
        real = dl is not None and dl > TODAY
        if posted and posted > TODAY:
            posted = TODAY
        expires = dl if real else (posted or TODAY) + dt.timedelta(days=CFG.get("job_ttl_days", 30))
        if expires <= TODAY:
            continue
        it["deadline"] = expires.isoformat()
        it["real_deadline"] = bool(real)
        it["posted"] = posted.isoformat() if posted else None
        loc = str(it.get("location") or "")
        if it.get("kind") not in KIND_ICON:
            it["kind"] = "brasil" if re.search(r"brasil|brazil", loc.lower()) else "mundo"
        it["category"] = it.get("category") if it.get("category") in CAT_KEYS else "geral"
        if not it.get("acronym"):
            it["acronym"] = f"{it.get('name')} · {it.get('company')}" if it.get("company") else it.get("name")
        k = norm_key(it)
        if k in out and out[k]["deadline"] <= it["deadline"]:
            continue
        out[k] = it
    return out


# ---------------------------------------------------------------- formatação
def br(d):
    return dt.date.fromisoformat(d).strftime("%d/%m/%Y")


def days_left(d):
    return (dt.date.fromisoformat(d) - TODAY).days


def is_priority(it):
    s = f"{it.get('name', '')} {it.get('acronym', '')} {it.get('parent_conference', '')}".lower()
    return any(k in s for k in CFG["priority_keywords"])


KIND_ICON = {"brasil": "🇧🇷", "mundo": "🌍"}
KIND_RANK = {"brasil": 0, "mundo": 1}
KIND_NAME = {"brasil": "remoto Brasil", "mundo": "remoto mundo"}


def fmt_compact(it):
    star = "⭐ " if is_priority(it) else ""
    flag = KIND_ICON.get(it["kind"], "🌍")
    title = esc(it.get("name") or it.get("acronym"))
    company = f" · {esc(it['company'])}" if it.get("company") else ""
    num = f"#{it['id']} " if it.get("id") else ""
    meta = [esc(it.get("location") or KIND_NAME.get(it["kind"], "remoto"))]
    if it.get("level") and str(it["level"]).lower() not in ("null", "none"):
        meta.append(esc(it["level"]))
    if it.get("salary") and str(it["salary"]).lower() not in ("null", "none"):
        meta.append("💰 " + esc(it["salary"]))
    if it.get("real_deadline"):
        when = f"⏳ candidatar até {br(it['deadline'])} · faltam {days_left(it['deadline'])} dias"
    elif it.get("posted"):
        ago = (TODAY - dt.date.fromisoformat(it["posted"])).days
        when = f"🕒 publicada em {br(it['posted'])} ({'hoje' if ago <= 0 else f'há {ago} dias'})"
    else:
        when = "🕒 data de publicação não informada"
    return (
        f"{star}{flag} {num}<b>{title}</b>{company}\n"
        f"🏠 {' · '.join(meta)}\n"
        f"{when} · "
        f'<a href="{html.escape(it["url"], quote=True)}">ver vaga</a>'
    )


fmt_item = fmt_compact


def order(items):
    recent = sorted(items, key=lambda i: i.get("posted") or "0000", reverse=True)
    return sorted(recent, key=lambda i: (KIND_RANK.get(i["kind"], 1), not is_priority(i)))


def build_messages(sections):
    """sections: [(titulo, [blocos de texto])] -> lista de textos <= 4000 chars."""
    msgs, cur = [], ""
    for title, blocks in sections:
        head = f"<b>{title}</b>\n\n"
        started = False
        for b in blocks:
            body = b + "\n\n"
            if cur and len(cur) + len(body) + len(head) > 3900:
                msgs.append(cur)
                cur, started = "", False  # nova mensagem repete o título da seção
            if not started:
                cur += head
                started = True
            cur += body
    if cur.strip():
        msgs.append(cur)
    return msgs


# ---------------------------------------------------------------- Telegram
def tg(method, payload):
    tok = os.environ["TELEGRAM_BOT_TOKEN"]
    return http_json(f"https://api.telegram.org/bot{tok}/{method}", payload, timeout=60)


def _chunks(text, size=3800):
    out, cur = [], ""
    for line in text.strip().split("\n"):
        if cur and len(cur) + len(line) + 1 > size:
            out.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        out.append(cur)
    return out


def send(text):
    for part in _chunks(text):
        tg(
            "sendMessage",
            {
                "chat_id": os.environ["TELEGRAM_CHAT_ID"],
                "text": part.strip(),
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )


def get_chat_id():
    for u in tg("getUpdates", {}).get("result", []):
        m = u.get("message") or u.get("channel_post") or {}
        c = m.get("chat")
        if c:
            print(f"chat_id={c['id']}  ({c.get('first_name') or c.get('title')})")


# ---------------------------------------------------------------- estado
def load_state():
    st = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    st.setdefault("events", {})
    st.setdefault("subs", [])
    st.setdefault("offset", 0)
    st.setdefault("next_id", 1)
    st.setdefault("next_sub", 1)
    st.setdefault("ignored", [])
    return st


def save_state(st):
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def due_thresholds(left, reminded, days):
    """True (e marca como avisado) se cruzou um limiar de lembrete ainda não avisado."""
    if any(left <= r and r not in reminded for r in days):
        reminded.extend(x for x in days if x >= left and x not in reminded)
        return True
    return False


# ---------------------------------------------------------------- seus artigos
HELP = (
    "<b>Comandos</b>\n"
    "/vagas: lista as vagas abertas, com número (#)\n"
    "/candidatei 12: registra que você se candidatou à vaga #12\n"
    "/candidatei Designer na Empresa: registra uma vaga que não está na lista\n"
    "/minhas: suas candidaturas e as próximas datas\n"
    "/status S1 entrevista | oferta | aceito | rejeitado | retirado\n"
    "/data S1 Entrevista 2026-12-01: guarda uma data (entrevista, teste, resposta)\n"
    "/remover S1: apaga a candidatura\n"
    "/ignorar 12: esconde a vaga #12 e não avisa mais dela\n\n"
    "Respondo em até ~3h e lembro 14, 7 e 2 dias antes de cada data, e no dia. 💅"
)


def sub_by_id(st, tok):
    try:
        n = int(re.sub(r"^[sS]", "", tok))
    except ValueError:
        return None
    return next((s for s in st["subs"] if s["id"] == n), None)


def fmt_sub(s):
    lines = [f"<b>S{s['id']} · {esc(s['name'])}</b> — {esc({'vou': 'vai participar', 'submetido': 'candidatada'}.get(s['status'], s['status']))}"]
    info = []
    if s.get("location"):
        info.append(f"📍 {esc(s['location'])}")
    if s.get("event_date"):
        end = f" a {br(s['event_end'])}" if s.get("event_end") else ""
        info.append(f"🗓 {br(s['event_date'])}{end}")
    if info:
        lines.append("  " + "  ".join(info))
    ms = sorted((m for m in s["milestones"] if days_left(m["date"]) >= 0), key=lambda m: m["date"])
    for m in ms:
        left = days_left(m["date"])
        lines.append(f"  • {esc(m['label'])}: {br(m['date'])} ({'hoje' if left == 0 else f'em {left} dias'})")
    if not ms:
        lines.append("  (sem datas futuras; use /data para adicionar)")
    if s.get("url"):
        lines.append(f'  🔗 <a href="{html.escape(s["url"], quote=True)}">site</a>')
    return "\n".join(lines)


def apply_event_info(sub, found):
    for f in found:
        if f.get("location") and not sub.get("location"):
            sub["location"] = f["location"]
        if f["label"].lower().startswith("event") and not sub.get("event_date"):
            sub["event_date"] = f["date"]


def merge_milestones(sub, found):
    """Atualiza datas vindas da busca; não sobrescreve as manuais. Retorna textos do que mudou."""
    changes = []
    by_label = {m["label"].lower(): m for m in sub["milestones"]}
    for f in found:
        cur = by_label.get(f["label"].lower())
        if cur is None:
            sub["milestones"].append({"label": f["label"], "date": f["date"], "reminded": []})
            changes.append(f"nova: {f['label']} em {br(f['date'])}")
        elif not cur.get("manual") and cur["date"] != f["date"]:
            changes.append(f"{cur['label']}: {br(cur['date'])} → {br(f['date'])}")
            cur["date"], cur["reminded"] = f["date"], []
    return changes


def handle(text, st):
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lstrip("/").split("@")[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("start", "ajuda", "help"):
        return pick(
            "💋 <b>Radar Vagas</b> · SISSS, oi! Sou o radar de vagas remotas de design: gráfico, UI/UX, produto, móveis e motion, no Brasil e no mundo. Vamos arrumar esse emprego, babilônica? ✨\n\n",
            "👠 <b>Radar Vagas</b> · Oi, diva! Chegou a madrinha dos prazos. Vagas remotas do mundo todo: eu cuido do chá, você cuida do brilho 💅\n\n",
        ) + HELP

    if cmd in ("eventos", "vagas"):
        evs = sorted(st["events"].values(), key=lambda i: i.get("posted") or "0000", reverse=True)
        if not evs:
            return "Ainda não tenho vagas na lista. A busca completa roda segunda e quinta, às 12h."
        rows = [
            f"#{e.get('id', '?')} · {KIND_ICON.get(e['kind'], '🌍')} {esc(e.get('name') or e.get('acronym'))} · {esc(e.get('company') or '')} · {esc(e.get('location') or 'remoto')}"
            for e in evs
        ]
        return "<b>Na passarela agora</b>\n" + "\n".join(rows) + "\n\nCandidatou: /candidatei número · Sem interesse: /ignorar número"

    if cmd in ("candidatei", "candidatura", "inscrevi", "submeti", "submetido", "submetida", "inscrita"):
        if not arg:
            return "Use /candidatei 12 (número da vaga) ou /candidatei Nome da vaga e empresa."
        ev = None
        if arg.lstrip("#").isdigit():
            ev = next((e for e in st["events"].values() if str(e.get("id")) == arg.lstrip("#")), None)
            if not ev:
                return "Não achei esse número. Veja /vagas."
        name = (ev.get("acronym") or ev["name"]) if ev else arg
        url = ev.get("url") if ev else None
        sub = {"id": st["next_sub"], "name": name, "url": url, "status": "submetido", "milestones": [], "created": TODAY.isoformat()}
        st["next_sub"] += 1
        if ev:
            sub["location"] = ev.get("location") or None
            try:
                sub["event_date"] = dt.date.fromisoformat(str(ev.get("event_date"))[:10]).isoformat()
            except ValueError:
                pass
        found = lookup_milestones(name, url)
        merge_milestones(sub, found)
        apply_event_info(sub, found)
        st["subs"].append(sub)
        extra = "" if found else (
            "\n\nQuando marcarem entrevista ou prazo, me conta com "
            f"/data S{sub['id']} Entrevista 2026-12-01"
        )
        cheer = pick(
            "CANDIDATOU!!! 👑 Arrasou, BABILÔNICA! Currículo no close, portfólio na lacração. Guardei tudo, agora é hidratar e esperar o RH, sis 💅✨",
            "Condragulations, diva! 🎉 Candidatura enviada, passou na passarela. Eu cuido das datas, você cuida do brilho ✨",
            "ENTREGOU, MÃE! 🔥 Candidatura registrada. Tá montada e pronta pra entrevista 👠💋",
        )
        return f"{cheer}\n\n{fmt_sub(sub)}{extra}\n\nBoa sorte, diva! 🍀"

    if cmd in ("vou", "vouir"):
        if not arg:
            return "Use /vou 12 (número do evento) ou /vou Nome do evento."
        ev = None
        if arg.lstrip("#").isdigit():
            ev = next((e for e in st["events"].values() if str(e.get("id")) == arg.lstrip("#")), None)
            if not ev:
                return "Não achei esse número. Veja /vagas."
        name = (ev.get("acronym") or ev["name"]) if ev else arg
        sub = {
            "id": st["next_sub"], "name": name, "url": ev.get("url") if ev else None, "status": "vou",
            "milestones": [], "created": TODAY.isoformat(), "location": (ev.get("location") if ev else None) or None,
        }
        st["next_sub"] += 1
        try:
            d = dt.date.fromisoformat(str(ev.get("event_date"))[:10]) if ev else None
        except ValueError:
            d = None
        if d and d >= TODAY:
            sub["event_date"] = d.isoformat()
            sub["milestones"].append({"label": "Evento", "date": d.isoformat(), "reminded": []})
        st["subs"].append(sub)
        missing = []
        if not sub.get("location"):
            missing.append(f"/local S{sub['id']} Cidade, País")
        if not sub.get("event_date"):
            missing.append(f"/evento S{sub['id']} AAAA-MM-DD")
        tail = ("\n\nMe conta pra eu guardar:\n" + "\n".join(missing)) if missing else ""
        return pick(
            f"VAI BRILHAR!!! ✈️👠 Tá confirmada no {esc(name)}, sis! Guardei tudo, e eu aviso quando a data chegar perto 💋",
            f"SISSS, você VAI! 🎟✨ {esc(name)} que se prepare, a diva tá chegando! Anotado na turnê 💅",
        ) + "\n\n" + fmt_sub(sub) + tail

    if cmd == "minhas":
        if not st["subs"]:
            return "Você ainda não registrou nenhuma candidatura. Use /candidatei."
        return "<b>Suas candidaturas</b>\n\n" + "\n\n".join(fmt_sub(s) for s in st["subs"])

    if cmd == "status":
        tok, _, val = arg.partition(" ")
        s = sub_by_id(st, tok) if tok else None
        new = STATUS_ALIAS.get(re.sub(r"[\s_-]", "", val.lower()))
        if not s or not new:
            return "Use /status S1 entrevista, oferta, aceito, rejeitado ou retirado."
        s["status"] = new
        sid = f"S{s['id']}"
        if new == "aceito":
            msg = pick(
                f"ACEITAAAA!!! 🏆👑 SERVIU CUNT, MONSTRA! LACROU TUDO! Condragulations, {esc(s['name'])} é SEU! Mamou todo mundo 💅🔥🎉",
                f"TOMBOU GERAL!!! 💥 ACEITARAM {esc(s['name'])}! SISSS, você é uma estrela, uma ícone, uma lenda! The time has come de brilhar no palco ✨👠",
            )
            return msg + "\n\nSe quiser guardar uma data (início, prazo da proposta): /data " + sid + " Início 2027-01-10"
        if new == "rejeitado":
            return pick(
                f"Sashay away?? NUNCA, amiga! 💔👠 Rejeição é só o RH sem enxergar a arte. {esc(s['name'])} só não achou o palco certo ainda. Hidrata, chora no banho e volta mais montada: /vagas",
                f"Ai, sis, o bafo foi ruim 🥲 mas ninguém lacra sem passar perrengue. {esc(s['name'])} merece um palco melhor. Bora pro próximo? 💅",
            ) + " Paro de lembrar das datas dele."
        if new == "retirado":
            return f"{esc(s['name'])} saiu de cena com dignidade, tipo diva que sabe a hora de descer do palco 🎭 Paro de lembrar das datas dele."
        if new == "em entrevista":
            return pick(
                f"ENTREVISTA!!! 🎤👑 Chamaram você pra {esc(s['name'])}, sis! Respira, hidrata e entra montada. Quando tiver data: /data {sid} Entrevista AAAA-MM-DD",
                f"SISSS, passou na triagem! 💅🔥 {esc(s['name'])} quer te conhecer. Bota o portfólio no close! Anota a data: /data {sid} Entrevista AAAA-MM-DD",
            )
        if new == "oferta":
            return pick(
                f"PROPOSTA!!! 💰👑 {esc(s['name'])} te quer, MONSTRA! Negocia com a pose de quem sabe o que vale 💅✨",
                f"TOMBOU, diva! 🎉 Veio oferta de {esc(s['name'])}. Lê tudo, pergunta tudo e só aceita se for lacração 👠",
            )
        if new == "em revisão":
            return pick(
                f"Os jurados estão deliberando {esc(s['name'])}... respira, hidrata e mantém a pose 💅✨ ({sid} anotado)",
                f"Em revisão, sis. Reviewer 2 que se prepare, porque a obra é icônica 👑 ({sid} anotado)",
            )
        return f"Atualizado: {sid} está {esc(new)}."

    if cmd == "data":
        m = re.match(r"(\S+)\s+(.+?)\s+(\d{4}-\d{2}-\d{2})$", arg)
        s = sub_by_id(st, m.group(1)) if m else None
        if not m or not s:
            return "Use /data S1 Entrevista 2026-12-01 (data no formato AAAA-MM-DD)."
        try:
            dt.date.fromisoformat(m.group(3))
        except ValueError:
            return "Data inválida. Use AAAA-MM-DD."
        label, date = m.group(2), m.group(3)
        cur = next((x for x in s["milestones"] if x["label"].lower() == label.lower()), None)
        if cur:
            cur.update(date=date, reminded=[], manual=True)
        else:
            s["milestones"].append({"label": label, "date": date, "reminded": [], "manual": True})
        return f"Anotado em S{s['id']}: {esc(label)} em {br(date)}."

    if cmd == "agenda":
        ac = sorted((x for x in st["subs"] if x["status"] in ("aceito", "vou")), key=lambda x: x.get("event_date") or "9999")
        if not ac:
            return "A turnê ainda tá vazia, sis, mas é questão de tempo 💅 Quando chamarem pra entrevista: /status S1 entrevista"
        rows = []
        for x in ac:
            when = br(x["event_date"]) if x.get("event_date") else "data a confirmar"
            if x.get("event_end"):
                when += f" a {br(x['event_end'])}"
            left = f" (faltam {days_left(x['event_date'])} dias)" if x.get("event_date") and days_left(x["event_date"]) >= 0 else ""
            rows.append(f"<b>{esc(x['name'])}</b> (S{x['id']})\n📍 {esc(x.get('location') or 'local a confirmar')}\n🗓 {when}{left}")
        return "<b>✈️ A TURNÊ DA DIVA</b>\n\n" + "\n\n".join(rows)

    if cmd == "local":
        tok, _, place = arg.partition(" ")
        x = sub_by_id(st, tok) if tok else None
        if not x or not place.strip():
            return "Use /local S1 Paris, França."
        x["location"] = place.strip()[:80]
        return f"Anotado, diva! 📍 {esc(x['name'])} vai ser em {esc(x['location'])}."

    if cmd == "evento":
        m = re.match(r"(\S+)\s+(\d{4}-\d{2}-\d{2})(?:\s+(?:a\s+)?(\d{4}-\d{2}-\d{2}))?$", arg)
        x = sub_by_id(st, m.group(1)) if m else None
        if not m or not x:
            return "Use /evento S1 2027-12-07 ou /evento S1 2027-12-07 2027-12-11."
        try:
            d1 = dt.date.fromisoformat(m.group(2))
            d2 = dt.date.fromisoformat(m.group(3)) if m.group(3) else None
        except ValueError:
            return "Data inválida. Use AAAA-MM-DD."
        x["event_date"] = d1.isoformat()
        x["event_end"] = d2.isoformat() if d2 else None
        cur = next((k for k in x["milestones"] if k["label"].lower() == "evento"), None)
        if cur:
            cur.update(date=d1.isoformat(), reminded=[], manual=True)
        else:
            x["milestones"].append({"label": "Evento", "date": d1.isoformat(), "reminded": [], "manual": True})
        return f"Guardado! 🗓 {esc(x['name'])}: {br(d1.isoformat())}" + (f" a {br(d2.isoformat())}" if d2 else "") + ". Eu aviso quando chegar perto ✈️"

    if cmd in ("ignorar", "ignora"):
        k = next((k for k, e in st["events"].items() if str(e.get("id")) == arg.lstrip("#")), None)
        if not k:
            return "Use /ignorar 12 (número da vaga, veja /vagas)."
        name = st["events"].pop(k)
        st["ignored"].append(k)
        return pick("Próxima, sis! 💅", "Sashay away, vaga! 👋", "Não é o meu babado. Tchau! ✨") + f" Não aviso mais de {esc(name.get('name') or name.get('acronym'))}."

    if cmd in ("remover", "apagar"):
        s = sub_by_id(st, arg)
        if not s:
            return "Use /remover S1."
        st["subs"].remove(s)
        return f"Removi S{s['id']} ({esc(s['name'])})."

    return "Sis, não entendi 🥲 Manda /ajuda que a madrinha explica de novo 💋"


def run_poll(st):
    for u in tg("getUpdates", {"offset": st["offset"], "timeout": 0}).get("result", []):
        st["offset"] = u["update_id"] + 1
        m = u.get("message") or {}
        if str((m.get("chat") or {}).get("id")) != str(os.environ["TELEGRAM_CHAT_ID"]):
            continue  # ignora quem não é você
        text = (m.get("text") or "").strip()
        if text:
            send(handle(text, st))


# ---------------------------------------------------------------- principal
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reminders", action="store_true")
    ap.add_argument("--poll", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--mock")
    ap.add_argument("--say")
    ap.add_argument("--get-chat-id", action="store_true")
    a = ap.parse_args()

    if a.get_chat_id:
        return get_chat_id()

    st = load_state()

    if a.say:
        print(handle(a.say, st))
        if not a.dry_run:
            save_state(st)
        return

    if not a.dry_run:
        run_poll(st)  # responde seus comandos antes de tudo
        if a.poll:
            save_state(st)
            return

    # eventos vencidos saem da lista
    st["events"] = {k: v for k, v in st["events"].items() if days_left(v["deadline"]) >= 0}

    new_items, changed = [], []
    if not a.reminders and not a.poll:
        found = clean(collect_items(a.mock))
        for k, it in found.items():
            if k in st["ignored"]:
                continue
            old = st["events"].get(k)
            if old is None:
                it["id"] = st["next_id"]
                st["next_id"] += 1
                it["reminded"] = []
                st["events"][k] = it
                new_items.append(it)
            else:
                it["id"] = old["id"]
                if it.get("real_deadline") and old.get("real_deadline") and old["deadline"] != it["deadline"]:
                    it["reminded"] = []
                    changed.append(it)
                elif it.get("real_deadline") and not old.get("real_deadline"):
                    it["reminded"] = []
                else:
                    it["reminded"] = old.get("reminded", [])
                if not it.get("real_deadline"):
                    it["deadline"] = old["deadline"]
                st["events"][k] = it

    # lembretes de prazos de submissão
    fresh = {id(i) for i in new_items + changed}
    due = []
    for it in st["events"].values():
        if it.get("real_deadline") and id(it) not in fresh and due_thresholds(days_left(it["deadline"]), it.setdefault("reminded", []), CFG["reminder_days"]):
            due.append(it)

    # datas dos seus artigos: reconfere na busca completa e lembra
    ms_changes = []
    if not a.reminders and not a.poll and not a.mock and os.environ.get("GROQ_API_KEY"):
        for s in st["subs"]:
            if s["status"] not in INACTIVE:
                got = lookup_milestones(s["name"], s.get("url"))
                apply_event_info(s, got)
                for c in merge_milestones(s, got):
                    ms_changes.append(f"<b>S{s['id']} · {esc(s['name'])}</b>: {esc(c)}")
    ms_due = []
    for s in st["subs"]:
        if s["status"] in INACTIVE:
            continue
        for m in s["milestones"]:
            left = days_left(m["date"])
            if left >= 0 and due_thresholds(left, m.setdefault("reminded", []), MS_DAYS):
                when = "hoje" if left == 0 else f"em {left} dias ({br(m['date'])})"
                loc = f"\n📍 {esc(s['location'])}" if s.get("location") else ""
                ms_due.append(f"<b>S{s['id']} · {esc(s['name'])}</b>\n{esc(m['label'])}: {when}{loc}")

    sections = [
        ("📬 ATENÇÃO, SIS: o relógio do runway tá correndo", ms_due),
        ("🔁 Ué, mudaram as datas das suas candidaturas! Que babado", ms_changes),
    ]
    for key, ar in list(CATS.items()) + [("geral", {"label": "Geral", "emoji": "🌐"})]:
        blk = [i for i in new_items if i.get("category") == key]
        sections.append((f"{ar['emoji']} {ar['label']}: vagas fresquinhas ☕", [fmt_compact(i) for i in order(blk)]))
    sections += [
        ("🔄 Prazo da vaga mudou", [fmt_compact(i) for i in order(changed)]),
        ("⏰ CORRE, MONSTRA: a vaga fecha logo! 👠", [fmt_compact(i) for i in order(due)]),
    ]
    msgs = build_messages(sections)
    full = not a.reminders and not a.poll
    if full and not msgs:
        msgs = [pick("Sis, calmaria: nenhuma vaga nova. Aproveita pra atualizar o portfólio e hidratar 💧✨", "Nada de vaga nova agora, diva. A madrinha volta já já 💋")]
    if full and msgs:
        msgs[0] = (
            f"📡 <b>Radar Vagas</b> · {TODAY.strftime('%d/%m/%Y')} · {len(new_items)} novo(s)\n"
            + pick(
                "💋 BAFO! Chegou o Radar da madrinha com as vagas quentinhas, sis! ☕✨",
                "SENHORAS E SENHORES, o runway de vagas abriu! 👠✨ Bora conseguir esse emprego, babilônica?",
                "SISSS, o babado é certo: tem vaga nova no pedaço! 💅🔥",
            )
            + "\nCandidatou: /candidatei número · Sem interesse: /ignorar número · /ajuda\n\n" + msgs[0]
        )

    for m in msgs:
        if a.dry_run:
            print(m, "\n" + "=" * 40)
        else:
            send(m)
            time.sleep(1)
    if not a.dry_run:
        save_state(st)
    print(
        f"novos={len(new_items)} mudaram={len(changed)} lembretes={len(due)} datas={len(ms_due)} mensagens={len(msgs)}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
