"""
Genera el informe de retención (HTML + PDF) a partir de un JSON por empleado.

Uso:
    python informe_pdf.py ejemplo_494.json            # un empleado
    python informe_pdf.py carpeta_jsons/              # todos los .json de una carpeta

Requisitos:
    pip install jinja2 weasyprint
    pip install google-genai   (para consultar_gemini en el notebook)

Flujo recomendado:
    1. Los DATOS (empleado, indicadores, modelo, salario) salen de tu dataframe.
    2. Gemini solo redacta los TEXTOS (resumen, acciones, valoración salarial, seguimiento)
       y los devuelve en JSON con el esquema de ESQUEMA_TEXTOS.
    3. Este script une ambos, aplica el formato y genera el PDF.
"""

import json
import os
import sys
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

BASE = Path(__file__).parent
CARPETA_ICONOS = BASE / "icons"
CARPETA_SALIDA = BASE / "informes"

# --- Categorías de acción que puede elegir Gemini y su icono -----------------
ICONOS_ACCION = {
    "formacion": "school",
    "mentoria": "users",
    "carrera": "stairs-up",
    "entorno": "building",
    "equipo": "heart-handshake",
    "salario": "cash",
    "conciliacion": "clock",
    "reconocimiento": "star",
}

# --- Esquema que debe devolver Gemini (sirve también como response_schema) ----
ESQUEMA_TEXTOS = {
    "type": "object",
    "properties": {
        "resumen": {"type": "string"},
        "condiciones": {"type": "array", "items": {"type": "string"}},
        "acciones": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "categoria": {"type": "string", "enum": list(ICONOS_ACCION)},
                    "titulo": {"type": "string"},
                    "dato_clave": {"type": "string"},
                    "detalle": {"type": "string"},
                },
                "required": ["categoria", "titulo", "dato_clave", "detalle"],
            },
        },
        "valoracion_salarial": {"type": "string"},
        "seguimiento": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "momento": {"type": "string"},
                    "titulo": {"type": "string"},
                    "detalle": {"type": "string"},
                },
                "required": ["momento", "titulo", "detalle"],
            },
        },
    },
    "required": ["resumen", "condiciones", "acciones", "valoracion_salarial", "seguimiento"],
}


# --- Formato de números en español --------------------------------------------
def num(valor, decimales=0):
    texto = f"{float(valor):,.{decimales}f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def eur(valor, decimales=0):
    return f"{num(valor, decimales)} $"


def anios(valor):
    v = float(valor)
    if v < 1:
        return "menos de 1 año"
    return "1 año" if v == 1 else f"{num(v, 0 if v.is_integer() else 1)} años"


# --- Iconos (Tabler, SVG en línea para que funcionen en el PDF sin internet) ---
_cache_iconos = {}


def icon(nombre, clase=""):
    if nombre not in _cache_iconos:
        ruta = CARPETA_ICONOS / f"{nombre}.svg"
        if not ruta.exists():
            ruta = CARPETA_ICONOS / "target-arrow.svg"
        svg = ruta.read_text(encoding="utf-8")
        _cache_iconos[nombre] = svg.replace('class="', 'class="ico ', 1) if 'class="' in svg \
            else svg.replace("<svg", '<svg class="ico"', 1)
    svg = _cache_iconos[nombre]
    if clase:
        svg = svg.replace('class="ico ', f'class="ico {clase} ', 1)
    return Markup(svg)


def icono_accion(categoria):
    return ICONOS_ACCION.get(str(categoria).lower().strip(), "target-arrow")


# --- Lógica de presentación ----------------------------------------------------
def nivel_riesgo(scoring):
    if scoring > 0.6:
        return {"clase": "alto", "etiqueta": "alto", "color": "#b42318"}
    if scoring >= 0.3:
        return {"clase": "medio", "etiqueta": "medio", "color": "#b54708"}
    return {"clase": "bajo", "etiqueta": "bajo", "color": "#067647"}


def datos_salario(s):
    if not s.get("referencia"):
        return {"sin_referencia": True, "icono": "info-circle", "color": "#4b5563", "fondo": "#f6f7f9"}
    emp, ref = float(s["empleado"]), float(s["referencia"])
    maximo = max(emp, ref) or 1
    diferencia = max(ref - emp, 0)
    por_debajo = diferencia > 0
    return {
        "pct_emp": round(emp / maximo * 100),
        "pct_ref": round(ref / maximo * 100),
        "diferencia": s.get("diferencia_mensual", diferencia),
        "coste_anual": s.get("coste_anual", diferencia * 12),
        "icono": "alert-circle" if por_debajo else "circle-check",
        "color": "#b54708" if por_debajo else "#067647",
        "fondo": "#fef4e6" if por_debajo else "#e7f6ee",
    }


def validar(e):
    faltan = [k for k in ("empleado", "indicadores", "modelo", "salario", "textos") if k not in e]
    if faltan:
        raise ValueError(f"Faltan bloques en el JSON: {faltan}")
    t = e["textos"]
    for k in ("resumen", "acciones", "valoracion_salarial", "seguimiento"):
        if k not in t:
            raise ValueError(f"Gemini no devolvió el campo textos.{k}")
    if len(t["acciones"]) != 3:
        raise ValueError(f"Se esperaban 3 acciones y llegaron {len(t['acciones'])}")


# --- Renderizado ---------------------------------------------------------------
entorno = Environment(loader=FileSystemLoader(BASE), autoescape=select_autoescape(["html"]))
entorno.globals.update(icon=icon, icono_accion=icono_accion, num=num, eur=eur, anios=anios)


def renderizar_html(e):
    validar(e)
    return entorno.get_template("plantilla_informe.html").render(
        e=e,
        riesgo=nivel_riesgo(float(e["indicadores"]["scoring"])),
        sal=datos_salario(e["salario"]),
        fecha=date.today().strftime("%d/%m/%Y"),
    )


def weasyprint_html():
    if os.name == "nt":
        dll_directory = Path(sys.prefix) / "Library" / "bin"
        if dll_directory.is_dir():
            directory = str(dll_directory)
            os.environ.setdefault("WEASYPRINT_DLL_DIRECTORIES", directory)
            path_entries = os.environ.get("PATH", "").split(os.pathsep)
            if directory not in path_entries:
                os.environ["PATH"] = os.pathsep.join([directory, *path_entries])
    from weasyprint import HTML
    return HTML


def informe_pdf_bytes(e):
    """Devuelve el PDF en memoria (bytes), sin escribir nada en disco."""
    return weasyprint_html()(string=renderizar_html(e), base_url=str(BASE)).write_pdf()


def generar(e, pdf=True):
    html = renderizar_html(e)
    CARPETA_SALIDA.mkdir(exist_ok=True)
    nombre = f"informe_retencion_{e['empleado']['id']}"
    ruta_html = CARPETA_SALIDA / f"{nombre}.html"
    ruta_html.write_text(html, encoding="utf-8")
    if pdf:
        weasyprint_html()(string=html, base_url=str(BASE)).write_pdf(CARPETA_SALIDA / f"{nombre}.pdf")
    return ruta_html


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    origen = Path(sys.argv[1])
    ficheros = sorted(origen.glob("*.json")) if origen.is_dir() else [origen]
    for f in ficheros:
        ruta = generar(json.loads(f.read_text(encoding="utf-8")))
        print(f"OK  {f.name}  ->  {ruta.with_suffix('.pdf').name}")
