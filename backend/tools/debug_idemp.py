from qsagent.storage.db import QSStore
from qsagent.ingest.cli import _ingest_masterfile
from pathlib import Path
store = QSStore(":memory:")
pid = store.get_or_create_project("Test")
_ingest_masterfile(store, pid, Path("tests/fixtures/master"))
print("RUN 1 NODES:")
for n in store.conn.execute("SELECT id, node_type, label, ingest_key FROM evidence_nodes").fetchall():
    print(dict(n))
_ingest_masterfile(store, pid, Path("tests/fixtures/master"))
print("RUN 2 NODES:")
for n in store.conn.execute("SELECT id, node_type, label, ingest_key FROM evidence_nodes").fetchall():
    print(dict(n))