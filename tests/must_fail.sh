#!/usr/bin/env bash
# must_fail.sh — banco de casos del preflight gate.
#
# Cada caso exige DOS cosas, no una:
#   (a) el RC esperado  -> condicion necesaria
#   (b) la CAUSA impresa -> la que dice QUE defecto se midio
# Un caso que devuelve el RC esperado por otro motivo NO discrimina y sale ROJO.
#
# Uso: bash tests/must_fail.sh [ruta/al/preflight-gate.sh]
set -uo pipefail

AQUI="$(cd "$(dirname "$0")" && pwd)"
GATE="${1:-$AQUI/../preflight-gate.sh}"
[ -f "$GATE" ] || { echo "GATE AUSENTE: $GATE"; exit 2; }

T="$(mktemp -d "${TMPDIR:-/tmp}/preflight_test.XXXXXX")" || exit 2
trap 'rm -rf "$T"' EXIT
export GATE_TELEMETRIA_LOG="$T/telemetria.log"

(cd "$T" && git init -q && git config user.email t@t.t && git config user.name t) || exit 2
printf 'uno\ndos\ntres\n' > "$T/obj.txt"
(cd "$T" && git add obj.txt && git commit -qm base) || exit 2

OBJ="$T/obj.txt"
BLOB="$(cd "$T" && git hash-object -- obj.txt)"
LINEAS="$(wc -l < "$OBJ" | tr -d ' ')"

FALLOS=0
TOTAL=0
corre() { # corre <rc_esperado> <causa_esperada> <args...>
  local rc_esp="$1" causa_esp="$2"; shift 2
  local salida rc primera
  salida="$(bash "$GATE" "$@" 2>&1)"; rc=$?
  primera="$(printf '%s\n' "$salida" | head -1)"
  TOTAL=$((TOTAL + 1))
  if [ "$rc" = "$rc_esp" ] && printf '%s' "$primera" | grep -qF -- "$causa_esp"; then
    printf 'OK    rc=%-3s %s\n' "$rc" "$primera"
  else
    FALLOS=$((FALLOS + 1))
    printf 'ROJO  rc=%-3s (esperado %s / causa "%s") :: %s\n' "$rc" "$rc_esp" "$causa_esp" "$primera"
  fi
}

echo "GATE=$GATE"
echo "OBJ=$OBJ BLOB=$BLOB LINEAS=$LINEAS"
echo ""

# 20 — tupla incompleta
corre 20 "CONTRATO INCOMPLETO" "$OBJ"
# 20 — target relativo (el gate re-basa con git -C: relativa = base doble)
corre 20 "TARGET debe ser absoluto" "obj.txt" "$BLOB" "uno" "$LINEAS"
# 10 — el objeto no existe
corre 10 "no existe" "$T/ausente.txt" "$BLOB" "uno" "$LINEAS"
# 11 — la huella ya habita en el destino (colision: la fila ya esta escrita)
corre 11 "COLISION DETECTADA" "$OBJ" "$BLOB" "dos" "$LINEAS"
# 12 — blob distinto al contrato (el arbol se movio)
corre 12 "DESFASE DE OBJETO" "$OBJ" "1111111111111111111111111111111111111111" "cuatro" "$LINEAS"
# 15 — lineas distintas al contrato
corre 15 "DESFASE DE LINEAS" "$OBJ" "$BLOB" "cuatro" "999"
# 13 — estado sucio en git
printf 'cuatro\n' >> "$OBJ"
corre 13 "ESTADO SUCIO" "$OBJ" "$(cd "$T" && git hash-object -- obj.txt)" "cinco" "$((LINEAS + 1))"
(cd "$T" && git checkout -- obj.txt)
# 14 — el destino no vive en un arbol git (fail-closed: sin arbol no hay identidad)
# Va FUERA de $T: un subdirectorio de $T seguiria dentro del arbol y el caso
# mediria otra rama (el gate resolvio el toplevel, no el directorio).
FUERA="$(mktemp -d "${TMPDIR:-/tmp}/preflight_norepo.XXXXXX")"
printf 'x\n' > "$FUERA/suelto.txt"
corre 14 "SIN REPO" "$FUERA/suelto.txt" "$(git hash-object -- "$FUERA/suelto.txt")" "y" "1"
rm -rf "$FUERA"
# 0 — caso limpio: TODO lo anterior debe poder pasar
corre 0 "PRE-VUELO OK" "$OBJ" "$BLOB" "cuatro" "$LINEAS"

echo ""
echo "BANCO: $((TOTAL - FALLOS))/$TOTAL"
# No se mide el residuo desde aqui: el directorio del banco existe mientras corre
# (el instrumento se contaria a si mismo). Se verifica DESPUES del trap, fuera.
[ "$FALLOS" -eq 0 ] || exit 1
