"""Smoke-check: configs, SQLite (required), Neo4j (needed from Phase 2)."""
import sys

from rich import print

from regintel.config import get_bank_profile, get_model_routing, get_sources, get_taxonomy
from regintel.db import audit, audit_trail, init_db
from regintel.settings import get_settings


def check_configs(s) -> None:
    bp = get_bank_profile()
    print(f"[green]✓[/] Bank profile: {bp.name} | {len(bp.products)} products")
    print(f"[green]✓[/] Taxonomy: {len(get_taxonomy().topics)} topics | "
          f"routing: {len(get_model_routing().routing)} tasks")
    for reg in get_sources().regulations:
        for v in reg.versions:
            ok = (s.root / v.path).exists()
            print(f"   {'[green]✓[/]' if ok else '[yellow]! missing[/]'} {reg.id}/{v.label}")


def check_sqlite(s) -> bool:
    try:
        init_db()
        audit("rule:check_infra", "PING", "system", "infra")
        assert audit_trail("system", "infra"), "audit write/read failed"
        print(f"[green]✓[/] SQLite ready -> {s.sqlite_file}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[red]✗ SQLite failed:[/] {e}")
        return False


def check_neo4j(s) -> bool:
    try:
        from neo4j import GraphDatabase
        with GraphDatabase.driver(
            s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password.get_secret_value())
        ) as drv:
            drv.verify_connectivity()
            ver = drv.execute_query("CALL dbms.components() YIELD versions RETURN versions[0] AS v")
            print(f"[green]✓[/] Neo4j reachable at {s.neo4j_uri} (v{ver.records[0]['v']})")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[yellow]! Neo4j not reachable[/] ({type(e).__name__}: {e})")
        print("   Needed from Phase 2. Fix options: `docker compose up -d neo4j`, "
              "Neo4j Desktop, or AuraDB Free -> update NEO4J_* in .env")
        return False


def main() -> None:
    s = get_settings()
    check_configs(s)
    sqlite_ok = check_sqlite(s)
    check_neo4j(s)
    sys.exit(0 if sqlite_ok else 1)


if __name__ == "__main__":
    main()