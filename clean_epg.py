#!/usr/bin/env python3
"""
clean_epg.py

Descarga un XMLTV desde una URL fija y elimina entradas <programme>
duplicadas/solapadas dentro del mismo canal, quedándose únicamente
con la más reciente (la que tiene el 'start' más alto) de cada grupo
de horarios que se solapan.

Esto resuelve el caso típico de canales tipo "stream" (ej. MLB ALT)
donde el proveedor deja varias entradas con títulos viejos apuntando
al mismo bloque horario del día.

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
"""

import sys
import os
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

XMLTV_TIME_FMT = "%Y%m%d%H%M%S %z"


def parse_xmltv_time(value: str) -> datetime:
    return datetime.strptime(value.strip(), XMLTV_TIME_FMT)


def download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "epg-cleaner/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def clean_programmes(root: ET.Element) -> int:
    """
    Agrupa <programme> por canal, y dentro de cada canal colapsa
    los que se solapan en tiempo dejando solo el de 'start' más reciente.
    Devuelve la cantidad de entradas eliminadas.
    """
    programmes = root.findall("programme")

    by_channel = {}
    for prog in programmes:
        ch = prog.get("channel")
        by_channel.setdefault(ch, []).append(prog)

    to_remove = []

    for ch, progs in by_channel.items():
        # Orden estable por hora de inicio
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

        kept_start, kept_stop, kept_el = None, None, None
        cluster = []

        def flush_cluster(cluster):
            """De un grupo de entradas que se solapan, deja solo la última (start más alto)."""
            if len(cluster) <= 1:
                return
            cluster_sorted = sorted(cluster, key=lambda x: x[0])
            # nos quedamos con el último (start más reciente)
            for start, stop, el in cluster_sorted[:-1]:
                to_remove.append(el)

        for start, stop, el in parsed:
            if not cluster:
                cluster = [(start, stop, el)]
                continue

            last_start, last_stop, _ = cluster[-1]
            # Se solapan si el nuevo empieza antes de que termine el anterior
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

    if not source_url:
        print("ERROR: falta la URL de origen. Usa: python3 clean_epg.py <URL> <SALIDA>", file=sys.stderr)
        sys.exit(1)

    print(f"Descargando XMLTV desde: {source_url}")
    raw = download(source_url)

    root = ET.fromstring(raw)

    if channel_prefix_env:
        prefixes = channel_prefix_env.split(",")
        rc, rp = filter_by_prefix(root, prefixes)
        print(f"Filtrado por prefijo {prefixes}: {rc} canales y {rp} programmes eliminados (fuera del filtro)")

    removed = clean_programmes(root)
    print(f"Entradas <programme> eliminadas por solape/duplicado: {removed}")

    tree = ET.ElementTree(root)
    ET.indent(tree, space="")  # Python 3.9+
    tree.write(output_file, encoding="utf-8", xml_declaration=True)
    print(f"XML limpio escrito en: {output_file}")


if __name__ == "__main__":
    main()
