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

    def delete(self, item_id: str) -> float | None:
        normalized_id = str(item_id or "").strip()
        if not normalized_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT amount FROM cost_additions WHERE id = ?",
                (normalized_id,),
            ).fetchone()
            if not row:
                return None
            amount = self._number(row["amount"], 0)
            conn.execute("DELETE FROM cost_additions WHERE id = ?", (normalized_id,))
        return amount

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

    def recover_snapshot(
        self,
        conn: Any,
        snapshot: list[dict[str, Any]],
        amount: float,
        frozen_cost: float,
    ) -> dict[str, Any]:
        """Apply one successful withdrawal to the live cost ledger.

        Frozen rows are consumed oldest-first. Only the untracked portion that
        already existed in the frozen total is treated as a legacy cost, so
        rows and totals added after task creation are never consumed.
        """
        requested_cents = max(int(round(self._number(amount, 0) * 100)), 0)
        stored_row = conn.execute(
            "SELECT value FROM settings WHERE key = 'stored_state'"
        ).fetchone()
        stored = self._loads(stored_row["value"], {}) if stored_row else {}
        live_cents = max(int(round(self._number(stored.get("cost"), 0) * 100)), 0)
        recovery_budget_cents = min(requested_cents, live_cents)
        if recovery_budget_cents <= 0:
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
        frozen_rows_cents = sum(amount_cents for _, amount_cents in ordered_snapshot)
        frozen_cost_cents = max(int(round(self._number(frozen_cost, 0) * 100)), 0)
        frozen_legacy_cents = max(frozen_cost_cents - frozen_rows_cents, 0)

        rows_by_id: dict[str, Any] = {}
        if ordered_snapshot:
            placeholders = ", ".join("?" for _ in ordered_snapshot)
            rows = conn.execute(
                f"SELECT id, amount FROM cost_additions WHERE id IN ({placeholders})",
                tuple(item_id for item_id, _ in ordered_snapshot),
            ).fetchall()
            rows_by_id = {str(row["id"]): row for row in rows}

        recorded_row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM cost_additions"
        ).fetchone()
        recorded_cents = max(int(round(self._number(recorded_row["total"] if recorded_row else 0, 0) * 100)), 0)
        live_legacy_cents = max(live_cents - recorded_cents, 0)

        remaining_cents = recovery_budget_cents
        row_recovered_cents = 0
        updated_rows = 0
        for item_id, frozen_cents in ordered_snapshot:
            if remaining_cents <= 0:
                break
            row = rows_by_id.get(item_id)
            if not row:
                continue
            current_cents = max(int(round(self._number(row["amount"], 0) * 100)), 0)
            available_cents = min(current_cents, frozen_cents)
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
            row_recovered_cents += consumed_cents
            updated_rows += 1

        legacy_recovered_cents = min(remaining_cents, frozen_legacy_cents, live_legacy_cents)
        recovered_cents = row_recovered_cents + legacy_recovered_cents
        if recovered_cents <= 0:
            return {
                "recoveredAmount": 0.0,
                "remainingCost": live_cents / 100,
                "updatedRows": 0,
            }

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
