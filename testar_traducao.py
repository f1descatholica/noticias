#!/usr/bin/env python3
"""
Teste isolado de tradução.
NÃO acessa o site de notícias, NÃO mexe no docs/noticias.json.
Só serve para confirmar se Google e/ou MyMemory estão respondendo.
"""

from deep_translator import GoogleTranslator, MyMemoryTranslator

_tradutor_google = GoogleTranslator(source="en", target="pt")
_tradutor_mymemory = MyMemoryTranslator(source="en-GB", target="pt-BR")

TEXTOS_TESTE = [
    "Pope Leo Implies Latin Mass Lacked Conscious Participation of the Faithful",
    "The Pachamama Church Prepares for Leo XIV",
]


def traduzir(texto: str) -> str:
    try:
        resultado = _tradutor_google.translate(texto)
        print("  [ok] traduzido pelo Google.")
        return resultado
    except Exception as e_google:
        print(f"  [aviso] Google falhou ({e_google}); tentando MyMemory...")

    try:
        resultado = _tradutor_mymemory.translate(texto)
        print("  [ok] traduzido pelo MyMemory.")
        return resultado
    except Exception as e_mymemory:
        print(f"  [aviso] MyMemory também falhou ({e_mymemory}); mantendo original.")

    return texto


if __name__ == "__main__":
    for texto in TEXTOS_TESTE:
        print(f"\nOriginal: {texto}")
        traduzido = traduzir(texto)
        print(f"Traduzido: {traduzido}")
