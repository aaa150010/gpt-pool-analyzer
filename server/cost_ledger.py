from __future__ import annotations

from typing import Any, Callable


class CostLedger:
    """Persistent cost ledger operations shared by routes and withdrawals."""

    def __init__(
        self,
        *,
        connect: Callable[..., Any],
        dumps: Callable[[Any], str],
        loads: Callable[[str | None, Any], Any],
        utc_now: Callable[[], str],
        number: Callable[[Any, float], float],
    ) -> None:
        self._connect = connect
        self._dumps = dumps
        self._loads = loads
        self._utc_now = utc_now
        self._number = number

    def insert(self, item: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO cost_additions(id, date, note, amount, created_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (
                    item.get("id") or f"server-{self._utc_now()}",
                    item.get("date") or self._utc_now(),
                    item.get("note") or "",
                    self._number(item.get("amount"), 0),
                    item.get("createdAt") or item.get("created_at") or self._utc_now(),
                ),
            )

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM cost_additions ORDER BY date ASC, created_at ASC"
            ).fetchall()
        return [
            {
                "id": row["id"],
                "date": row["date"],
                "note": row["note"],
                "amount": row["amount"],
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

    def clear_all(self) -> float:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS total FROM cost_additions"
            ).fetchone()
            total = self._number(row["total"] if row else 0, 0)
            conn.execute("DELETE FROM cost_additions")
        return total

    def clear_snapshot(self, conn: Any, snapshot: list[dict[str, Any]]) -> dict[str, Any]:
        """Clear only rows frozen by one withdrawal, inside its transaction."""
        ids = [
            str(item.get("id") or "").strip()
            for item in snapshot
            if str(item.get("id") or "").strip()
        ]
        stored_row = conn.execute(
            "SELECT value FROM settings WHERE key = 'stored_state'"
        ).fetchone()
        stored = self._loads(stored_row["value"], {}) if stored_row else {}
        if not ids:
            return {
                "clearedAmount": 0.0,
                "clearedCount": 0,
                "remainingCost": self._number(stored.get("cost"), 0),
            }

        placeholders = ", ".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT id, amount FROM cost_additions WHERE id IN ({placeholders})",
            tuple(ids),
        ).fetchall()
        if not rows:
            return {
                "clearedAmount": 0.0,
                "clearedCount": 0,
                "remainingCost": self._number(stored.get("cost"), 0),
            }

        cleared_amount = round(sum(self._number(row["amount"], 0) for row in rows), 2)
        conn.execute(f"DELETE FROM cost_additions WHERE id IN ({placeholders})", tuple(ids))
        stored["cost"] = round(
            max(self._number(stored.get("cost"), 0) - cleared_amount, 0.0),
            2,
        )
        conn.execute(
            "INSERT INTO settings(key, value) VALUES('stored_state', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (self._dumps(stored),),
        )
        return {
            "clearedAmount": cleared_amount,
            "clearedCount": len(rows),
            "remainingCost": stored["cost"],
        }

    def recover_snapshot(self, conn: Any, snapshot: list[dict[str, Any]], amount: float) -> dict[str, Any]:
        """Apply one successful withdrawal to the live cost ledger.

        Frozen rows are consumed oldest-first. Costs added after the withdrawal
        task was created are never edited, while the stored total also supports
        legacy costs that do not have a matching history row.
        """
        requested_cents = max(int(round(self._number(amount, 0) * 100)), 0)
        stored_row = conn.execute(
            "SELECT value FROM settings WHERE key = 'stored_state'"
        ).fetchone()
        stored = self._loads(stored_row["value"], {}) if stored_row else {}
        live_cents = max(int(round(self._number(stored.get("cost"), 0) * 100)), 0)
        recovered_cents = min(requested_cents, live_cents)
        if recovered_cents <= 0:
            return {
                "recoveredAmount": 0.0,
                "remainingCost": live_cents / 100,
                "updatedRows": 0,
            }

        ordered_snapshot: list[tuple[str, int]] = []
        seen_ids: set[str] = set()
        for item in snapshot:
            item_id = str(item.get("id") or "").strip()
            if not item_id or item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            ordered_snapshot.append(
                (item_id, max(int(round(self._number(item.get("amount"), 0) * 100)), 0))
            )

        rows_by_id: dict[str, Any] = {}
        if ordered_snapshot:
            placeholders = ", ".join("?" for _ in ordered_snapshot)
            rows = conn.execute(
                f"SELECT id, amount FROM cost_additions WHERE id IN ({placeholders})",
                tuple(item_id for item_id, _ in ordered_snapshot),
            ).fetchall()
            rows_by_id = {str(row["id"]): row for row in rows}

        remaining_cents = recovered_cents
        updated_rows = 0
        for item_id, frozen_cents in ordered_snapshot:
            if remaining_cents <= 0:
                break
            row = rows_by_id.get(item_id)
            if not row:
                continue
            current_cents = max(int(round(self._number(row["amount"], 0) * 100)), 0)
            available_cents = min(current_cents, frozen_cents) if frozen_cents else current_cents
            consumed_cents = min(remaining_cents, available_cents)
            if consumed_cents <= 0:
                continue
            next_cents = current_cents - consumed_cents
            if next_cents <= 0:
                conn.execute("DELETE FROM cost_additions WHERE id = ?", (item_id,))
            else:
                conn.execute(
                    "UPDATE cost_additions SET amount = ? WHERE id = ?",
                    (next_cents / 100, item_id),
                )
            remaining_cents -= consumed_cents
            updated_rows += 1

        stored["cost"] = (live_cents - recovered_cents) / 100
        conn.execute(
            "INSERT INTO settings(key, value) VALUES('stored_state', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (self._dumps(stored),),
        )
        return {
            "recoveredAmount": recovered_cents / 100,
            "remainingCost": stored["cost"],
            "updatedRows": updated_rows,
        }
