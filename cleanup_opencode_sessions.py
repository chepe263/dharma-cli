#!/usr/bin/env python3
"""Limpia SOLO las sesiones de OpenCode creadas por el puente ACP de dharma.

El registro ~/.kiro/crew/dharma_opencode_sessions.json se escribe al crear una
sesión nueva (nunca al restaurar una con session/load). Las sesiones del usuario
en OpenCode no aparecen ahí y por tanto nunca se tocan.

Uso:
  python3 cleanup_opencode_sessions.py            # simulación (no borra)
  python3 cleanup_opencode_sessions.py --apply    # borra las viejas
  python3 cleanup_opencode_sessions.py --keep 10 --max-age-days 3 --apply
"""
import argparse
import json
import os
import subprocess
import sys
import time

REGISTRY = os.path.expanduser("~/.kiro/crew/dharma_opencode_sessions.json")
DEFAULT_BIN = os.environ.get("DHARMA_OPENCODE_BIN", "opencode")
DB = os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
    "opencode", "opencode.db",
)
# El puente mantiene UNA sesión viva por workspace: nunca la borramos.
SESSION_MAP = os.path.expanduser("~/.kiro/crew/dharma_session_map.json")


def _active_ids() -> set:
    try:
        with open(SESSION_MAP, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return {v for v in data.values() if isinstance(v, str)}
    except (OSError, ValueError):
        return set()


def _children_of(parent_ids: list) -> list:
    """Sesiones hijas (subagentes de OpenCode) de las sesiones del registro."""
    if not parent_ids:
        return []
    try:
        import sqlite3  # stdlib
        marks = ",".join("?" for _ in parent_ids)
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        rows = list(con.execute(
            f"select id, time_created from session_v2 where parent_id in ({marks})",
            parent_ids,
        ))
        con.close()
        return [{"id": r[0], "created": (r[1] or 0) / 1000} for r in rows]
    except Exception:  # noqa: BLE001 - si no hay DB, simplemente no hay hijas
        return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=20, help="sesiones nuevas a conservar")
    ap.add_argument("--max-age-days", type=float, default=7.0)
    ap.add_argument("--apply", action="store_true", help="ejecutar borrados")
    ap.add_argument("--bin", default=DEFAULT_BIN)
    args = ap.parse_args()

    try:
        with open(REGISTRY, "r", encoding="utf-8") as fh:
            rows = json.load(fh)
        if not isinstance(rows, list):
            rows = []
    except (OSError, ValueError):
        print("sin registro de sesiones del puente; nada que limpiar")
        return 0

    rows.sort(key=lambda r: r.get("created", 0), reverse=True)
    now = time.time()
    active = _active_ids()
    keep = set(r["id"] for r in rows[:max(0, args.keep)]) | active
    old = [r for r in rows
           if r.get("id") not in keep
           and now - r.get("created", 0) >= args.max_age_days * 86400]
    if not old:
        print(f"OK: {len(rows)} sesiones registradas, ninguna surpassa los "
              f"{args.max_age_days} días fuera de las {args.keep} recientes")
        return 0

    # Los subagentes (task) viven como sesiones hijas: se borran con su padre.
    children = _children_of([r["id"] for r in old])
    if children:
        print(f"   (+{len(children)} sesión(es) hija(s) de subagente)")
    to_delete = children + old

    for row in to_delete:
        sid = row.get("id", "")
        verb = "borrando" if args.apply else "borraría"
        if args.apply:
            proc = subprocess.run([args.bin, "session", "delete", sid],
                                  capture_output=True, text=True, timeout=60)
            ok = proc.returncode == 0
            print(f"{'✅' if ok else '❌'} {verb} {sid} "
                  f"({(now - row.get('created', 0)) / 86400:.1f} días)")
            if not ok:
                print(f"   {proc.stderr.strip()[:200]}")
        else:
            print(f"🔎 {verb} {sid} ({(now - row.get('created', 0)) / 86400:.1f} días)")

    if args.apply:
        survivors = [r for r in rows if r not in old]
        try:
            with open(REGISTRY, "w", encoding="utf-8") as fh:
                json.dump(survivors, fh)
        except OSError as exc:  # noqa: BLE001
            print(f"no pude actualizar el registro: {exc}", file=sys.stderr)
            return 1
    else:
        print("\n(simulación; pasa --apply para borrar de verdad)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
