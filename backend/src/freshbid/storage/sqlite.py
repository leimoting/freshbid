from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from freshbid.models import DecisionEvent, DecisionEventType, OutcomeRecord


class SQLiteRepository:
    """保存推荐生命周期和反馈结果的本地SQLite实现。"""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database_path)
        self.connection.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS decision_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recommendation_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                actor TEXT NOT NULL,
                effective_price_cents INTEGER,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_events_recommendation
            ON decision_events (recommendation_id, id);

            CREATE TABLE IF NOT EXISTS outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recommendation_id TEXT NOT NULL,
                interval_start TEXT NOT NULL,
                interval_end TEXT NOT NULL,
                effective_price_cents INTEGER NOT NULL,
                units_sold INTEGER NOT NULL,
                ending_inventory INTEGER NOT NULL,
                policy_version TEXT NOT NULL,
                model_version TEXT NOT NULL,
                source TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def append_event(self, event: DecisionEvent) -> None:
        payload = event.model_dump(mode="json")
        self.connection.execute(
            """
            INSERT INTO decision_events (
                recommendation_id, event_type, occurred_at, actor,
                effective_price_cents, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.recommendation_id,
                event.event_type.value,
                event.occurred_at.isoformat(),
                event.actor,
                event.effective_price_cents,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        self.connection.commit()

    def list_events(self, recommendation_id: str) -> tuple[DecisionEvent, ...]:
        rows = self.connection.execute(
            """
            SELECT payload_json
            FROM decision_events
            WHERE recommendation_id = ?
            ORDER BY id
            """,
            (recommendation_id,),
        ).fetchall()
        return tuple(DecisionEvent.model_validate_json(row["payload_json"]) for row in rows)

    def append(self, outcome: OutcomeRecord) -> None:
        payload = outcome.model_dump(mode="json")
        self.connection.execute(
            """
            INSERT INTO outcomes (
                recommendation_id, interval_start, interval_end,
                effective_price_cents, units_sold, ending_inventory,
                policy_version, model_version, source, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                outcome.recommendation_id,
                outcome.interval_start.isoformat(),
                outcome.interval_end.isoformat(),
                outcome.effective_price_cents,
                outcome.units_sold,
                outcome.ending_inventory,
                outcome.policy_version,
                outcome.model_version,
                outcome.source,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        self.connection.commit()

    def list_outcomes(self, recommendation_id: str) -> tuple[OutcomeRecord, ...]:
        rows = self.connection.execute(
            """
            SELECT payload_json
            FROM outcomes
            WHERE recommendation_id = ?
            ORDER BY id
            """,
            (recommendation_id,),
        ).fetchall()
        return tuple(OutcomeRecord.model_validate_json(row["payload_json"]) for row in rows)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> SQLiteRepository:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
