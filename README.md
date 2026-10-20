# git-preflight-gate

**Un gate de integridad para agentes de IA autónomos: que un bot no ensucie el árbol
ni cometa colisiones de concurrencia.**

Antes de que un agente escriba una línea, el gate exige una tupla de destino —
`TARGET` (absoluto) · `BLOB_PRE` · `HUELLA` · `LINEAS_PRE` — y devuelve un código que
**autoriza o bloquea** la escritura. Sin corrida previa con `RC 0`, no se escribe.

```bash
bash preflight-gate.sh /ruta/absoluta/archivo.py <blob_pre> <huella_exclusiva> <lineas_pre>
```

## Los códigos (fail-closed)

| RC | significado |
|---:|---|
| `0` | PRE-VUELO OK — el destino acepta la edición sobre `BLOB_PRE` |
| `10` | el objeto no existe (creación) |
| `11` | **la huella ya habita en el destino** ⇒ colisión: la fila ya está escrita |
| `12` | blob medido ≠ contrato ⇒ el árbol se movió |
| `13` | estado sucio en git ⇒ modificaciones no adjudicadas |
| `14` | el destino no vive en un árbol git ⇒ el gate no puede validar identidad |
| `15` | líneas distintas al contrato |
| `20` | uso incorrecto (tupla incompleta, o `TARGET` no absoluto) |

Cada código sale **con su causa impresa**. Un `RC` correcto por el motivo equivocado
no es un gate: es una coincidencia.

## Por qué `RC 11` es el caso que importa

El defecto más caro de un agente no es equivocarse de ruta: es **volver a escribir algo
que otra mano ya escribió**, y reportar éxito. La huella es un fragmento literal
exclusivo del texto a insertar; si ya está en el destino, la escritura se rechaza antes
de tocar el archivo. El gate no adivina intenciones: niega la colisión.

## Por qué `TARGET` debe ser absoluto

El gate re-basa con `git -C <directorio>` para medir el blob y el estado. Una ruta
relativa se contaría desde **dos bases distintas** (`cwd` y el directorio del objeto) y
podría hashear *otro* archivo que sí existe. Fail-closed: ruta relativa ⇒ `RC 20`.

## Por qué `RC 14` existe

Sin árbol git, `git status` devuelve 128 con salida vacía. Un gate ingenuo lee esa
salida vacía como «limpio» y da verde. Aquí se declara la imposibilidad: sin árbol no
hay identidad de blob que valga.

## Banco de casos

```bash
bash tests/must_fail.sh
```

9 casos, **9/9**, cada uno verificando código **y** causa: `20` (tupla incompleta),
`20` (ruta relativa), `10`, `11`, `12`, `15`, `13`, `14` y el control positivo `0`.
Un banco sin control positivo sólo demuestra que el instrumento sabe decir que no.

## Telemetría fuera de banda

Cada corrida anota 7 campos (`hora_utc target blob_pre blob_medido rc hash_arbol_ini
hash_arbol_fin`) en `${GATE_TELEMETRIA_LOG:-$HOME/preflight-telemetria.log}`. La
telemetría **no puede mover el RC**, **no puede abortar** y **no puede refrescar el
índice** (`--no-optional-locks`). La ruta nunca es relativa: un log dentro del árbol se
vuelve un archivo no rastreado, y entonces el gate termina midiendo su propia
telemetría — el instrumento que se cuenta a sí mismo.

## Límites declarados

- Sobre un `TARGET` inexistente el gate corta en `RC 10` **antes** de comparar blob y
  líneas: en la rama de creación, la tupla es **contrato**, no medición. Se declara; no
  se repara (fail-closed es correcto).
- No juzga el contenido: certifica identidad y colisión, no corrección.

## Licencia

MIT. Ver `LICENSE`.
