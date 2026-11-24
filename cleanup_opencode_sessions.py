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
    keep = set(r["id"] for r in rows[:max(0, args.keep)])
    old = [r for r in rows
           if r.get("id") not in keep
           and now - r.get("created", 0) >= args.max_age_days * 86400]
    if not old:
        print(f"OK: {len(rows)} sesiones registradas, ninguna surpassa los "
              f"{args.max_age_days} días fuera de las {args.keep} recientes")
        return 0

    for row in old:
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
