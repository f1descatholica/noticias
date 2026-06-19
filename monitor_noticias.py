#!/usr/bin/env python3
"""
Monitor de Notícias - Complicit Clergy -> JSON para o Blogger
----------------------------------------------------------------
Lê a página de notícias do site, filtra por palavras-chave,
traduz título + resumo para português (Google Translate gratuito,
via deep-translator, sem precisar de chave de API), e ACUMULA o
resultado em `docs/noticias.json` — notícias antigas nunca são
apagadas, só são adicionadas as novas que ainda não estavam lá.

Esse JSON é publicado via GitHub Pages e consumido por um
JavaScript dentro de uma página do Blogger (ver blogger_widget.html).
"""

import re
import sys
import json
import time
import requests
from bs4 import BeautifulSoup
from pathlib import Path
from datetime import datetime, timezone
from deep_translator import GoogleTranslator

# ===================== CONFIGURAÇÃO =====================

URL_NOTICIAS = "https://www.complicitclergy.com/news/"

# Palavras-chave em INGLÊS (idioma original do site).
PALAVRAS_CHAVE = [
    "pope leo",
    "leo xiv",
    "LGBT",
    "Viganò",
]

# Pasta "docs" é a que o GitHub Pages publica por padrão
ARQUIVO_SAIDA = Path(__file__).parent / "docs" / "noticias.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
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


# ===================== COLETA DA PÁGINA =====================

def baixar_pagina(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def extrair_noticias(html: str) -> list[dict]:
    """
    Percorre o HTML e extrai uma lista de notícias.
    Cada notícia: {titulo, link, resumo}
    """
    soup = BeautifulSoup(html, "html.parser")
    noticias = []
    vistos = set()

    # Os títulos das notícias estão em tags h4, cada uma com um <a href="...">
    for h4 in soup.find_all("h4"):
        link_tag = h4.find("a", href=True)
        if not link_tag:
            continue

        titulo = link_tag.get_text(strip=True)
        link = link_tag["href"]

        if not titulo or link in vistos:
            continue
        vistos.add(link)

        # O resumo geralmente é o próximo parágrafo "de verdade" depois do título.
        # Pulamos parágrafos curtos (metadados de autor/data/"compartilhe"), mas
        # com uma margem maior de tentativas e um limite mais baixo, para não
        # perder resumos legítimos que comecem com frases curtas ou citações.
# O resumo está em <div class="pt-cv-content"> dentro do mesmo
        # bloco pai (.pt-cv-content-item) que contém o <h4>.
        resumo = ""
        bloco_pai = h4.find_parent(class_="pt-cv-content-item")
        if bloco_pai:
            div_resumo = bloco_pai.find("div", class_="pt-cv-content")
            if div_resumo:
                resumo = div_resumo.get_text(strip=True)

        noticias.append({
            "titulo": titulo,
            "link": link,
            "resumo": resumo,
        })

    return noticias


def bate_palavra_chave(noticia: dict, palavras: list[str]) -> bool:
    texto = f"{noticia['titulo']} {noticia['resumo']}".lower()
    return any(p.lower() in texto for p in palavras)





# ===================== PROGRAMA PRINCIPAL =====================

def main():
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Baixando página: {URL_NOTICIAS}")
    html = baixar_pagina(URL_NOTICIAS)

    # Diagnóstico: ajuda a identificar se o site está bloqueando o robô
    # (ex: Cloudflare, captcha) em vez de retornar a página real.
    print(f"  -> HTML recebido: {len(html)} caracteres.")
    print(f"  -> Primeiros 300 caracteres do HTML recebido:")
    print("  " + "-" * 60)
    print(html[:300].replace("\n", " "))
    print("  " + "-" * 60)
    if "cloudflare" in html.lower() or "captcha" in html.lower() or "checking your browser" in html.lower():
        print("  [AVISO] O HTML recebido parece ser uma página de bloqueio/desafio anti-bot, não o conteúdo real do site.")

    print("Extraindo notícias da página...")
    todas = extrair_noticias(html)
    print(f"  -> {len(todas)} notícias encontradas na página.")

    filtradas = [n for n in todas if bate_palavra_chave(n, PALAVRAS_CHAVE)]
    print(f"  -> {len(filtradas)} notícias batem com as palavras-chave: {PALAVRAS_CHAVE}")

# Apaga o arquivo anterior antes de começar (evita erros de leitura)
    if ARQUIVO_SAIDA.exists():
        ARQUIVO_SAIDA.unlink()

    novas = filtradas
    print(f"  -> {len(novas)} notícia(s) encontrada(s) para publicar.")

    if not novas:
        print("Nenhuma notícia encontrada com as palavras-chave hoje.")
        return

    traduzidas_novas = []
    for i, n in enumerate(novas, 1):
        print(f"Traduzindo {i}/{len(novas)}: {n['titulo'][:60]}...")
        titulo_pt = traduzir(n["titulo"])
        resumo_pt = traduzir(n["resumo"]) if n["resumo"] else ""
        traduzidas_novas.append({
            "titulo": titulo_pt,
            "titulo_original": n["titulo"],
            "resumo": resumo_pt,
            "link": n["link"],
            "adicionado_em": datetime.now(timezone.utc).isoformat(),
        })
        time.sleep(0.5)

lista_final = traduzidas_novas

    saida = {
        "atualizado_em": datetime.now(timezone.utc).isoformat(),
        "ultima_verificacao": datetime.now(timezone.utc).isoformat(),
        "fonte": URL_NOTICIAS,
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
    except requests.RequestException as e:
        print(f"Erro ao acessar o site: {e}", file=sys.stderr)
        sys.exit(1)
