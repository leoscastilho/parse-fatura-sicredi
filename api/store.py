"""Estado transacional em SQLite.

Um `transaction_id` amarra o upload ao export. Entre os dois, o usuário pode
levar meia hora revisando, recarregar a aba, ou o container pode reiniciar —
por isso o estado é persistente em volume, e não em memória de processo nem
em Redis sem persistência.

Duas colunas guardam coisas bem diferentes:

  * `lines_json`      — o que foi LIDO do extrato. Imutável durante a
                        transação inteira. É a fonte de verdade.
  * `assignments_json`— o que o USUÁRIO decidiu. Sobrescrito a cada /validate,
                        /preview ou /export.

Manter os dois separados é o que permite refazer o /preview quantas vezes for
preciso sem nunca reprocessar o .xls — e garante que um erro de atribuição
não corrompa os dados originais.

`yaml_snapshot` guarda o `categories.yml` como estava no upload. É contra isso
que as edições da sessão são aplicadas, e é o SHA dele que detecta se alguém
mexeu no repositório no meio da revisão.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id                  TEXT PRIMARY KEY,
    created_at          TEXT NOT NULL,
    expires_at          TEXT NOT NULL,
    filename            TEXT NOT NULL,
    yaml_snapshot       TEXT NOT NULL,
    yaml_working        TEXT NOT NULL,
    yaml_sha            TEXT,
    statements_json     TEXT NOT NULL,
    dropped_json        TEXT NOT NULL,
    lines_json          TEXT NOT NULL,
    modo                TEXT NOT NULL DEFAULT 'fatura',
    assignments_json    TEXT NOT NULL DEFAULT '[]',
    mapping_changes_json TEXT NOT NULL DEFAULT '[]',
    travel_json         TEXT NOT NULL DEFAULT '[]',
    travel_rejected_json TEXT NOT NULL DEFAULT '[]',
    travel_pinned_json  TEXT NOT NULL DEFAULT '{}',
    committed_url       TEXT
);
CREATE INDEX IF NOT EXISTS idx_transactions_expires ON transactions(expires_at);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TransactionNotFound(KeyError):
    pass


class Store:
    def __init__(self, db_path: Path, ttl_hours: int = 24):
        self.db_path = db_path
        self.ttl = timedelta(hours=ttl_hours)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            # Migração para bancos criados antes destas colunas existirem. O
            # estado é efêmero (TTL de 24h), mas derrubar a transação de alguém
            # no meio de uma revisão por causa de uma coluna nova seria
            # grosseiro. Todo default aqui precisa reproduzir o comportamento
            # antigo: 'fatura' e listas vazias não mudam nada para quem já
            # estava no meio do fluxo.
            colunas = {row[1] for row in conn.execute("PRAGMA table_info(transactions)")}
            for nome, ddl in (
                ("modo", "TEXT NOT NULL DEFAULT 'fatura'"),
                ("travel_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("travel_rejected_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("travel_pinned_json", "TEXT NOT NULL DEFAULT '{}'"),
            ):
                if nome not in colunas:
                    conn.execute(f"ALTER TABLE transactions ADD COLUMN {nome} {ddl}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=10)
        conn.row_factory = sqlite3.Row
        # WAL deixa leitura e escrita concorrerem sem travar — importante
        # porque o /preview lê enquanto o /update-mapping escreve.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------ CRUD

    def create(
        self,
        *,
        filename: str,
        yaml_text: str,
        yaml_sha: str | None,
        statements: list[dict],
        dropped: list[dict],
        lines: list[dict],
        modo: str = "fatura",
    ) -> tuple[str, datetime]:
        transaction_id = uuid.uuid4().hex
        created = _now()
        expires = created + self.ttl
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO transactions
                   (id, created_at, expires_at, filename, yaml_snapshot, yaml_working,
                    yaml_sha, statements_json, dropped_json, lines_json, modo)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    transaction_id,
                    created.isoformat(),
                    expires.isoformat(),
                    filename,
                    yaml_text,
                    yaml_text,
                    yaml_sha,
                    json.dumps(statements, ensure_ascii=False),
                    json.dumps(dropped, ensure_ascii=False),
                    json.dumps(lines, ensure_ascii=False),
                    modo,
                ),
            )
        return transaction_id, expires

    def get(self, transaction_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM transactions WHERE id = ?", (transaction_id,)
            ).fetchone()
        if row is None:
            raise TransactionNotFound(transaction_id)

        record = dict(row)
        if datetime.fromisoformat(record["expires_at"]) < _now():
            self.delete(transaction_id)
            raise TransactionNotFound(transaction_id)

        for column in ("statements", "dropped", "lines", "assignments",
                       "mapping_changes", "travel", "travel_rejected",
                       "travel_pinned"):
            record[column] = json.loads(record.pop(f"{column}_json"))
        return record

    def save_assignments(self, transaction_id: str, assignments: list[dict]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE transactions SET assignments_json = ? WHERE id = ?",
                (json.dumps(assignments, ensure_ascii=False), transaction_id),
            )

    def save_travel(self, transaction_id: str, ranges: list[dict]) -> None:
        """Guarda os PERÍODOS, não a marcação das linhas.

        A marca `viagem` de cada linha é derivada destes períodos na leitura
        (`mark_travel`), o que mantém `lines_json` imutável e torna reeditar um
        período uma operação trivialmente idempotente: some o período, some a
        marca, sem precisar desfazer nada.
        """
        with self._connect() as conn:
            conn.execute(
                "UPDATE transactions SET travel_json = ? WHERE id = ?",
                (json.dumps(ranges, ensure_ascii=False), transaction_id),
            )

    def save_travel_rejected(self, transaction_id: str, line_ids: list[str]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE transactions SET travel_rejected_json = ? WHERE id = ?",
                (json.dumps(line_ids, ensure_ascii=False), transaction_id),
            )

    def save_travel_pinned(self, transaction_id: str, pinned: dict[str, str]) -> None:
        """`line_id -> chave do período`: as linhas penduradas na viagem à mão.

        Fica ao lado de `travel_rejected` porque é a mesma ideia com o sinal
        trocado: uma lista diz "caiu na janela e NÃO é viagem", a outra diz
        "não caiu e É". Nenhuma das duas toca `lines_json` — as duas são
        aplicadas na leitura, o que mantém editar períodos idempotente.
        """
        with self._connect() as conn:
            conn.execute(
                "UPDATE transactions SET travel_pinned_json = ? WHERE id = ?",
                (json.dumps(pinned, ensure_ascii=False), transaction_id),
            )

    def save_yaml_working(
        self, transaction_id: str, yaml_text: str, mapping_changes: list[dict]
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE transactions
                   SET yaml_working = ?, mapping_changes_json = ?
                   WHERE id = ?""",
                (yaml_text, json.dumps(mapping_changes, ensure_ascii=False), transaction_id),
            )

    def mark_committed(self, transaction_id: str, url: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE transactions SET committed_url = ? WHERE id = ?",
                (url, transaction_id),
            )

    def delete(self, transaction_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))

    def purge_expired(self) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM transactions WHERE expires_at < ?", (_now().isoformat(),)
            )
            return cursor.rowcount or 0
