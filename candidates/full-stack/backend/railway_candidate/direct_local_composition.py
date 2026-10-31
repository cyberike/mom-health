"""Explicit, loopback-only composition for private synthetic review.

Constructing the config does not bind a socket, connect SQLite, run migrations,
create files or start the ASGI lifespan. Use only an isolated disposable
fixture, never a retained volume. The
contents of an arbitrary file cannot be proven synthetic from its path: never
point this at user or retained evidence data.
This is not a production or Railway deployment entry point.
"""
from railway_candidate.budgeted_sqlite import BudgetedSQLiteRepository
from railway_candidate.direct_candidate import create_direct_candidate
from railway_candidate.direct_server_config import build_local_config
from railway_candidate.existing_only_storage import validate_existing_storage


def build_budgeted_local_config(host, caregiver_user, caregiver_password,
                                phone_token, database_path, mount_path, *,
                                private_child=False, port=0,
                                caregiver_previous=None, phone_previous=None):
    """Return a loopback config for a pre-existing disposable synthetic DB.

    The function accepts credentials in memory only; it does not intentionally
    write them to a file, environment, URL or log. Calling the returned server's
    lifespan would run SQLite initialization and migrations and may mutate that
    fixture.
    """
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError('Port must be an integer in 0..65535')
    if not isinstance(private_child, bool):
        raise ValueError('Invalid private-child storage configuration')
    # Path safety/ownership checks occur before even constructing the repo;
    # create_direct_candidate independently repeats them before app creation.
    validated_path = validate_existing_storage(mount_path, database_path, private_child=private_child)
    # SQLite mode=rw refuses to recreate the database if it disappears after
    # either of the noncreating validation checks.
    repository = BudgetedSQLiteRepository(validated_path, require_existing=True)
    app = create_direct_candidate(
        host, caregiver_user, caregiver_password, phone_token,
        validated_path, mount_path, caregiver_previous=caregiver_previous,
        phone_previous=phone_previous, private_child=private_child,
        repository=repository, require_existing_storage=True,
    )
    return build_local_config(app, port=port)
