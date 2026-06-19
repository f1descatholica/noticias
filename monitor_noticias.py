#!/usr/bin/env python3
"""
Monitor de Notícias Católicas -> JSON para o Blogger
------------------------------------------------------
Lê múltiplos feeds RSS, filtra por palavras-chave,
traduz título + resumo para português (Google Translate
gratuito, via deep-translator, sem precisar de chave de API),
e ACUMULA o resultado em `docs/noticias.json`.

Notícias antigas nunca são apagadas — só são adicionadas
as novas que ainda não estavam lá.

Esse JSON é publicado via GitHub Pages e consumido por um
JavaScript dentro de uma página do Blogger (ver blogger_widget.html).
"""

import re
import sys
import json
import time
import requests
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timezone
from deep_translator import GoogleTranslator

# ===================== CONFIGURAÇÃO =====================

# Lista de feeds RSS a monitorar.
# Para adicionar ou remover um site, edite apenas esta lista.
FEEDS = [
    {
        "nome": "Rorate Caeli",
        "url": "http://rorate-caeli.blogspot.com/feeds/posts/default?alt=rss",
    },
    {
        "nome": "Radical Fidelity",
        "url": "https://radicalfidelity.substack.com/feed",
    },
    {
        "nome": "Traditionsanity",
        "url": "https://www.traditionsanity.com/feed/",
    },
]

# Palavras-chave em INGLÊS (idioma original dos sites).
# O filtro é case-insensitive e busca no título + resumo de cada post.
PALAVRAS_CHAVE = [
    "pope leo",
    "leo xiv",
]

# Pasta "docs" é a que o GitHub Pages publica por padrão
ARQUIVO_SAIDA = Path(__file__).parent / "docs" / "noticias.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

_tradutor = GoogleTranslator(source="en", target="pt")


# ===================== TRADUÇÃO (gratuita, sem API key) =====================

def traduzir(texto: str) -> str:
    """Traduz inglês -> português usando o Google Translate gratuito."""
    texto = texto.strip()
    if not texto:
        return texto
    try:
        return _tradutor.translate(texto)
    except Exception as e:
        print(f"  [aviso] falha ao traduzir trecho ({e}); mantendo original.")
        return texto


# ===================== LEITURA DE FEED RSS =====================

# Namespaces comuns em feeds RSS/Atom
NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc":      "http://purl.org/dc/elements/1.1/",
    "atom":    "http://www.w3.org/2005/Atom",
}


def limpar_html(texto: str) -> str:
    """Remove tags HTML de um texto e normaliza espaços."""
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def truncar(texto: str, max_chars: int = 400) -> str:
    """Trunca o texto no limite de caracteres, quebrando em palavra inteira."""
    if len(texto) <= max_chars:
        return texto
    truncado = texto[:max_chars].rsplit(" ", 1)[0]
    return truncado + "…"


def baixar_feed(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def extrair_itens_feed(xml_texto: str, nome_feed: str) -> list[dict]:
    """
    Parseia um feed RSS 2.0 ou Atom e retorna lista de itens.
    Cada item: {titulo, link, resumo, fonte}
    """
    itens = []
    try:
        root = ET.fromstring(xml_texto)
    except ET.ParseError as e:
        print(f"  [erro] falha ao parsear XML do feed '{nome_feed}': {e}")
        return []

    # Detecta se é RSS 2.0 (tem <channel><item>) ou Atom (tem <entry>)
    tag_raiz = root.tag.lower()

    ns_atom = "{http://www.w3.org/2005/Atom}"
    is_atom = "feed" in root.tag  # cobre <feed> e <{ns}feed>

    if is_atom:
        entradas = root.findall(f"{ns_atom}entry")
        if not entradas:
            entradas = root.findall("entry")
        for entry in entradas:
            titulo_el = entry.find(f"{ns_atom}title")
            if titulo_el is None:
                titulo_el = entry.find("title")
            link_el = entry.find(f"{ns_atom}link")
            if link_el is None:
                link_el = entry.find("link")
            resumo_el = entry.find(f"{ns_atom}summary")
            if resumo_el is None:
                resumo_el = entry.find("summary")
            if resumo_el is None:
                resumo_el = entry.find(f"{ns_atom}content")
            if resumo_el is None:
                resumo_el = entry.find("content")

            titulo = titulo_el.text.strip() if titulo_el is not None and titulo_el.text else ""
            link   = (link_el.get("href") or link_el.text or "").strip() if link_el is not None else ""
            resumo_raw = ""
            if resumo_el is not None:
                resumo_raw = resumo_el.text or ""
            resumo = truncar(limpar_html(resumo_raw))

            if titulo and link:
                itens.append({"titulo": titulo, "link": link, "resumo": resumo, "fonte": nome_feed})

    else:
        # RSS 2.0 (Rorate Caeli, Traditionsanity, WordPress em geral)
        channel = root.find("channel")
        if channel is None:
            channel = root
        for item in channel.findall("item"):
            titulo_el = item.find("title")
            link_el   = item.find("link")
            desc_el   = item.find("description")
            content_el = item.find("content:encoded", NS)

            titulo = titulo_el.text.strip() if titulo_el is not None and titulo_el.text else ""
            link   = link_el.text.strip()   if link_el   is not None and link_el.text   else ""

            # Prefere o conteúdo completo (content:encoded), mas usa description como fallback
            resumo_raw = ""
            if content_el is not None and content_el.text:
                resumo_raw = content_el.text
            elif desc_el is not None and desc_el.text:
                resumo_raw = desc_el.text
            resumo = truncar(limpar_html(resumo_raw))

            if titulo and link:
                itens.append({"titulo": titulo, "link": link, "resumo": resumo, "fonte": nome_feed})

    return itens


# ===================== FILTRO POR PALAVRA-CHAVE =====================

def bate_palavra_chave(noticia: dict, palavras: list[str]) -> bool:
    texto = f"{noticia['titulo']} {noticia['resumo']}".lower()
    return any(p.lower() in texto for p in palavras)


# ===================== ACÚMULO (não apaga nada, só adiciona) =====================

def carregar_existente() -> dict:
    """Carrega o JSON já publicado, se existir. Caso contrário, começa vazio."""
    if ARQUIVO_SAIDA.exists():
        try:
            return json.loads(ARQUIVO_SAIDA.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [aviso] não consegui ler o JSON existente ({e}); começando do zero.")
    return {"noticias": []}


# ===================== PROGRAMA PRINCIPAL =====================

def main():
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Iniciando monitoramento de {len(FEEDS)} feed(s).")

    todos_itens = []
    for feed in FEEDS:
        print(f"\nBaixando feed: {feed['nome']} ({feed['url']})")
        try:
            xml_texto = baixar_feed(feed["url"])
            itens = extrair_itens_feed(xml_texto, feed["nome"])
            print(f"  -> {len(itens)} itens encontrados no feed.")
            todos_itens.extend(itens)
        except requests.RequestException as e:
            print(f"  [erro] não foi possível baixar o feed '{feed['nome']}': {e}")
            continue
        time.sleep(0.5)  # pausa entre feeds para não sobrecarregar

    print(f"\nTotal de itens coletados em todos os feeds: {len(todos_itens)}")

    filtrados = [n for n in todos_itens if bate_palavra_chave(n, PALAVRAS_CHAVE)]
    print(f"  -> {len(filtrados)} batem com as palavras-chave: {PALAVRAS_CHAVE}")

    dados_existentes = carregar_existente()
    noticias_existentes = dados_existentes.get("noticias", [])
    links_existentes = {n["link"] for n in noticias_existentes}

    novas = [n for n in filtrados if n["link"] not in links_existentes]
    print(f"  -> {len(novas)} são novas (ainda não publicadas).")

    if not novas:
        print("Nenhuma notícia nova. JSON permanece como estava.")
        dados_existentes["ultima_verificacao"] = datetime.now(timezone.utc).isoformat()
        ARQUIVO_SAIDA.parent.mkdir(parents=True, exist_ok=True)
        ARQUIVO_SAIDA.write_text(
            json.dumps(dados_existentes, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return

    traduzidas_novas = []
    for i, n in enumerate(novas, 1):
        print(f"Traduzindo {i}/{len(novas)}: [{n['fonte']}] {n['titulo'][:55]}...")
        titulo_pt = traduzir(n["titulo"])
        resumo_pt = traduzir(n["resumo"]) if n["resumo"] else ""
        traduzidas_novas.append({
            "titulo": titulo_pt,
            "titulo_original": n["titulo"],
            "resumo": resumo_pt,
            "link": n["link"],
            "fonte": n["fonte"],
            "adicionado_em": datetime.now(timezone.utc).isoformat(),
        })
        time.sleep(0.5)

    # Acumula: novas no TOPO (mais recentes primeiro), antigas preservadas abaixo.
    lista_final = traduzidas_novas + noticias_existentes

    saida = {
        "atualizado_em": datetime.now(timezone.utc).isoformat(),
        "ultima_verificacao": datetime.now(timezone.utc).isoformat(),
        "feeds_monitorados": [f["nome"] for f in FEEDS],
        "palavras_chave": PALAVRAS_CHAVE,
        "total_noticias": len(lista_final),
        "noticias": lista_final,
    }

    ARQUIVO_SAIDA.parent.mkdir(parents=True, exist_ok=True)
    ARQUIVO_SAIDA.write_text(
        json.dumps(saida, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nJSON atualizado: {ARQUIVO_SAIDA}")
    print(f"  -> {len(novas)} notícia(s) nova(s) adicionada(s).")
    print(f"  -> {len(lista_final)} notícia(s) no total (acumulado).")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Erro inesperado: {e}", file=sys.stderr)
        sys.exit(1)
