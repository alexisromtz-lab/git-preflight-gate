#!/usr/bin/env bash
# gate_pre_escritura.sh — arnes de PRE-VUELO obligatorio y bloqueante (A2.1).
# Regla: sin corrida previa con RC 0 esta PROHIBIDO abrir o mutar el archivo.
# Uso: bash gate_pre_escritura.sh TARGET BLOB_PRE HUELLA [LINEAS_PRE]
#
#   TARGET      ruta ABSOLUTA del objeto destino (relativa -> RC 20)
#   BLOB_PRE    git hash-object esperado (contrato de identidad)
#   HUELLA      fragmento literal EXCLUSIVO del texto a insertar (grep -F)
#   LINEAS_PRE  (opcional) wc -l esperado
#
# Tupla de autorizacion: una orden de append/edicion que no traiga sus cuatro
# parametros se RECHAZA por defecto de contrato (no se escribe; se pide la tupla).
#
# RC (fail-closed):
#   0   PRE-VUELO OK — listo para recibir edicion
#   10  el objeto no existe
#   11  la huella YA habita en el destino  -> COLISION: la fila ya esta escrita
#   12  blob distinto al contrato          -> DESFASE DE OBJETO (arbol en movimiento)
#   13  estado sucio en git (modificaciones no adjudicadas)
#   14  el destino no vive en un arbol git -> el gate no puede validar identidad
#   15  lineas distintas al contrato
#   20  uso incorrecto (tupla incompleta, o TARGET no absoluto)
set -euo pipefail

TARGET="${1:-}"
BLOB_PRE="${2:-}"
HUELLA="${3:-}"
LINEAS_PRE="${4:-}"

# HUELLA-GATE-TELEMETRIA-RC-20261019
# Telemetria FUERA DE BANDA y con retroaccion nula sobre el arbol rastreado:
#   (a) no puede mover el RC      -> cada comando con || true, funcion con return 0;
#   (b) no puede abortar por set -u -> toda expansion con valor por defecto;
#   (c) no puede refrescar el indice -> todo git con --no-optional-locks;
#   (d) el hash del arbol NO es "ls-files -s" solo: eso es CIEGO a los archivos
#       no rastreados (??), que es exactamente la retroaccion que se quiere cazar.
#       Se hashea HEAD + indice + status --porcelain -uall, en flujo puro.
# Campos (7, siempre 7: el vacio se escribe '-'): hora_utc target blob_pre
#   blob_medido rc hash_arbol_ini hash_arbol_fin
# Destino del log (HUELLA-GATE-TELEMETRIA-HOME-20261020): fuera de /tmp, que
#   no sobrevive al reinicio, y fuera del arbol rastreado. Regla dura: la ruta
#   jamas es relativa — un fallback relativo crearia un no rastreado DENTRO del
#   repo y el gate acabaria midiendo su propia telemetria (el instrumento que
#   se cuenta a si mismo). Si HOME no existe cae a /tmp y se declara volatil;
#   nunca al arbol de trabajo.
GATE_TELEMETRIA_LOG="${GATE_TELEMETRIA_LOG:-${HOME:-/tmp}/preflight-telemetria.log}"
BLOB_ACTUAL=""
GATE_HASH_INI="sin-arbol"

_gate_hash_arbol() {
  local dir="${1:-}" raiz="" h=""
  # La raiz se resuelve a TOPLEVEL: medido, 'git -C subdir status' acota la salida
  # al subdirectorio, de modo que el hash de tools/ no es el hash del repo.
  if [ -n "$dir" ]; then
    raiz="$(git --no-optional-locks -C "$dir" rev-parse --show-toplevel 2>/dev/null)" || raiz=""
  fi
  if [ -n "$raiz" ]; then
    h="$({ git --no-optional-locks -C "$raiz" rev-parse HEAD 2>/dev/null
           git --no-optional-locks -C "$raiz" ls-files -s 2>/dev/null
           git --no-optional-locks -C "$raiz" status --porcelain -uall 2>/dev/null
         } | git --no-optional-locks hash-object --stdin 2>/dev/null)" || h=""
  fi
  printf '%s' "${h:-sin-arbol}"
}

_gate_telemetria_exit() {
  local rc="${1:-0}" dir="" fin="sin-arbol"
  if [ -n "${TARGET:-}" ]; then dir="$(dirname -- "$TARGET" 2>/dev/null)" || dir=""; fi
  fin="$(_gate_hash_arbol "$dir")"
  printf '%s %s %s %s %s %s %s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${TARGET:--}" "${BLOB_PRE:--}" \
    "${BLOB_ACTUAL:--}" "$rc" "${GATE_HASH_INI:-sin-arbol}" "${fin:-sin-arbol}" \
    >>"${GATE_TELEMETRIA_LOG:-${HOME:-/tmp}/preflight-telemetria.log}" 2>/dev/null || true
  return 0
}
trap '_gate_telemetria_exit $?' EXIT

if [ -n "${TARGET:-}" ]; then
  GATE_HASH_INI="$(_gate_hash_arbol "$(dirname -- "$TARGET" 2>/dev/null)")" || GATE_HASH_INI="sin-arbol"
fi

if [ -z "$TARGET" ] || [ -z "$BLOB_PRE" ] || [ -z "$HUELLA" ]; then
  echo "CONTRATO INCOMPLETO: uso = gate_pre_escritura.sh TARGET BLOB_PRE HUELLA [LINEAS_PRE]" >&2
  echo "Orden sin tupla de destino: rechazo por defecto de contrato. Cero escrituras." >&2
  exit 20
fi

# HUELLA-GATE-TARGET-ABSOLUTO-20261014
# 0. El TARGET debe ser ABSOLUTO. El gate re-basa la ruta con git -C (pasos 3 y
#    4): una ruta relativa se cuenta desde DOS bases distintas -> git fatal 128,
#    o peor, el hash de OTRO objeto que si existe bajo DIR. Fail-closed con causa.
case "$TARGET" in
  /*) : ;;
  *)  echo "CONTRATO INCOMPLETO: TARGET debe ser absoluto (recibido: '$TARGET')." >&2
      echo "El gate re-basa la ruta con git -C: relativa = base doble. Cero escrituras." >&2
      exit 20 ;;
esac

# 1. El objeto existe
if [ ! -f "$TARGET" ]; then
  echo "COLISION: $TARGET no existe." >&2
  exit 10
fi

# 2. El contenido ordenado YA existe en destino (la colision de la Pestana 3)
if grep -Fq -- "$HUELLA" "$TARGET"; then
  echo "COLISION DETECTADA: la huella '$HUELLA' ya habita en $TARGET. Abortando escritura." >&2
  exit 11
fi

# 2b. GUARDA DE REPO (fail-closed): sin arbol git, las condiciones 3 y 4 no
#     pueden medir nada; git status fuera de repo devuelve 128 y salida VACIA,
#     que en el gate ingenuo se lee como "limpio" -> verde falso.
DIR="$(cd "$(dirname "$TARGET")" && pwd)"
if ! git -C "$DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "SIN REPO: $TARGET no vive en un arbol git. El gate no puede validar identidad de blob. Fail-closed." >&2
  exit 14
fi

# 3. El archivo base no fue tocado por otra mano concurrente
BLOB_ACTUAL="$(git -C "$DIR" hash-object -- "$TARGET")"
if [ "$BLOB_ACTUAL" != "$BLOB_PRE" ]; then
  echo "DESFASE DE OBJETO: esperado $BLOB_PRE, medido $BLOB_ACTUAL. Arbol en movimiento." >&2
  exit 12
fi

# 4. Estado de git limpio en ese archivo
if [ -n "$(git -C "$DIR" status --porcelain -- "$TARGET")" ]; then
  echo "ESTADO SUCIO: $TARGET tiene modificaciones no adjudicadas." >&2
  exit 13
fi

# 5. (opcional) el contrato de lineas
# HUELLA-FIX-WRAPPER-PADDING-20261018: se recortan ambos lados antes de comparar.
if [ -n "$LINEAS_PRE" ]; then
  L_ACT="$(wc -l < "$TARGET" | tr -d ' ')"
  L_PRE="$(printf '%s' "$LINEAS_PRE" | tr -d '[:space:]')"
  if [ "$L_ACT" != "$L_PRE" ]; then
    echo "DESFASE DE LINEAS: esperado $L_PRE, medido $L_ACT." >&2
    exit 15
  fi
fi

echo "PRE-VUELO OK: $TARGET listo para recibir edicion sobre $BLOB_PRE."
exit 0
