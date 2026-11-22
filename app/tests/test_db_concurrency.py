"""
Concurrency guarantees of the SQLite engine used with a database file.

gunicorn runs several threads per worker, /readyz checks the database from an
executor thread, and the UI tests run the app on a server thread. With a single
connection shared by every thread (StaticPool), two threads could use the same
sqlite3 connection at once, which can crash the process with a segmentation fault.
"""
import threading

from sqlalchemy import text
from sqlalchemy.pool import StaticPool


def test_file_database_does_not_share_one_connection(test_app):
    assert not isinstance(test_app.connection.engine.pool, StaticPool)


def test_threads_get_their_own_connections(test_app):
    engine = test_app.connection.engine
    threads_count = 4
    barrier = threading.Barrier(threads_count)
    seen, errors = [], []

    def hold_a_connection():
        try:
            with engine.connect() as conn:
                seen.append(id(conn.connection.dbapi_connection))
                # Keep the connection checked out until every thread has one.
                barrier.wait(timeout=10)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=hold_a_connection) for _ in range(threads_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert not errors
    assert len(set(seen)) == threads_count


def test_concurrent_reads_and_writes(test_app):
    """Several threads writing and reading at once neither crash nor lose rows."""
    models = test_app.models
    connection = test_app.connection
    threads_count, writes_each = 4, 10
    errors = []

    def worker(n):
        session = connection.get_db_session()
        try:
            for i in range(writes_each):
                session.add(models.Client(nom=f"Client {n}-{i}"))
                session.commit()
                session.execute(text("SELECT COUNT(*) FROM clients")).scalar_one()
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(threads_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors
    session = connection.get_db_session()
    try:
        assert session.query(models.Client).count() == threads_count * writes_each
    finally:
        session.close()
