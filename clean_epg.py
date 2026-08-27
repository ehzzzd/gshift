#!/usr/bin/env python3
"""
clean_epg.py
 
Descarga un XMLTV desde una URL fija y elimina entradas <programme>
duplicadas/solapadas dentro del mismo canal, quedándose únicamente
con la del día correcto (hoy), leyendo la fecha directamente del
título del programa (ej. "Aug 27") en vez de confiar ciegamente en
el timestamp 'start' que trae el proveedor.
 
Esto resuelve el caso típico de canales tipo "stream" (ej. MLB ALT)
donde el proveedor deja varias entradas (Aug 25, Aug 26, Aug 27...)
apuntando al mismo bloque horario del día, y el orden interno del
XML no siempre coincide con cuál es la más reciente.
 
Uso:
    python3 clean_epg.py <URL_ORIGEN> <ARCHIVO_SALIDA>
 
Variables de entorno (alternativa a argumentos):
    EPG_SOURCE_URL     -> URL del XMLTV original
    EPG_OUTPUT_FILE    -> ruta del archivo de salida (default: epg.xml)
    EPG_CHANNEL_PREFIX -> opcional. Lista separada por comas de prefijos de
                          canal a conservar (ej: "mlbvip"). Si se define,
                          se descartan todos los <channel>/<programme> cuyo
                          id NO empiece con alguno de esos prefijos.
                          Sirve para mantener el archivo final pequeño
                          (GitHub rechaza archivos de más de 100 MB).
    EPG_TZ_OFFSET_HOURS -> opcional. Offset horario (en horas, puede ser
                          negativo) usado para calcular "cuál es la fecha
                          de HOY" al momento de correr el script.
                          Default: -4 (Puerto Rico / AST, todo el año).
"""
 
import sys
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
 
XMLTV_TIME_FMT = "%Y%m%d%H%M%S %z"
 
MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
 
# Busca patrones tipo "Aug 27", "Aug. 27", "August 27" dentro del título
DATE_IN_TITLE_RE = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})\b",
    re.IGNORECASE,
)
 
 
def parse_xmltv_time(value: str) -> datetime:
    return datetime.strptime(value.strip(), XMLTV_TIME_FMT)
 
 
def download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "epg-cleaner/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()
 
 
def extract_title_date(title: str, ref_year: int):
    """
    Busca una fecha tipo 'Aug 27' dentro del título y devuelve un
    objeto date (usando ref_year como año). Devuelve None si no
    encuentra nada parseable.
    """
    if not title:
        return None
    m = DATE_IN_TITLE_RE.search(title)
    if not m:
        return None
    month_str, day_str = m.groups()
    month = MONTHS.get(month_str[:3].lower())
    if not month:
        return None
    try:
        day = int(day_str)
        return datetime(ref_year, month, day).date()
    except ValueError:
        return None
 
 
def pick_best(cluster, today):
    """
    De un grupo de <programme> que se solapan en tiempo para el mismo
    canal, elige cuál conservar:
      1. Si alguna entrada tiene, en el título, una fecha == hoy -> esa.
         (si hay varias con fecha == hoy, la de 'start' más alto)
      2. Si ninguna coincide con hoy, la que tenga la fecha más
         reciente/futura mencionada en el título.
      3. Si ninguna entrada trae fecha parseable en el título, se cae
         de vuelta al criterio anterior: la de 'start' más alto.
    Devuelve el elemento <programme> a conservar.
    """
    enriched = []
    for start, stop, el in cluster:
        title = el.findtext("title") or ""
        title_date = extract_title_date(title, today.year)
        enriched.append((start, stop, el, title_date))
 
    exact_matches = [e for e in enriched if e[3] == today]
    if exact_matches:
        exact_matches.sort(key=lambda e: e[0])
        return exact_matches[-1][2]
 
    with_date = [e for e in enriched if e[3] is not None]
    if with_date:
        with_date.sort(key=lambda e: (e[3], e[0]))
        return with_date[-1][2]
 
    enriched.sort(key=lambda e: e[0])
    return enriched[-1][2]
 
 
def clean_programmes(root: ET.Element, today) -> int:
    """
    Agrupa <programme> por canal, y dentro de cada canal colapsa
    los que se solapan en tiempo dejando solo uno (ver pick_best).
    Devuelve la cantidad de entradas eliminadas.
    """
    programmes = root.findall("programme")
 
    by_channel = {}
    for prog in programmes:
        ch = prog.get("channel")
        by_channel.setdefault(ch, []).append(prog)
 
    to_remove = []
 
    for ch, progs in by_channel.items():
        parsed = []
        for p in progs:
            try:
                start = parse_xmltv_time(p.get("start"))
                stop = parse_xmltv_time(p.get("stop"))
            except Exception:
                # Si algún timestamp viene mal formado, no lo tocamos
                continue
            parsed.append((start, stop, p))
 
        parsed.sort(key=lambda x: x[0])
 
        cluster = []
 
        def flush_cluster(cluster):
            if len(cluster) <= 1:
                return
            keep_el = pick_best(cluster, today)
            for start, stop, el in cluster:
                if el is not keep_el:
                    to_remove.append(el)
 
        for start, stop, el in parsed:
            if not cluster:
                cluster = [(start, stop, el)]
                continue
 
            last_start, last_stop, _ = cluster[-1]
            if start < last_stop:
                cluster.append((start, stop, el))
            else:
                flush_cluster(cluster)
                cluster = [(start, stop, el)]
 
        flush_cluster(cluster)
 
    for el in to_remove:
        root.remove(el)
 
    return len(to_remove)
 
 
def filter_by_prefix(root: ET.Element, prefixes) -> tuple:
    """
    Elimina todos los <channel> cuyo id no empiece con ninguno de los
    prefijos dados, y todos los <programme> cuyo channel no empiece con
    ninguno de esos prefijos. Comparación insensible a mayúsculas.
    Devuelve (canales_eliminados, programmes_eliminados).
    """
    prefixes = [p.strip().lower() for p in prefixes if p.strip()]
    if not prefixes:
        return 0, 0
 
    def keep(channel_id: str) -> bool:
        if not channel_id:
            return False
        cid = channel_id.lower()
        return any(cid.startswith(p) for p in prefixes)
 
    removed_channels = 0
    for el in root.findall("channel"):
        if not keep(el.get("id")):
            root.remove(el)
            removed_channels += 1
 
    removed_programmes = 0
    for el in root.findall("programme"):
        if not keep(el.get("channel")):
            root.remove(el)
            removed_programmes += 1
 
    return removed_channels, removed_programmes
 
 
def main():
    source_url = os.environ.get("EPG_SOURCE_URL") or (sys.argv[1] if len(sys.argv) > 1 else None)
    output_file = os.environ.get("EPG_OUTPUT_FILE") or (sys.argv[2] if len(sys.argv) > 2 else "epg.xml")
    channel_prefix_env = os.environ.get("EPG_CHANNEL_PREFIX", "")
    tz_offset_hours = float(os.environ.get("EPG_TZ_OFFSET_HOURS", "-4"))
 
    if not source_url:
        print("ERROR: falta la URL de origen. Usa: python3 clean_epg.py <URL> <SALIDA>", file=sys.stderr)
        sys.exit(1)
 
    tz = timezone(timedelta(hours=tz_offset_hours))
    today = datetime.now(tz).date()
    print(f"Fecha de 'hoy' usada para decidir cuál entrada conservar: {today} (offset {tz_offset_hours}h)")
 
    print(f"Descargando XMLTV desde: {source_url}")
    raw = download(source_url)
 
    root = ET.fromstring(raw)
 
    if channel_prefix_env:
        prefixes = channel_prefix_env.split(",")
        rc, rp = filter_by_prefix(root, prefixes)
        print(f"Filtrado por prefijo {prefixes}: {rc} canales y {rp} programmes eliminados (fuera del filtro)")
 
    removed = clean_programmes(root, today)
    print(f"Entradas <programme> eliminadas por solape/duplicado: {removed}")
 
    tree = ET.ElementTree(root)
    ET.indent(tree, space="")  # Python 3.9+
    tree.write(output_file, encoding="utf-8", xml_declaration=True)
    print(f"XML limpio escrito en: {output_file}")
 
 
if __name__ == "__main__":
    main()
 
