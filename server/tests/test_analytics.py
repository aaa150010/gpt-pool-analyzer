import unittest
from datetime import datetime, timedelta, timezone

from server import analytics


def snapshot(
    timestamp: datetime,
    *,
    total: int = 100,
    active: int = 100,
    schedulable: int = 100,
    capacity5h: int = 100,
    capacity7d: int = 100,
    remaining5h: float = 100,
    remaining7d: float = 100,
    error: int = 0,
) -> dict:
    return {
        "date": timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "groupName": "PLUS共享号池",
        "status": "active",
        "total": total,
        "active": active,
        "schedulable": schedulable,
        "capacity5h": capacity5h,
        "capacity7d": capacity7d,
        "remainingCapacity5h": remaining5h,
        "remainingCapacity7d": remaining7d,
        "remaining5h": round(remaining5h),
        "remaining7d": round(remaining7d),
        "limited": 0,
        "quotaProtected": 0,
        "error": error,
        "disabled": 0,
        "concurrentAvailable": schedulable,
        "concurrentTotal": schedulable,
    }


class DailyAggregationTests(unittest.TestCase):
    def test_capacity_increase_does_not_look_like_consumption(self) -> None:
        start = datetime(2026, 7, 28, 1, 0, tzinfo=timezone.utc)
        rows = [
            snapshot(start, capacity5h=100, remaining5h=90, capacity7d=100, remaining7d=80),
            snapshot(start + timedelta(minutes=5), capacity5h=120, remaining5h=110, capacity7d=120, remaining7d=100),
            snapshot(start + timedelta(minutes=10), capacity5h=120, remaining5h=105, capacity7d=120, remaining7d=96),
        ]
        result = analytics.aggregate_daily(rows, datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(result[0]["estimated5h"], 5.0)
        self.assertEqual(result[0]["estimated7d"], 4.0)

    def test_long_gap_is_not_counted(self) -> None:
        start = datetime(2026, 7, 27, 0, 0, tzinfo=timezone.utc)
        rows = [
            snapshot(start, remaining5h=100),
            snapshot(start + timedelta(minutes=5), remaining5h=95),
            snapshot(start + timedelta(minutes=30), remaining5h=50),
        ]
        result = analytics.aggregate_daily(rows, datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(result[0]["estimated5h"], 5.0)
        self.assertLess(result[0]["coverage"], 0.01)
        self.assertFalse(result[0]["eligible"])

    def test_midnight_creates_independent_days(self) -> None:
        rows = [
            snapshot(datetime(2026, 7, 27, 15, 55, tzinfo=timezone.utc), remaining5h=100),
            snapshot(datetime(2026, 7, 27, 16, 0, tzinfo=timezone.utc), remaining5h=90),
            snapshot(datetime(2026, 7, 27, 16, 5, tzinfo=timezone.utc), remaining5h=85),
        ]
        result = analytics.aggregate_daily(rows, datetime(2026, 7, 28, 20, 0, tzinfo=timezone.utc))
        self.assertEqual([row["date"] for row in result], ["2026-07-27", "2026-07-28"])
        self.assertEqual(result[0]["estimated5h"], None)
        self.assertEqual(result[1]["estimated5h"], 5.0)


class ForecastTests(unittest.TestCase):
    def daily_rows(self, count: int) -> list[dict]:
        start = datetime(2026, 7, 1, tzinfo=timezone.utc).date()
        rows = []
        day = start
        while len(rows) < count:
            kind, _ = analytics.day_type(day)
            if kind == "workday":
                value = 40 + len(rows) * 2
                rows.append(
                    {
                        "date": day.isoformat(),
                        "dayType": kind,
                        "eligible": True,
                        "isComplete": True,
                        "estimated5h": value,
                        "estimated7d": value * 2,
                        "accountDecrease": 3,
                        "observedHours": 24,
                    }
                )
            day += timedelta(days=1)
        return rows

    def test_confidence_levels_and_nonnegative_forecast(self) -> None:
        target = datetime(2026, 7, 29, tzinfo=timezone.utc).date()
        low = analytics.forecast_metric(self.daily_rows(2), target, "estimated5h")
        medium = analytics.forecast_metric(self.daily_rows(4), target, "estimated5h")
        high = analytics.forecast_metric(self.daily_rows(7), target, "estimated5h")
        self.assertEqual(low["confidence"], "low")
        self.assertEqual(medium["confidence"], "medium")
        self.assertEqual(high["confidence"], "high")
        self.assertGreaterEqual(high["value"], 0)
        self.assertGreaterEqual(high["upper"], high["value"])

    def test_next_three_days_are_consecutive(self) -> None:
        now = datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc)
        rows = [
            snapshot(now - timedelta(hours=2)),
            snapshot(now - timedelta(hours=1), remaining5h=95, remaining7d=90),
            snapshot(now, remaining5h=90, remaining7d=80),
        ]
        result = analytics.build_analytics("PLUS共享号池", rows, 30, now)
        forecasts = result["forecasts"]["nextThreeDays"]
        self.assertEqual(len(forecasts), 3)
        self.assertEqual(
            [item["date"] for item in forecasts],
            ["2026-07-29", "2026-07-30", "2026-07-31"],
        )

    @unittest.skipIf(analytics.chinese_is_workday is None, "chinesecalendar is not installed")
    def test_chinese_holiday_and_makeup_workday(self) -> None:
        self.assertEqual(analytics.day_type(datetime(2026, 2, 14).date())[0], "workday")
        self.assertEqual(analytics.day_type(datetime(2026, 2, 17).date())[0], "nonWorkday")


class RecommendationTests(unittest.TestCase):
    def test_health_risk_changes_with_current_pool(self) -> None:
        now = datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc)
        rows = []
        start = now - timedelta(hours=3)
        for index in range(37):
            rows.append(
                snapshot(
                    start + timedelta(minutes=5 * index),
                    total=100,
                    active=100,
                    schedulable=5,
                    capacity5h=100,
                    capacity7d=100,
                    remaining5h=80 - index * 0.2,
                    remaining7d=80 - index * 0.3,
                    error=30,
                )
            )
        result = analytics.build_analytics("PLUS共享号池", rows, 30, now)
        self.assertEqual(result["risk"]["level"], "high")
        self.assertTrue(any("可调度率" in reason for reason in result["risk"]["reasons"]))
        self.assertTrue(any("错误账号" in reason for reason in result["risk"]["reasons"]))


class DeathPatternTests(unittest.TestCase):
    def test_current_hour_error_increase_is_available_before_hour_end(self) -> None:
        local = analytics.SHANGHAI
        now = datetime(2026, 7, 29, 10, 30, tzinfo=local)
        rows = [
            snapshot(datetime(2026, 7, 28, 10, 0, tzinfo=local), total=100, error=0),
            snapshot(datetime(2026, 7, 28, 10, 5, tzinfo=local), total=100, error=10),
            snapshot(datetime(2026, 7, 29, 10, 0, tzinfo=local), total=100, error=0),
            snapshot(datetime(2026, 7, 29, 10, 5, tzinfo=local), total=100, error=20),
        ]

        analysis, risk = analytics.analyze_death_patterns(rows, now)
        current_hour = next(
            item
            for item in analysis["timeline"]
            if item["date"] == "2026-07-29" and item["hour"] == 10
        )
        historical_hour = next(
            item
            for item in analysis["timeline"]
            if item["date"] == "2026-07-28" and item["hour"] == 10
        )

        self.assertTrue(current_hour["isCurrentHour"])
        self.assertFalse(current_hour["isComplete"])
        self.assertFalse(historical_hour["isCurrentHour"])
        self.assertTrue(historical_hour["isComplete"])
        self.assertEqual(current_hour["lastSnapshotAt"], "2026-07-29T02:05:00Z")
        self.assertEqual(current_hour["newErrors"], 20)
        self.assertEqual(current_hour["sampleCount"], 2)
        self.assertEqual(current_hour["observedMinutes"], 5.0)
        self.assertEqual(risk["newErrors"], 30)
        self.assertEqual(risk["currentHourNewErrors"], 20)
        self.assertEqual(risk["currentHourRemovals"], 0)
        self.assertEqual(risk["currentHourSampleCount"], 2)
        self.assertEqual(risk["currentHourObservedMinutes"], 5.0)
        self.assertEqual(risk["currentHourLastSnapshotAt"], "2026-07-29T02:05:00Z")

    def test_current_hour_removal_is_available_before_hour_end(self) -> None:
        local = analytics.SHANGHAI
        now = datetime(2026, 7, 29, 10, 30, tzinfo=local)
        rows = [
            snapshot(datetime(2026, 7, 28, 10, 0, tzinfo=local), total=100, error=10),
            snapshot(datetime(2026, 7, 28, 10, 5, tzinfo=local), total=90, error=0),
            snapshot(datetime(2026, 7, 29, 10, 0, tzinfo=local), total=100, error=20),
            snapshot(datetime(2026, 7, 29, 10, 5, tzinfo=local), total=80, error=0),
            snapshot(datetime(2026, 7, 29, 10, 10, tzinfo=local), total=80, error=0),
        ]

        analysis, risk = analytics.analyze_death_patterns(rows, now)
        current_hour = next(
            item
            for item in analysis["timeline"]
            if item["date"] == "2026-07-29" and item["hour"] == 10
        )

        self.assertEqual(current_hour["inferredAccountRemovals"], 20)
        self.assertEqual(current_hour["likelyErrorDeaths"], 20)
        self.assertEqual(risk["inferredAccountRemovals"], 30)
        self.assertEqual(risk["currentHourNewErrors"], 0)
        self.assertEqual(risk["currentHourRemovals"], 20)
        self.assertEqual(risk["currentHourLikelyErrorDeaths"], 20)
        self.assertEqual(risk["level"], "high")
        self.assertEqual(risk["action"], "avoid")
        self.assertTrue(any("本小时截至 10:10 已删除 20 个账号" in reason for reason in risk["reasons"]))

    def test_cross_day_auto_deletion_candidate_is_matched_after_24_hours(self) -> None:
        local = analytics.SHANGHAI
        rows = [
            snapshot(datetime(2026, 7, 27, 23, 50, tzinfo=local), total=100, error=0),
            snapshot(datetime(2026, 7, 27, 23, 55, tzinfo=local), total=100, error=5),
            snapshot(datetime(2026, 7, 28, 23, 50, tzinfo=local), total=100, error=5),
            snapshot(datetime(2026, 7, 28, 23, 55, tzinfo=local), total=95, error=0),
        ]
        analysis, _ = analytics.analyze_death_patterns(
            rows, datetime(2026, 7, 29, 1, 0, tzinfo=local)
        )
        july_28 = next(item for item in analysis["daily"] if item["date"] == "2026-07-28")
        hour_23 = next(
            item
            for item in analysis["timeline"]
            if item["date"] == "2026-07-28" and item["hour"] == 23
        )
        self.assertEqual(analysis["windowDays"], 7)
        self.assertEqual(len(analysis["timeline"]), 7 * 24)
        self.assertEqual(july_28["inferredAccountRemovals"], 5)
        self.assertEqual(july_28["likelyErrorDeaths"], 5)
        self.assertEqual(july_28["autoDeletionCandidates"], 5)
        self.assertEqual(july_28["manualOrUnmatchedCandidates"], 0)
        self.assertEqual(hour_23["endingErrors"], 0)
        self.assertEqual(hour_23["autoDeletionCandidates"], 5)

    def test_long_gap_does_not_create_error_or_removal_event(self) -> None:
        local = analytics.SHANGHAI
        rows = [
            snapshot(datetime(2026, 7, 29, 2, 0, tzinfo=local), total=100, error=0),
            snapshot(datetime(2026, 7, 29, 2, 30, tzinfo=local), total=80, error=20),
        ]
        analysis, risk = analytics.analyze_death_patterns(
            rows, datetime(2026, 7, 29, 2, 35, tzinfo=local)
        )
        current_day = analysis["daily"][-1]
        self.assertEqual(current_day["newErrors"], 0)
        self.assertEqual(current_day["inferredAccountRemovals"], 0)
        self.assertEqual(current_day["observedHours"], 0.0)
        self.assertEqual(risk["level"], "insufficient")

    def test_historical_peak_at_current_hour_is_high_replenishment_risk(self) -> None:
        local = analytics.SHANGHAI
        now = datetime(2026, 7, 29, 2, 30, tzinfo=local)
        rows = []
        for days_ago in range(7):
            local_day = (now - timedelta(days=days_ago)).date()
            base = datetime.combine(local_day, datetime.min.time(), tzinfo=local) + timedelta(hours=2)
            rows.extend(
                [
                    snapshot(base - timedelta(minutes=10), total=100, error=0),
                    snapshot(base - timedelta(minutes=5), total=100, error=0),
                    snapshot(base, total=100, error=0),
                    snapshot(base + timedelta(minutes=5), total=90, error=10),
                    snapshot(base + timedelta(minutes=10), total=90, error=10),
                ]
            )
        rows.sort(key=lambda item: item["date"])
        analysis, risk = analytics.analyze_death_patterns(rows, now)
        hour_2 = analysis["hourly"][2]
        self.assertEqual(risk["level"], "high")
        self.assertEqual(risk["action"], "avoid")
        self.assertIn(2, analysis["peakHours"])
        self.assertEqual(hour_2["observedDays"], 7)
        self.assertGreater(hour_2["errorRatePercent"], 0)

    def test_recent_continuous_error_growth_is_a_high_risk_signal(self) -> None:
        local = analytics.SHANGHAI
        now = datetime(2026, 7, 29, 2, 30, tzinfo=local)
        errors = [0, 1, 1, 2, 3, 3, 4]
        rows = [
            snapshot(now - timedelta(minutes=30 - index * 5), total=100, error=error)
            for index, error in enumerate(errors)
        ]
        analysis, risk = analytics.analyze_death_patterns(rows, now)
        trend = analysis["recentErrorTrend"]
        self.assertTrue(trend["window30m"]["isContinuouslyRising"])
        self.assertEqual(trend["window30m"]["netIncrease"], 4)
        self.assertEqual(trend["signalLevel"], "high")
        self.assertEqual(risk["level"], "high")
        self.assertTrue(any("连续上升" in reason for reason in risk["reasons"]))


if __name__ == "__main__":
    unittest.main()
