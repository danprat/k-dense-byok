"""One-shot sandbox bootstrap for every known project.

Historically this script set up a single ``sandbox/`` folder. Now that each
project has its own isolated sandbox under ``projects/<id>/sandbox/``, this
script:

1. Migrates the legacy ``sandbox/`` + ``user_config/custom_mcps.json`` into
   ``projects/default/`` on first run (idempotent - no-op afterwards).
2. Runs the heavy sandbox init (copy GEMINI.md, merge Gemini settings,
   seed pyproject.toml, ``uv sync``, download scientific skills) for every
   non-archived project in the registry.

Running this is safe and idempotent; it can be re-invoked any time a new
dependency is added to the sandbox pyproject template.
"""

from __future__ import annotations

from kady_agent.projects import (
    DEFAULT_PROJECT_ID,
    ensure_project_exists,
    init_project_sandbox,
    list_projects,
    migrate_legacy_layout,
)


def main() -> None:
    migrated = migrate_legacy_layout()
    if migrated:
        print("Migrated legacy sandbox/ + user_config/ into projects/default/.")

    # Guarantee the default project exists even on a fresh checkout where
    # there was no legacy layout to migrate.
    ensure_project_exists(DEFAULT_PROJECT_ID)

    projects = list_projects()
    if not projects:
        # Defensive: list_projects() self-heals from on-disk state, so this
        # branch means the registry truly is empty. Seed the default.
        ensure_project_exists(DEFAULT_PROJECT_ID)
        projects = list_projects()

    for meta in projects:
        if meta.archived:
            print(f"Skipping archived project: {meta.id}")
            continue
        print(f"== Initializing project: {meta.id} ({meta.name}) ==")
        init_project_sandbox(meta.id)


if __name__ == "__main__":
    main()
