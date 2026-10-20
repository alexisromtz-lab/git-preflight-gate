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

## La Action de la raíz: CI Authority Gate

El `action.yml` de este repositorio es una **action composite** que ejecuta un segundo
instrumento, `sim/gate_workflows_ci.py`: un verificador estructural de la topología de
autoridad en `.github/workflows/`. Cinco invariantes contables, siete causas tipadas,
`RC = min(causas)`, `UNIVERSO_VACIO` = causa `31` (cero archivos es fracaso, no
aprobación).

Identidad medida del instrumento copiado a este árbol:

| propiedad | valor |
|---|---|
| líneas / bytes | 615 / 21470 |
| git blob | `ff9c69a944e7965b3fda0901f7745d80a9e85c7c` |
| sha256-16 | `073954a84f3c84fd` |

Uso como consumidor:

```yaml
- uses: alexisromtz-lab/git-preflight-gate@v0.1.1
  with:
    workflows-dir: .github/workflows
    fail-on-red: "true"
```

Los cuatro inputs (`workflows-dir`, `root`, `python-bin`, `fail-on-red`) y los dos
outputs (`rc`, `veredicto`) conservan el contrato del `action.yml` que este repositorio
**reemplaza**: cualquier CI que apuntaba a la action de la raíz de un repositorio de
trabajo migra cambiando sólo el nombre del repositorio en `uses:`; el resto del bloque
`with:` no cambia.

La acción no adivina el dominio: el instrumento resuelve un único punto,
`<raiz>/.github/workflows`. Por eso `workflows-dir` se enruta a `--file` cuando el path
existe y a `--root` cuando no. Un `--root` que aceptara cualquier directorio con YAMLs
sería editar el metro para que quepa la demo.

Dependencia única: `PyYAML==6.0.3`, declarada como extra opcional
(`pip install ".[ci-authority-gate]"`). El resto es stdlib. El instrumento no lee red,
no escribe archivos, no usa aleatoriedad y no consulta el reloj.

Control positivo medido: workflow con `permissions: contents: read` y `uses` fijado por
SHA completo ⇒ `CAUSAS PRESENTES: ninguna`, `VEREDICTO: PASA`, `RC=0`. Control negativo:
`pull_request_target` + `permissions: write-all` + `curl | bash` + `actions/checkout@v4`
⇒ `CAUSAS PRESENTES: [1, 2, 5]`, `RC=1`. Universo vacío ⇒ `CAUSAS PRESENTES: [31]`,
`RC=31`.

## Los dos instrumentos no se confunden

| instrumento | pregunta | forma |
|---|---|---|
| `preflight-gate.sh` | ¿este agente puede escribir *este archivo* ahora? | CLI bash, tupla de destino |
| `sim/gate_workflows_ci.py` | ¿esta topología de CI concentra autoridad sin control? | action composite + CLI Python |

El primero protege una escritura; el segundo audita un árbol de workflows. Comparten la
doctrina (fail-closed, causa impresa, cero archivos = imposibilidad declarada), no el
código.

## Licencia

MIT. Ver `LICENSE`.
