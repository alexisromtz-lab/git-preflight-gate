#!/usr/bin/env python3
"""Falsadores del template templates/platino-gate.yml. Solo stdlib.

Cada caso declara su entrada, su salida esperada y su salida medida. Un caso
que acierta por la rama equivocada no discrimina: se compara el valor medido,
no la mera existencia de un fallo.

    python3 tests/test_platino_gate_template.py
    RC 0  = todos los casos concuerdan con su contrato
    RC 1  = al menos un caso divergio (se imprime cual)

HUELLA-TEST-PLATINO-GATE-TEMPLATE-20261031
"""
import pathlib
import re
import subprocess
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
TPL = RAIZ / "templates" / "platino-gate.yml"


def leer():
    if not TPL.is_file():
        print("FALLO: el template no existe en %s" % TPL)
        raise SystemExit(1)
    return TPL.read_text(encoding="utf-8")


def bloque_contrato(txt):
    """Extrae el `case ... esac` real del job contrato-nombre.

    Se ejecuta el texto del template, no una copia: una copia en el test deja
    de falsar el archivo en cuanto el archivo cambia.
    """
    m = re.search(r'^\s*case "\$WORKFLOW_REF" in\n(.*?)^\s*esac\n', txt, re.S | re.M)
    if not m:
        print("FALLO: el template no contiene el bloque case sobre WORKFLOW_REF")
        raise SystemExit(1)
    return m.group(0)


def correr_contrato(cuerpo, workflow_ref):
    script = "set -u\nWORKFLOW_REF=%r\n%s" % (workflow_ref, cuerpo)
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def main():
    txt = leer()
    cuerpo = bloque_contrato(txt)
    filas = []

    casos = [
        (
            "NOMBRE_CORRECTO",
            "alexisromtz-lab/mi-repo/.github/workflows/platino-gate.yml@refs/heads/main",
            0,
        ),
        (
            "RENOMBRADO_A_CI",
            "alexisromtz-lab/mi-repo/.github/workflows/ci.yml@refs/heads/main",
            20,
        ),
        (
            "EXTENSION_YAML",
            "alexisromtz-lab/mi-repo/.github/workflows/platino-gate.yaml@refs/heads/main",
            20,
        ),
        (
            "SIN_DIRECTORIO_WORKFLOWS",
            "platino-gate.yml@refs/heads/main",
            20,
        ),
        (
            "REUTILIZABLE_WORKFLOW_CALL",
            "alexisromtz-lab/otro-repo/.github/workflows/reuse.yml@refs/heads/main",
            20,
        ),
    ]
    for nombre, ref, esperado in casos:
        rc, salida = correr_contrato(cuerpo, ref)
        filas.append(
            {
                "caso": nombre,
                "esperado": esperado,
                "medido": rc,
                "ok": rc == esperado,
                "salida": salida.splitlines()[0] if salida else "",
            }
        )

    estructurales = [
        ("EL_ARCHIVO_SE_LLAMA_PLATINO_GATE", TPL.name == "platino-gate.yml", TPL.name),
        (
            "SIN_CONTENTS_WRITE",
            "contents: write" not in txt and "write-all" not in txt,
            "contents: write ausente",
        ),
        (
            "PERMISO_GLOBAL_READ",
            re.search(r"^permissions:\n\s+contents: read\s*$", txt, re.M) is not None,
            "permissions: contents: read",
        ),
        (
            "USES_FIJADOS_POR_SHA",
            all(
                re.fullmatch(r"[^/\s]+/[^@\s]+@[0-9a-f]{40}", u)
                for u in re.findall(r"uses:\s*(\S+)", txt)
            ),
            ",".join(re.findall(r"uses:\s*(\S+)", txt)),
        ),
        (
            "EL_INSTRUMENTO_PASA_EL_TEMPLATE",
            _instrumento_pasa(),
            "sim/gate_workflows_ci.py --file",
        ),
    ]
    for nombre, ok, detalle in estructurales:
        filas.append({"caso": nombre, "esperado": True, "medido": ok, "ok": bool(ok), "salida": detalle})

    fallos = [f for f in filas if not f["ok"]]
    for f in filas:
        print(
            "  [%s] %-34s esperado=%s medido=%s  %s"
            % ("OK" if f["ok"] else "FALLA", f["caso"], f["esperado"], f["medido"], f["salida"])
        )
    print("casos=%d fallos=%d" % (len(filas), len(fallos)))
    return 1 if fallos else 0


def _instrumento_pasa():
    p = subprocess.run(
        [sys.executable, str(RAIZ / "sim" / "gate_workflows_ci.py"), "--file", str(TPL)],
        capture_output=True,
        text=True,
    )
    return p.returncode == 0


if __name__ == "__main__":
    raise SystemExit(main())
