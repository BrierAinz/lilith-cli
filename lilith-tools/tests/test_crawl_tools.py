"""Tests de mimir_ingest_url — traer documentación externa al índice de Mimir.

El crawler se simula: la red no entra en la suite. Lo que se verifica es el
contrato del tool (validación, nombres de archivo, no pisar, cabecera de
procedencia), que es donde de verdad se rompe.
"""

from __future__ import annotations

import pytest

from lilith_tools import crawl_tools
from lilith_tools.crawl_tools import MimirIngestUrlTool
from lilith_tools.registry import ToolRegistry


@pytest.fixture
def raiz_temporal(tmp_path, monkeypatch):
    """Apunta la raíz del workspace a un tmp_path: no se toca Svartalfheim real."""
    monkeypatch.setattr(crawl_tools, "_resolve_yggdrasil_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def crawl_ok(monkeypatch):
    """El crawler devuelve markdown sin salir a la red."""
    async def _fake(url: str):
        return True, f"# Documento\n\nContenido de {url}\n", ""

    monkeypatch.setattr(crawl_tools, "_crawl", _fake)


def test_registrado_en_el_registry():
    assert "mimir_ingest_url" in ToolRegistry.list_tools()


class TestValidacion:
    def test_url_vacia_es_error(self, raiz_temporal):
        resultado = MimirIngestUrlTool().execute(url="   ")
        assert not resultado.success
        assert "url" in resultado.error

    @pytest.mark.parametrize("url", ["ftp://x.com/a", "no-es-una-url", "file:///etc/passwd"])
    def test_esquema_no_http_es_error(self, raiz_temporal, url):
        resultado = MimirIngestUrlTool().execute(url=url)
        assert not resultado.success
        assert "inválida" in resultado.error


class TestEscritura:
    def test_guarda_en_svartalfheim_para_que_mimir_lo_indexe(
        self, raiz_temporal, crawl_ok
    ):
        resultado = MimirIngestUrlTool().execute(url="https://docs.comfy.org/get-started")

        assert resultado.success, resultado.error
        destino = raiz_temporal / crawl_tools.SUBCARPETA / "docscomfyorg-get-started.md"
        assert destino.is_file()
        # La ruta importa: Mimir solo indexa lo que cuelga de Svartalfheim.
        assert "Svartalfheim" in resultado.data["ruta"]

    def test_la_cabecera_deja_la_procedencia(self, raiz_temporal, crawl_ok):
        url = "https://example.com/guia"
        MimirIngestUrlTool().execute(url=url)

        texto = (raiz_temporal / crawl_tools.SUBCARPETA / "examplecom-guia.md").read_text(
            encoding="utf-8"
        )
        # Sin esto, en un mes nadie sabe de dónde salió el documento.
        assert url in texto
        assert "Fuente:" in texto
        assert "# Documento" in texto

    def test_nombre_explicito_manda_sobre_el_derivado(self, raiz_temporal, crawl_ok):
        resultado = MimirIngestUrlTool().execute(
            url="https://example.com/x", nombre="Guía de ComfyUI"
        )

        # El slug conserva los acentos: `\w` en regex de Python es Unicode, y
        # es el mismo criterio que `styles._generate_id`, con el que se buscó
        # ser consistente. Los nombres de archivo van en UTF-8 sin problema.
        assert resultado.data["slug"] == "guía-de-comfyui"
        assert (raiz_temporal / crawl_tools.SUBCARPETA / "guía-de-comfyui.md").is_file()

    def test_no_pisa_un_documento_existente(self, raiz_temporal, crawl_ok):
        url = "https://example.com/guia"
        assert MimirIngestUrlTool().execute(url=url).success

        segundo = MimirIngestUrlTool().execute(url=url)

        assert not segundo.success
        assert "ya existe" in segundo.error
        assert "sobrescribir" in segundo.error

    def test_sobrescribir_true_si_lo_reemplaza(self, raiz_temporal, crawl_ok):
        url = "https://example.com/guia"
        MimirIngestUrlTool().execute(url=url)

        segundo = MimirIngestUrlTool().execute(url=url, sobrescribir=True)

        assert segundo.success, segundo.error


class TestFallos:
    def test_crawl_fallido_propaga_el_motivo(self, raiz_temporal, monkeypatch):
        async def _falla(url: str):
            return False, "", "403 Forbidden"

        monkeypatch.setattr(crawl_tools, "_crawl", _falla)

        resultado = MimirIngestUrlTool().execute(url="https://example.com/x")

        assert not resultado.success
        assert "403" in resultado.error

    def test_contenido_vacio_no_escribe_un_documento_inutil(
        self, raiz_temporal, monkeypatch
    ):
        async def _vacio(url: str):
            return True, "   \n  ", ""

        monkeypatch.setattr(crawl_tools, "_crawl", _vacio)

        resultado = MimirIngestUrlTool().execute(url="https://example.com/x")

        assert not resultado.success
        assert "no devolvió contenido" in resultado.error
        assert not (raiz_temporal / crawl_tools.SUBCARPETA).exists()

    def test_sin_crawl4ai_el_error_dice_como_instalarlo(self, raiz_temporal, monkeypatch):
        async def _sin_dep(url: str):
            raise ImportError("No module named 'crawl4ai'")

        monkeypatch.setattr(crawl_tools, "_crawl", _sin_dep)

        resultado = MimirIngestUrlTool().execute(url="https://example.com/x")

        assert not resultado.success
        assert "uv pip install crawl4ai" in resultado.error
        assert "playwright install chromium" in resultado.error
