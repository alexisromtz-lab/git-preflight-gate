#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gate_workflows_ci.py
====================
Verificador estructural DETERMINISTA de la topologia de autoridad en pipelines
de CI.  Analiza el AST de los YAML de .github/workflows/ y aplica CINCO
invariantes contables.  No lee red, no escribe archivos, no usa aleatoriedad,
no consulta el reloj.

INVARIANTE CENTRAL (forma corregida):

    Autoridad(Job) > 0   =>   Ejecucion(repo U deps)(Job) = vacio

No se afirma "cero ejecucion": la autoridad tiene que ejecutar ALGO (descargar
un artefacto, correr el cliente nativo).  Lo que se prohibe es que ese algo
provenga del repositorio o de sus dependencias.

UNIVERSO DECLARADO: .github/workflows/*.yml + .github/workflows/*.yaml
MOTOR: python3 stdlib + PyYAML (safe_load) -- sin red, sin escritura, sin azar
REGLA DE VEREDICTO: RC 0 (PASA) | RC > 0 (FALLO CERRADO, causa tipada)

TABLA DE CAUSAS (RC):
  1  AUTORIDAD_CONTAMINADA  Job con permisos de ESCRITURA que ejecuta codigo
                            de terceros mediante `run:` (gestor de paquetes,
                            interprete, descarga o mutacion de VCS).
  2  SUMIDERO_IMPUDO         Job sumidero (`id-token: write`) que hace
                            `actions/checkout`, invoca herramientas dinamicas
                            (npx/pipx) o no consume exclusivamente artefactos
                            descargados (`actions/download-artifact`).
  3  DISPARADOR_ENVENENADO   Trigger `pull_request_target` / `workflow_run`
                            con checkout explicito del commit del PR / head
                            dentro de un contexto con secretos o autoridad.
  4  SECRETO_EXPUESTO        `${{ secrets.* }}` en un bloque `env:` (global o
                            de job), o interpolado dentro de `run:` / `with:`.
  5  REF_MUTABLE             `uses:` con tag flotante (@v*, @main, @master) en
                            vez de SHA-1 de 40 caracteres.
 31  UNIVERSO_VACIO          No hay archivos de workflow que medir.
 32  YAML_INVALIDO           El archivo no parsea.

RESIDUO DECLARADO (PLANO CIEGO FUERA DEL ARCHIVO):
  1. Permisos por defecto del GITHUB_TOKEN a nivel de repositorio/organizacion.
  2. Politicas de confianza OIDC / Trusted Publishing en el registro destino.
  3. Ejecucion de codigo nativo no aislado en runtime (modificacion de deps).
  4. Workflows reutilizables (`workflow_call`) y acciones compuestas alojadas
     en repositorios ajenos.
  => El instrumento certifica la AUSENCIA de la forma `autoridad contaminada`
     en el archivo que PUEDE leer.  NO certifica el plano que no ve.

DOMINIO DECLARADO DE RC 1 (ancho, no umbral):
  `actions/checkout` por si solo NO dispara RC 1.  RC 1 mide EJECUCION
  (`run:`), no presencia del arbol.  La presencia del arbol en un job con
  autoridad de publicacion es RC 2 (SUMIDERO_IMPUDO).  La particion es
  declarada, no silenciosa.
  ALLOWLIST_SINK: el cliente nativo de publicacion (`npm publish ...`) queda
  fuera del computo de ejecucion de terceros, porque el sumidero canonico
  TIENE que ejecutarlo.  Es una restriccion de DOMINIO declarada, no una
  bajada de umbral: el conjunto admitido es exactamente el cliente nativo.
"""

import argparse
import io
import json
import os
import re
import sys

try:
    import yaml
except Exception as _e:  # pragma: no cover
    sys.stderr.write("MOTOR INCOMPLETO: PyYAML ausente (%s). Fail-closed.\n" % (_e,))
    raise SystemExit(30)

MOTOR = "python3 stdlib + PyYAML %s (sin red, sin escritura, sin azar)" % (
    getattr(yaml, "__version__", "?"),
)
UNIVERSO = ".github/workflows/*.yml + .github/workflows/*.yaml del arbol objetivo"
INVARIANTE = "Autoridad(Job) > 0  =>  Ejecucion(repo U deps)(Job) = vacio"
REGLA = "RC 0 (PASA) | RC > 0 (FALLO CERRADO, causa tipada)"

RESIDUO = (
    "Permisos por defecto del GITHUB_TOKEN a nivel de repositorio u organizacion.",
    "Politicas de confianza OIDC / Trusted Publishing en el registro destino (npm, PyPI, AWS).",
    "Modificacion de dependencias en runtime si se ejecuta codigo nativo no aislado.",
    "Workflows reutilizables (workflow_call) y acciones compuestas alojadas en repositorios ajenos.",
)

CAUSAS = {
    1: "AUTORIDAD_CONTAMINADA",
    2: "SUMIDERO_IMPUDO",
    3: "DISPARADOR_ENVENENADO",
    4: "SECRETO_EXPUESTO",
    5: "REF_MUTABLE",
    31: "UNIVERSO_VACIO",
    32: "YAML_INVALIDO",
}

# ----------------------------------------------------------------------------
# Patrones.  Todos son LEXICOS y se aplican linea por linea (dominio declarado:
# una linea de `run:` es la unidad de juicio).
# ----------------------------------------------------------------------------
PAT_EXEC = re.compile(
    r"\b(npm|pnpm|yarn|npx|pipx|pip3|pip|cargo|rustc|go|gem|bundle|composer"
    r"|make|cmake|node|deno|bun|python3|python|ruby|php|java|mvn|gradle|dotnet)\b"
)
PAT_FETCH = re.compile(r"\b(curl|wget)\b")
PAT_GITMUT = re.compile(r"\bgit\s+(push|commit|tag|remote|config)\b")
PAT_GH = re.compile(r"(^|[\s;&|(])gh\s+[a-z]")
PAT_SINK_OK = re.compile(r"^\s*npm\s+publish\b")
PAT_DINAMICO = re.compile(r"\b(npx|pipx)\b")
PAT_SECRETS = re.compile(r"\$\{\{\s*secrets\.[A-Za-z0-9_]")
PAT_SHA40 = re.compile(r"^[0-9a-f]{40}$")
PAT_UNTRUSTED_REF = re.compile(r"github\.event\.(pull_request\.head|workflow_run\.head)")


def _walk_str(obj):
    """Todos los literales de cadena de un subarbol YAML (claves incluidas)."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _walk_str(v)
    elif isinstance(obj, (list, tuple)):
        for x in obj:
            yield from _walk_str(x)


def _clave_on(wf):
    """PyYAML (YAML 1.1) convierte la clave `on` en el booleano True."""
    for k in (True, "on", "On", "ON"):
        if k in wf:
            return wf[k]
    return None


def _triggers(val):
    if val is None:
        return set()
    if isinstance(val, str):
        return {val}
    if isinstance(val, (list, tuple)):
        return {str(x) for x in val}
    if isinstance(val, dict):
        return {str(k) for k in val.keys()}
    return set()


def _mapa_perm(p):
    if p is None:
        return None
    if isinstance(p, str):
        v = p.strip().lower()
        if v == "write-all":
            return {"*": "write"}
        if v == "read-all":
            return {"*": "read"}
        return {v: "write"}
    if isinstance(p, dict):
        return {str(k): str(v).strip().lower() for k, v in p.items()}
    return None


def scopes_write(mapa):
    if not mapa:
        return set()
    return {k for k, v in mapa.items() if v == "write"}


def _pasos(job):
    out = []
    for s in (job.get("steps") or []):
        if isinstance(s, dict):
            out.append(s)
    return out


def _runs(job):
    return [str(s.get("run", "")) for s in _pasos(job) if "run" in s]


def _uses(job):
    return [str(s.get("uses", "")) for s in _pasos(job) if "uses" in s]


def _todos_los_uses(wf):
    out = []
    jobs = wf.get("jobs")
    if isinstance(jobs, dict):
        for job in jobs.values():
            if not isinstance(job, dict):
                continue
            if isinstance(job.get("uses"), str):
                out.append(job["uses"])
            out.extend(_uses(job))
    return out


def _tiene_checkout(job):
    return any(u.startswith("actions/checkout") for u in _uses(job))


def _tiene_download(job):
    return any(u.startswith("actions/download-artifact") for u in _uses(job))


def _textos_sensibles(wf, job):
    """Texto de los bloques donde un secreto se expone: env / run: / with:."""
    out = []
    for src in (wf.get("env"), job.get("env")):
        if src:
            out.extend(_walk_str(src))
    for s in _pasos(job):
        if "run" in s:
            out.append(str(s.get("run", "")))
        if "with" in s:
            out.extend(_walk_str(s.get("with")))
    return out


def _linea_ejecuta(linea):
    """Una linea de shell ejecuta codigo de terceros?"""
    s = linea.strip()
    if not s or s.startswith("#"):
        return False
    if PAT_SINK_OK.match(s):  # ALLOWLIST_SINK: cliente nativo de publicacion
        return False
    return bool(
        PAT_EXEC.search(s) or PAT_FETCH.search(s) or PAT_GITMUT.search(s) or PAT_GH.search(s)
    )


def ref_mutable(valor):
    v = valor.strip()
    if v.startswith("./") or v.startswith(".\\"):
        return False
    if v.startswith("docker://"):
        return "@sha256:" not in v
    if "@" not in v:
        return True
    return not PAT_SHA40.match(v.rsplit("@", 1)[1])


# ----------------------------------------------------------------------------
# NUCLEO
# ----------------------------------------------------------------------------
def analizar(texto, nombre):
    """Devuelve la lista de hallazgos [(rc, causa, archivo, job, detalle)]."""
    hs = []
    try:
        wf = yaml.safe_load(texto)
    except yaml.YAMLError as e:
        return [{"rc": 32, "causa": CAUSAS[32], "archivo": nombre, "job": "-",
                 "detalle": str(e)[:200]}]
    if not isinstance(wf, dict):
        return [{"rc": 32, "causa": CAUSAS[32], "archivo": nombre, "job": "-",
                 "detalle": "raiz del YAML no es un mapping"}]

    wf_perm = _mapa_perm(wf.get("permissions"))
    triggers = _triggers(_clave_on(wf))
    pr_target = bool(triggers & {"pull_request_target", "workflow_run"})
    clase_trigger = ",".join(sorted(triggers & {"pull_request_target", "workflow_run"}))

    # ---- RC 5: barrido global de referencias
    for v in _todos_los_uses(wf):
        if ref_mutable(v):
            hs.append({"rc": 5, "causa": CAUSAS[5], "archivo": nombre, "job": "-",
                       "detalle": "uses con ref flotante: %s" % (v[:90],)})

    # ---- RC 4: env global (workflow-level)
    env_wf = wf.get("env")
    if env_wf and any(PAT_SECRETS.search(x) for x in _walk_str(env_wf)):
        hs.append({"rc": 4, "causa": CAUSAS[4], "archivo": nombre, "job": "<workflow>",
                   "detalle": "secrets en bloque env global"})

    jobs = wf.get("jobs")
    if not isinstance(jobs, dict):
        jobs = {}

    for jn in sorted(jobs.keys(), key=str):
        job = jobs.get(jn)
        if not isinstance(job, dict):
            continue
        perm = _mapa_perm(job.get("permissions")) if "permissions" in job else wf_perm
        sw = scopes_write(perm)

        # ---- RC 1: AUTORIDAD_CONTAMINADA
        if sw:
            malas = []
            for cmd in _runs(job):
                for linea in cmd.splitlines():
                    if _linea_ejecuta(linea):
                        malas.append(linea.strip())
            if malas:
                hs.append({"rc": 1, "causa": CAUSAS[1], "archivo": nombre, "job": str(jn),
                           "detalle": "scopes=%s ejecuta: %s"
                                      % (",".join(sorted(sw)), malas[0][:90])})

        # ---- RC 2: SUMIDERO_IMPUDO
        if ("id-token" in sw) or ("*" in sw):
            motivos = []
            if _tiene_checkout(job):
                motivos.append("actions/checkout")
            if any(PAT_DINAMICO.search(c) for c in _runs(job)):
                motivos.append("herramienta dinamica (npx/pipx)")
            if not _tiene_download(job):
                motivos.append("sin actions/download-artifact")
            if motivos:
                hs.append({"rc": 2, "causa": CAUSAS[2], "archivo": nombre, "job": str(jn),
                           "detalle": "; ".join(motivos)})

        # ---- RC 3: DISPARADOR_ENVENENADO
        if pr_target and _tiene_checkout(job):
            refs = []
            for s in _pasos(job):
                if str(s.get("uses", "")).startswith("actions/checkout"):
                    w = s.get("with")
                    if isinstance(w, dict):
                        refs.append(str(w.get("ref", "")))
            untrusted = any(PAT_UNTRUSTED_REF.search(r) for r in refs)
            contexto = bool(sw) or any(PAT_SECRETS.search(x) for x in _textos_sensibles(wf, job))
            if untrusted and contexto:
                hs.append({"rc": 3, "causa": CAUSAS[3], "archivo": nombre, "job": str(jn),
                           "detalle": "trigger %s + checkout de ref no confiable en contexto privilegiado"
                                      % (clase_trigger,)})

        # ---- RC 4: SECRETO_EXPUESTO (env de job / run: / with:)
        if any(PAT_SECRETS.search(x) for x in _textos_sensibles(wf, job)):
            hs.append({"rc": 4, "causa": CAUSAS[4], "archivo": nombre, "job": str(jn),
                       "detalle": "secrets inline en env/run:/with:"})

    return hs


# ----------------------------------------------------------------------------
# BANCO MUST-FAIL INCORPORADO  (sobre cadenas: CERO escrituras a disco)
# ----------------------------------------------------------------------------
SHA_CHECKOUT = "11bd71901bbe5b1630ceea73d27597364c9af683"
SHA_SETUPNODE = "39370e3970a6d050c480ffad4ff0ed4d3fdee5af"
SHA_UPLOAD = "65c4c4a1ddee5b72f698fdd19549f0f0fb45cf08"
SHA_DOWNLOAD = "fa0a91b85d4f404e444e00e005971372dc801d16"

C0_CANONICO = """
name: publicar
on:
  release:
    types: [published]
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@%s
      - uses: actions/setup-node@%s
        with:
          node-version: "20"
      - run: npm ci --ignore-scripts
      - run: npm pack
      - run: sha256sum ./*.tgz > SHASUMS256.txt
      - uses: actions/upload-artifact@%s
        with:
          name: dist
          path: |
            *.tgz
            SHASUMS256.txt
  publish:
    needs: [build]
    runs-on: ubuntu-latest
    permissions:
      id-token: write
      actions: read
    steps:
      - uses: actions/download-artifact@%s
        with:
          name: dist
      - run: sha256sum -c SHASUMS256.txt
      - run: npm publish --provenance ./*.tgz
""" % (SHA_CHECKOUT, SHA_SETUPNODE, SHA_UPLOAD, SHA_DOWNLOAD)

C1_AUTORIDAD = """
name: contaminado
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@%s
      - run: npm ci
""" % (SHA_CHECKOUT,)

C2_SUMIDERO = """
name: sumidero
on:
  release:
    types: [published]
jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      id-token: write
    steps:
      - run: npm publish --provenance ./*.tgz
"""

C3_DISPARADOR = """
name: pr-target
on: pull_request_target
jobs:
  lint:
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
    steps:
      - uses: actions/checkout@%s
        with:
          ref: ${{ github.event.pull_request.head.sha }}
""" % (SHA_CHECKOUT,)

C4_SECRETO = """
name: secreto
on: push
env:
  TOKEN: ${{ secrets.NPM_TOKEN }}
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""

C5_REF = """
name: flotante
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
      - run: npm ci --ignore-scripts
"""

C6_DOS_CAUSAS = """
name: sumidero-sucio
on:
  release:
    types: [published]
jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      id-token: write
    steps:
      - uses: actions/checkout@%s
      - uses: actions/download-artifact@%s
        with:
          name: dist
      - run: npx --yes provenance-publish ./dist/*.tgz
""" % (SHA_CHECKOUT, SHA_DOWNLOAD)

BANCO = (
    ("C0_CANONICO_DOS_JOBS", C0_CANONICO, 0, frozenset()),
    ("C1_AUTORIDAD_CONTAMINADA", C1_AUTORIDAD, 1, frozenset([1])),
    ("C2_SUMIDERO_IMPUDO", C2_SUMIDERO, 2, frozenset([2])),
    ("C3_DISPARADOR_ENVENENADO", C3_DISPARADOR, 3, frozenset([3])),
    ("C4_SECRETO_EXPUESTO", C4_SECRETO, 4, frozenset([4])),
    ("C5_REF_MUTABLE", C5_REF, 5, frozenset([5])),
    ("C6_DOS_CAUSAS_A_LA_VEZ", C6_DOS_CAUSAS, 1, frozenset([1, 2])),
)
CONTROL = "C0_CANONICO_DOS_JOBS"


def correr_banco():
    filas = []
    for nombre, texto, rc_esp, causas_esp in BANCO:
        hs = analizar(texto, "<memoria>/%s.yml" % nombre)
        rc = min((h["rc"] for h in hs), default=0)
        causas = frozenset(h["rc"] for h in hs)
        ok = (rc == rc_esp) and (causas == causas_esp)
        filas.append({
            "caso": nombre,
            "rc_esperado": rc_esp,
            "rc_medido": rc,
            "causas_esperadas": sorted(causas_esp),
            "causas_medidas": sorted(causas),
            "ok": ok,
            "detalle": [h["detalle"] for h in hs],
        })
    banco_ok = all(f["ok"] for f in filas)
    return filas, banco_ok


# ----------------------------------------------------------------------------
# SALIDA
# ----------------------------------------------------------------------------
def imprimir_cabecera():
    print("UNIVERSO DECLARADO: %s" % UNIVERSO)
    print("MOTOR: %s" % MOTOR)
    print("REGLA DE VEREDICTO: %s" % REGLA)
    print("INVARIANTE: %s" % INVARIANTE)
    print("RESIDUO DECLARADO (PLANO CIEGO FUERA DE YAML):")
    for r in RESIDUO:
        print("  - %s" % r)


def _fmt_causas(s):
    return "{rc:%s|%s}" % (
        ",".join(str(x) for x in s) if s else "-",
        ",".join(CAUSAS.get(x, "?") for x in s) if s else "ninguna",
    )


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Gate estructural de CI: topologia de autoridad en .github/workflows/."
    )
    ap.add_argument("ruta", nargs="?", default=".", help="raiz del arbol objetivo (default: .)")
    ap.add_argument("--root", dest="root", default=None, help="raiz explicita del arbol objetivo")
    ap.add_argument("--file", dest="archivo", default=None, help="analizar UN archivo YAML")
    ap.add_argument("--json", dest="json", action="store_true", help="salida JSON")
    ap.add_argument("--self-test", dest="self_test", action="store_true",
                    help="correr el banco must-fail incorporado (sin tocar disco)")
    ap.add_argument("--must-fail", dest="must_fail", action="store_true",
                    help="alias de --self-test: exige que cada causa provoque su RC")
    ns = ap.parse_args(argv)

    banco = ns.self_test or ns.must_fail

    if banco:
        filas, banco_ok = correr_banco()
        if ns.json:
            print(json.dumps({
                "universo": UNIVERSO, "motor": MOTOR, "invariante": INVARIANTE,
                "regla_de_veredicto": REGLA, "residuo_declarado": list(RESIDUO),
                "causas": CAUSAS, "self_test": filas, "banco_ok": banco_ok,
                "rc": 0 if banco_ok else 21,
            }, indent=2, ensure_ascii=False))
        else:
            imprimir_cabecera()
            print("")
            print("BANCO MUST-FAIL (casos en memoria; el instrumento no escribe disco)")
            print("%-28s %-24s %-24s %s" % ("CASO", "ESPERADO", "MEDIDO", "OK"))
            for f in filas:
                print("%-28s %-24s %-24s %s" % (
                    f["caso"],
                    _fmt_causas(frozenset(f["causas_esperadas"])),
                    _fmt_causas(frozenset(f["causas_medidas"])),
                    "SI" if f["ok"] else "NO",
                ))
            print("")
            for f in filas:
                for d in f["detalle"]:
                    print("  [%s] %s" % (f["caso"], d))
            print("")
            print("banco_ok = %s" % banco_ok)
        return 0 if banco_ok else 21

    raiz = ns.root or ns.ruta
    archivos = []
    if ns.archivo:
        archivos = [ns.archivo]
    else:
        d = os.path.join(raiz, ".github", "workflows")
        if os.path.isdir(d):
            for fn in sorted(os.listdir(d)):
                if fn.endswith(".yml") or fn.endswith(".yaml"):
                    archivos.append(os.path.join(d, fn))

    hallazgos = []
    if not archivos:
        hallazgos.append({"rc": 31, "causa": CAUSAS[31], "archivo": "-", "job": "-",
                          "detalle": "0 archivos en %s: nada que medir" % (os.path.join(raiz, ".github", "workflows"),)})
    else:
        for ruta in archivos:
            try:
                with io.open(ruta, "r", encoding="utf-8") as fh:
                    texto = fh.read()
            except Exception as e:
                hallazgos.append({"rc": 32, "causa": CAUSAS[32], "archivo": ruta, "job": "-",
                                  "detalle": "no legible: %s" % (e,)})
                continue
            hallazgos.extend(analizar(texto, ruta))

    causas = sorted({h["rc"] for h in hallazgos})
    rc = min(causas) if causas else 0

    if ns.json:
        print(json.dumps({
            "universo": UNIVERSO, "motor": MOTOR, "invariante": INVARIANTE,
            "regla_de_veredicto": REGLA, "residuo_declarado": list(RESIDUO),
            "causas": CAUSAS, "raiz": raiz, "archivos": archivos,
            "hallazgos": hallazgos, "causas_presentes": causas, "rc": rc,
        }, indent=2, ensure_ascii=False))
    else:
        imprimir_cabecera()
        print("")
        print("RAIZ: %s" % raiz)
        print("ARCHIVOS MEDIDOS: %d" % len(archivos))
        for h in hallazgos:
            print("  RC %-2d %-22s %s :: %s :: %s"
                  % (h["rc"], h["causa"], h["archivo"], h["job"], h["detalle"]))
        print("")
        print("CAUSAS PRESENTES: %s" % (causas if causas else "ninguna"))
        print("VEREDICTO: %s  RC=%d" % ("PASA" if rc == 0 else "FALLO CERRADO", rc))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
