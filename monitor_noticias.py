
#!/usr/bin/env python3
"""
Monitor de Notícias - Complicit Clergy -> JSON para o Blogger
----------------------------------------------------------------
Lê a página de notícias do site, filtra por palavras-chave,
traduz título + resumo para português (Google Translate; se falhar,
tenta MyMemory como reserva; se os dois falharem, publica o texto
original), e ACUMULA o resultado em `docs/noticias.json` — notícias
antigas nunca são apagadas, só são adicionadas as novas que ainda
não estavam lá.

Também mantém um controle interno (dentro do próprio JSON) para:
- avisar (via Issue do GitHub) quando o robô parece estar sendo
  bloqueado pelo site (página sem nenhuma notícia reconhecível),
  repetindo o aviso a cada 3 dias consecutivos de bloqueio;
- avisar (via Issue do GitHub) quando o total de notícias acumuladas
  cruza 300, 325, 350... (a partir de 300, a cada 25).

Esse JSON é publicado via GitHub Pages e consumido por um
JavaScript dentro de uma página do Blogger (ver blogger_widget.html).
"""

import os
import sys
import json
import time
import requests
from bs4 import BeautifulSoup
from pathlib import Path
from datetime import datetime, timezone
from deep_translator import GoogleTranslator, MyMemoryTranslator

# ===================== CONFIGURAÇÃO =====================

URL_NOTICIAS = "https://www.complicitclergy.com/news/"

# Palavras-chave em INGLÊS (idioma original do site).
PALAVRAS_CHAVE = [
    "pope leo",
    "leo xiv",
    "LGBT",
    "Viganò",
    "Postconciliar Rome",
]

# Pasta "docs" é a que o GitHub Pages publica por padrão
ARQUIVO_SAIDA = Path(__file__).parent / "docs" / "noticias.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# Regras dos avisos (Issue no GitHub)
LIMITE_INICIAL_VOLUME = 300   # primeiro aviso de volume acontece a partir daqui
PASSO_VOLUME = 25             # e se repete a cada 25 notícias acumuladas depois disso
DIAS_PARA_AVISO_BLOQUEIO = 3  # avisa a cada N execuções seguidas com a página "vazia"

_tradutor_google = GoogleTranslator(source="en", target="pt")
_tradutor_mymemory = MyMemoryTranslator(source="en-GB", target="pt-BR")


# ===================== TRADUÇÃO (Google -> MyMemory -> original) =====================

def traduzir(texto: str) -> str:
    """
    Traduz inglês -> português.
    1) tenta Google Translate (gratuito, via deep-translator)
    2) se falhar, tenta MyMemory (gratuito, via deep-translator)
    3) se os dois falharem, devolve o texto original (nunca trava o robô)
    """
    texto = texto.strip()
    if not texto:
        return texto

    try:
        return _tradutor_google.translate(texto)
    except Exception as e_google:
        print(f"  [aviso] Google falhou ao traduzir trecho ({e_google}); tentando MyMemory...")

    try:
        return _tradutor_mymemory.translate(texto)
    except Exception as e_mymemory:
        print(f"  [aviso] MyMemory também falhou ({e_mymemory}); mantendo texto original.")

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

        # O resumo está em <div class="pt-cv-content"> dentro do mesmo
        # bloco pai (.pt-cv-content-item) que contém o <h4>. Hoje pegamos
        # diretamente esse bloco (sem varrer múltiplos parágrafos).
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


# ===================== ACÚMULO (não apaga nada, só adiciona) =====================

def carregar_existente() -> dict:
    """Carrega o JSON já publicado, se existir. Caso contrário, começa vazio."""
    if ARQUIVO_SAIDA.exists():
        try:
            dados = json.loads(ARQUIVO_SAIDA.read_text(encoding="utf-8"))
            dados.setdefault("noticias", [])
            dados.setdefault("controle", {})
            dados["controle"].setdefault("bloqueios_consecutivos", 0)
            dados["controle"].setdefault("ultimo_aviso_volume", 0)
            return dados
        except Exception as e:
            print(f"  [aviso] não consegui ler o JSON existente ({e}); começando do zero.")
    return {"noticias": [], "controle": {"bloqueios_consecutivos": 0, "ultimo_aviso_volume": 0}}


# ===================== AVISOS (Issue no GitHub = vira email automático) =====================

def criar_issue_github(titulo: str, corpo: str) -> None:
    """
    Cria uma Issue no repositório do GitHub, o que gera notificação por
    email automaticamente. Se as credenciais não estiverem disponíveis
    (ex: rodando local, fora do GitHub Actions), apenas avisa no console
    e segue em frente — isso nunca deve travar o robô.
    """
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")

    if not token or not repo:
        print(f"  [aviso] não é possível criar Issue fora do GitHub Actions. "
              f"Título que seria enviado: {titulo}")
        return

    url = f"https://api.github.com/repos/{repo}/issues"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    payload = {"title": titulo, "body": corpo}

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=20)
        resp.raise_for_status()
        print(f"  [ok] Issue de aviso criada: {titulo}")
    except Exception as e:
        print(f"  [aviso] falha ao criar Issue de aviso ({e}).")


def verificar_aviso_bloqueio(controle: dict, pagina_veio_vazia: bool) -> None:
    """
    Conta execuções seguidas em que a página não trouxe nenhuma notícia
    reconhecível (sinal de possível bloqueio pelo site). Avisa a cada
    N execuções seguidas nessa condição; zera a contagem assim que a
    página voltar ao normal.
    """
    if pagina_veio_vazia:
        controle["bloqueios_consecutivos"] += 1
        dias = controle["bloqueios_consecutivos"]
        if dias % DIAS_PARA_AVISO_BLOQUEIO == 0:
            criar_issue_github(
                titulo=f"[Robô de notícias] Possível bloqueio há {dias} execuções seguidas",
                corpo=(
                    f"A página {URL_NOTICIAS} voltou sem nenhuma notícia reconhecível "
                    f"em {dias} execuções seguidas do robô. Isso costuma indicar bloqueio "
                    f"anti-robô (Cloudflare/captcha) por parte do site, não falta real de "
                    f"notícias. Vale checar manualmente."
                ),
            )
    else:
        controle["bloqueios_consecutivos"] = 0


def verificar_aviso_volume(controle: dict, total_antes: int, total_agora: int) -> None:
    """
    Avisa quando o total acumulado cruza 300, 325, 350... (a partir de
    300, a cada 25). Usa o último degrau avisado, guardado no controle,
    para nunca avisar duas vezes o mesmo degrau.
    """
    if total_agora < LIMITE_INICIAL_VOLUME:
        return

    ultimo_avisado = controle["ultimo_aviso_volume"]

    # Maior degrau (múltiplo de 25, a partir de 300) já alcançado agora
    passos_acima = (total_agora - LIMITE_INICIAL_VOLUME) // PASSO_VOLUME
    degrau_atual = LIMITE_INICIAL_VOLUME + passos_acima * PASSO_VOLUME

    if degrau_atual > ultimo_avisado:
        criar_issue_github(
            titulo=f"[Robô de notícias] Acervo passou de {degrau_atual} notícias",
            corpo=(
                f"O arquivo acumulado de notícias passou de {degrau_atual} notícias "
                f"(total atual: {total_agora}). Este é apenas um aviso informativo de "
                f"volume, sem nenhuma ação necessária."
            ),
        )
        controle["ultimo_aviso_volume"] = degrau_atual


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

    # Carrega o que já foi publicado antes (inclui o controle de avisos)
    dados_existentes = carregar_existente()
    noticias_existentes = dados_existentes["noticias"]
    controle = dados_existentes["controle"]

    # Sinal de possível bloqueio: a página não trouxe NENHUMA notícia
    # reconhecível (diferente de "nenhuma bateu com as palavras-chave").
    pagina_veio_vazia = len(todas) == 0
    verificar_aviso_bloqueio(controle, pagina_veio_vazia)

    filtradas = [n for n in todas if bate_palavra_chave(n, PALAVRAS_CHAVE)]
    print(f"  -> {len(filtradas)} notícias batem com as palavras-chave: {PALAVRAS_CHAVE}")

    links_existentes = {n["link"] for n in noticias_existentes}
    novas = [n for n in filtradas if n["link"] not in links_existentes]
    print(f"  -> {len(novas)} são novas (ainda não publicadas).")

    if not novas:
        print("Nenhuma notícia nova hoje. JSON permanece como estava (controle é atualizado).")
        dados_existentes["ultima_verificacao"] = datetime.now(timezone.utc).isoformat()
        ARQUIVO_SAIDA.parent.mkdir(parents=True, exist_ok=True)
        ARQUIVO_SAIDA.write_text(
            json.dumps(dados_existentes, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
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

    # Acumula: notícias novas vão para o TOPO da lista (mais recentes primeiro),
    # seguidas das que já existiam. Nada é removido.
    total_antes = len(noticias_existentes)
    lista_final = traduzidas_novas + noticias_existentes
    total_agora = len(lista_final)

    verificar_aviso_volume(controle, total_antes, total_agora)

    saida = {
        "atualizado_em": datetime.now(timezone.utc).isoformat(),
        "ultima_verificacao": datetime.now(timezone.utc).isoformat(),
        "fonte": URL_NOTICIAS,
        "palavras_chave": PALAVRAS_CHAVE,
        "total_noticias": total_agora,
        "noticias": lista_final,
        "controle": controle,
    }

    ARQUIVO_SAIDA.parent.mkdir(parents=True, exist_ok=True)
    ARQUIVO_SAIDA.write_text(
        json.dumps(saida, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nJSON atualizado: {ARQUIVO_SAIDA}")
    print(f"  -> {len(novas)} notícia(s) nova(s) adicionada(s).")
    print(f"  -> {total_agora} notícia(s) no total (acumulado).")


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as e:
        print(f"Erro ao acessar o site: {e}", file=sys.stderr)
        sys.exit(1)
