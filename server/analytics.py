from __future__ import annotations

import math
import statistics
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

try:
    from chinese_calendar import is_workday as chinese_is_workday
except ImportError:  # The fallback keeps the server usable during a staged rollout.
    chinese_is_workday = None


SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_SAMPLE_GAP_SECONDS = 15 * 60
MIN_PARTIAL_HOURS = 2
MIN_COMPLETE_COVERAGE = 0.8
FORECAST_SAMPLE_LIMIT = 8
DEATH_ANALYSIS_DAYS = 7
AUTO_DELETION_DELAY = timedelta(hours=24)
AUTO_DELETION_TOLERANCE = timedelta(minutes=15)


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(SHANGHAI)


def day_type(day: date) -> tuple[str, bool]:
    if chinese_is_workday is not None:
        try:
            return ("workday" if chinese_is_workday(day) else "nonWorkday", False)
        except NotImplementedError:
            pass
    return ("workday" if day.weekday() < 5 else "nonWorkday", True)


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _window_used(row: dict[str, Any], suffix: str) -> float | None:
    capacity = _number(row.get(f"capacity{suffix}"))
    if capacity is None:
        capacity = _number(row.get("schedulable"))
    remaining = _number(row.get(f"remainingCapacity{suffix}"))
    if remaining is None:
        remaining = _number(row.get(f"remaining{suffix}"))
    if capacity is None or remaining is None:
        return None
    return max(capacity - remaining, 0.0)


def _round_metric(value: float | None) -> float | None:
    return None if value is None else round(max(value, 0.0), 1)


def aggregate_daily(rows: Iterable[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    local_now = (now or datetime.now(timezone.utc)).astimezone(SHANGHAI)
    prepared: list[tuple[datetime, dict[str, Any]]] = []
    for row in rows:
        try:
            prepared.append((parse_timestamp(str(row["date"])), row))
        except (KeyError, TypeError, ValueError):
            continue
    prepared.sort(key=lambda item: item[0])

    grouped: dict[date, list[tuple[datetime, dict[str, Any]]]] = {}
    for timestamp, row in prepared:
        grouped.setdefault(timestamp.date(), []).append((timestamp, row))

    daily: list[dict[str, Any]] = []
    for local_day, items in sorted(grouped.items()):
        consumed5h = 0.0
        consumed7d = 0.0
        valid5h = False
        valid7d = False
        covered_seconds = 0.0

        for (previous_time, previous), (current_time, current) in zip(items, items[1:]):
            gap = (current_time - previous_time).total_seconds()
            if gap <= 0 or gap > MAX_SAMPLE_GAP_SECONDS:
                continue
            covered_seconds += gap
            previous5h = _window_used(previous, "5h")
            current5h = _window_used(current, "5h")
            if previous5h is not None and current5h is not None:
                consumed5h += max(current5h - previous5h, 0.0)
                valid5h = True
            previous7d = _window_used(previous, "7d")
            current7d = _window_used(current, "7d")
            if previous7d is not None and current7d is not None:
                consumed7d += max(current7d - previous7d, 0.0)
                valid7d = True

        start_of_day = datetime.combine(local_day, time.min, tzinfo=SHANGHAI)
        is_complete = local_day < local_now.date()
        expected_seconds = 86400.0 if is_complete else max((local_now - start_of_day).total_seconds(), 1.0)
        coverage = min(covered_seconds / expected_seconds, 1.0)
        first_total = int(_number(items[0][1].get("total")) or 0)
        last_total = int(_number(items[-1][1].get("total")) or 0)
        net_change = last_total - first_total
        classification, fallback = day_type(local_day)
        daily.append(
            {
                "date": local_day.isoformat(),
                "dayType": classification,
                "calendarFallback": fallback,
                "isComplete": is_complete,
                "eligible": is_complete and coverage >= MIN_COMPLETE_COVERAGE and (valid5h or valid7d),
                "estimated5h": _round_metric(consumed5h) if valid5h else None,
                "estimated7d": _round_metric(consumed7d) if valid7d else None,
                "accountDecrease": max(-net_change, 0),
                "accountIncrease": max(net_change, 0),
                "netAccountChange": net_change,
                "sampleCount": len(items),
                "coverage": round(coverage, 3),
                "observedHours": round(covered_seconds / 3600.0, 2),
            }
        )
    return daily


def _empty_death_day(local_day: date, is_complete: bool) -> dict[str, Any]:
    classification, fallback = day_type(local_day)
    return {
        "date": local_day.isoformat(),
        "dayType": classification,
        "calendarFallback": fallback,
        "isComplete": is_complete,
        "estimated5h": None,
        "estimated7d": None,
        "newErrors": 0,
        "endingErrors": None,
        "inferredAccountRemovals": 0,
        "likelyErrorDeaths": 0,
        "autoDeletionCandidates": 0,
        "manualOrUnmatchedCandidates": 0,
        "otherRemovalCandidates": 0,
        "accountAdditions": 0,
        "sampleCount": 0,
        "coverage": 0.0,
        "observedHours": 0.0,
        "_coveredSeconds": 0.0,
    }


def _empty_death_hour(hour: int) -> dict[str, Any]:
    return {
        "hour": hour,
        "label": f"{hour:02d}:00-{(hour + 1) % 24:02d}:00",
        "newErrors": 0,
        "inferredAccountRemovals": 0,
        "likelyErrorDeaths": 0,
        "autoDeletionCandidates": 0,
        "manualOrUnmatchedCandidates": 0,
        "otherRemovalCandidates": 0,
        "accountAdditions": 0,
        "observedDays": 0,
        "observedHours": 0.0,
        "coverage": 0.0,
        "errorRatePercent": None,
        "removalRatePercent": None,
        "likelyErrorDeathRatePercent": None,
        "_coveredSeconds": 0.0,
        "_expectedSeconds": 0.0,
        "_exposureAccountHours": 0.0,
        "_observedDates": set(),
    }


def _empty_death_timeline_hour(local_day: date, hour: int) -> dict[str, Any]:
    return {
        "date": local_day.isoformat(),
        "hour": hour,
        "label": f"{hour:02d}:00-{(hour + 1) % 24:02d}:00",
        "newErrors": 0,
        "endingErrors": None,
        "inferredAccountRemovals": 0,
        "likelyErrorDeaths": 0,
        "autoDeletionCandidates": 0,
        "manualOrUnmatchedCandidates": 0,
        "otherRemovalCandidates": 0,
        "accountAdditions": 0,
        "sampleCount": 0,
        "observed": False,
        "observedMinutes": 0.0,
        "coverage": 0.0,
        "errorRatePercent": None,
        "removalRatePercent": None,
        "_coveredSeconds": 0.0,
        "_expectedSeconds": 0.0,
        "_exposureAccountHours": 0.0,
    }


def _allocate_interval_exposure(
    start: datetime,
    end: datetime,
    total: float,
    daily: dict[date, dict[str, Any]],
    hourly: list[dict[str, Any]],
    timeline: dict[tuple[date, int], dict[str, Any]],
) -> None:
    cursor = start
    while cursor < end:
        next_hour = (cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
        segment_end = min(end, next_hour)
        seconds = (segment_end - cursor).total_seconds()
        day_bucket = daily.get(cursor.date())
        if day_bucket is not None:
            day_bucket["_coveredSeconds"] += seconds
        hour_bucket = hourly[cursor.hour]
        hour_bucket["_coveredSeconds"] += seconds
        hour_bucket["_exposureAccountHours"] += max(total, 0.0) * seconds / 3600.0
        hour_bucket["_observedDates"].add(cursor.date())
        timeline_bucket = timeline.get((cursor.date(), cursor.hour))
        if timeline_bucket is not None:
            timeline_bucket["_coveredSeconds"] += seconds
            timeline_bucket["_exposureAccountHours"] += max(total, 0.0) * seconds / 3600.0
        cursor = segment_end


def _timing_confidence(observed_days: int) -> str:
    if observed_days >= 5:
        return "high"
    if observed_days >= 3:
        return "medium"
    if observed_days >= 2:
        return "low"
    return "insufficient"


def _upcoming_auto_deletions(
    error_events: list[dict[str, Any]], current_error: float, now: datetime
) -> tuple[list[dict[str, Any]], float]:
    start = now.replace(minute=0, second=0, microsecond=0)
    end = now + timedelta(hours=24)
    pending: list[tuple[datetime, float]] = []
    for event in error_events:
        due_at = event["time"] + AUTO_DELETION_DELAY
        if now <= due_at < end:
            pending.append((due_at, float(event["count"])))

    raw_total = sum(count for _, count in pending)
    scale = min(max(current_error, 0.0) / raw_total, 1.0) if raw_total > 0 else 0.0
    buckets: dict[datetime, float] = {}
    for due_at, count in pending:
        bucket_start = due_at.replace(minute=0, second=0, microsecond=0)
        buckets[bucket_start] = buckets.get(bucket_start, 0.0) + count * scale

    result: list[dict[str, Any]] = []
    bucket_count = math.ceil((end - start).total_seconds() / 3600.0)
    for offset in range(bucket_count):
        bucket_start = start + timedelta(hours=offset)
        count = buckets.get(bucket_start, 0.0)
        result.append(
            {
                "start": bucket_start.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "localHour": bucket_start.hour,
                "estimatedCount": round(count, 1),
            }
        )
    due_next_hour = sum(
        count * scale
        for due_at, count in pending
        if now <= due_at < now + timedelta(hours=1)
    )
    return result, round(due_next_hour, 1)


def _recent_error_window(
    prepared: list[tuple[datetime, dict[str, Any]]], now: datetime, minutes: int
) -> dict[str, Any]:
    start = now - timedelta(minutes=minutes)
    net_change = 0.0
    positive_steps = 0
    decrease_steps = 0
    covered_seconds = 0.0
    sample_times: set[datetime] = set()
    start_errors: float | None = None
    end_errors: float | None = None

    for (previous_time, previous), (current_time, current) in zip(prepared, prepared[1:]):
        if current_time < start or current_time > now:
            continue
        gap = (current_time - previous_time).total_seconds()
        if gap <= 0 or gap > MAX_SAMPLE_GAP_SECONDS:
            continue
        previous_error = _number(previous.get("error"))
        current_error = _number(current.get("error"))
        if previous_error is None or current_error is None:
            continue
        overlap_start = max(previous_time, start)
        covered_seconds += max((current_time - overlap_start).total_seconds(), 0.0)
        delta = current_error - previous_error
        net_change += delta
        positive_steps += int(delta > 0)
        decrease_steps += int(delta < 0)
        sample_times.update((previous_time, current_time))
        if start_errors is None:
            start_errors = previous_error
        end_errors = current_error

    observed_minutes = covered_seconds / 60.0
    if observed_minutes >= minutes * 0.8:
        confidence = "high"
    elif observed_minutes >= minutes * 0.5:
        confidence = "medium"
    elif observed_minutes >= min(15, minutes * 0.5):
        confidence = "low"
    else:
        confidence = "insufficient"
    return {
        "minutes": minutes,
        "startErrors": None if start_errors is None else int(round(start_errors)),
        "endErrors": None if end_errors is None else int(round(end_errors)),
        "netIncrease": int(round(net_change)),
        "positiveSteps": positive_steps,
        "decreaseSteps": decrease_steps,
        "isContinuouslyRising": positive_steps >= 2 and decrease_steps == 0 and net_change > 0,
        "sampleCount": len(sample_times),
        "observedMinutes": round(observed_minutes, 1),
        "confidence": confidence,
    }


def _recent_error_trend(
    prepared: list[tuple[datetime, dict[str, Any]]], now: datetime
) -> dict[str, Any]:
    window30 = _recent_error_window(prepared, now, 30)
    window60 = _recent_error_window(prepared, now, 60)
    current_errors = _number(prepared[-1][1].get("error")) if prepared else None
    continuous = window30["isContinuouslyRising"] or window60["isContinuouslyRising"]
    largest_increase = max(window30["netIncrease"], window60["netIncrease"])
    confidences = [window30["confidence"], window60["confidence"]]
    rank = {"insufficient": 0, "low": 1, "medium": 2, "high": 3}
    confidence = max(confidences, key=lambda item: rank[item])
    if confidence == "insufficient":
        signal_level = "insufficient"
    elif continuous and largest_increase >= 3:
        signal_level = "high"
    elif largest_increase > 0:
        signal_level = "medium"
    else:
        signal_level = "low"
    return {
        "currentErrors": None if current_errors is None else int(round(current_errors)),
        "signalLevel": signal_level,
        "isContinuouslyRising": continuous,
        "window30m": window30,
        "window60m": window60,
    }


def analyze_death_patterns(
    rows: Iterable[dict[str, Any]], now: datetime | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    local_now = (now or datetime.now(timezone.utc)).astimezone(SHANGHAI)
    window_start = datetime.combine(local_now.date() - timedelta(days=DEATH_ANALYSIS_DAYS - 1), time.min, tzinfo=SHANGHAI)
    matching_start = window_start - AUTO_DELETION_DELAY - AUTO_DELETION_TOLERANCE

    prepared: list[tuple[datetime, dict[str, Any]]] = []
    for row in rows:
        try:
            timestamp = parse_timestamp(str(row["date"]))
        except (KeyError, TypeError, ValueError):
            continue
        if matching_start - timedelta(minutes=MAX_SAMPLE_GAP_SECONDS // 60) <= timestamp <= local_now:
            prepared.append((timestamp, row))
    prepared.sort(key=lambda item: item[0])

    daily = {
        local_day: _empty_death_day(local_day, local_day < local_now.date())
        for local_day in (window_start.date() + timedelta(days=offset) for offset in range(DEATH_ANALYSIS_DAYS))
    }
    hourly = [_empty_death_hour(hour) for hour in range(24)]
    timeline = {
        (local_day, hour): _empty_death_timeline_hour(local_day, hour)
        for local_day in daily
        for hour in range(24)
    }
    for timestamp, row in prepared:
        if timestamp >= window_start:
            daily_bucket = daily[timestamp.date()]
            daily_bucket["sampleCount"] += 1
            timeline_bucket = timeline[(timestamp.date(), timestamp.hour)]
            timeline_bucket["sampleCount"] += 1
            ending_errors = _number(row.get("error"))
            if ending_errors is not None:
                rounded_errors = int(round(ending_errors))
                daily_bucket["endingErrors"] = rounded_errors
                timeline_bucket["endingErrors"] = rounded_errors

    error_events: list[dict[str, Any]] = []
    removal_events: list[dict[str, Any]] = []
    for (previous_time, previous), (current_time, current) in zip(prepared, prepared[1:]):
        gap = (current_time - previous_time).total_seconds()
        if gap <= 0 or gap > MAX_SAMPLE_GAP_SECONDS:
            continue

        previous_total = _number(previous.get("total"))
        current_total = _number(current.get("total"))
        previous_error = _number(previous.get("error"))
        current_error = _number(current.get("error"))
        if previous_error is not None and current_error is not None:
            error_increase = max(current_error - previous_error, 0.0)
            if error_increase > 0 and current_time >= matching_start:
                error_events.append({"time": current_time, "count": int(round(error_increase)), "remaining": int(round(error_increase))})

        overlap_start = max(previous_time, window_start)
        overlap_end = min(current_time, local_now)
        if overlap_start >= overlap_end:
            continue
        _allocate_interval_exposure(overlap_start, overlap_end, previous_total or 0.0, daily, hourly, timeline)

        event_day = daily.get(current_time.date())
        if event_day is None or current_time < window_start or current_time > local_now:
            continue
        event_hour = hourly[current_time.hour]
        event_timeline = timeline[(current_time.date(), current_time.hour)]
        total_decrease = 0
        total_increase = 0
        if previous_total is not None and current_total is not None:
            total_decrease = int(round(max(previous_total - current_total, 0.0)))
            total_increase = int(round(max(current_total - previous_total, 0.0)))
        error_increase = 0
        error_decrease = 0
        if previous_error is not None and current_error is not None:
            error_increase = int(round(max(current_error - previous_error, 0.0)))
            error_decrease = int(round(max(previous_error - current_error, 0.0)))
        likely_error_deaths = min(total_decrease, error_decrease)
        other_removals = max(total_decrease - likely_error_deaths, 0)

        values = {
            "newErrors": error_increase,
            "inferredAccountRemovals": total_decrease,
            "likelyErrorDeaths": likely_error_deaths,
            "otherRemovalCandidates": other_removals,
            "accountAdditions": total_increase,
        }
        for key, value in values.items():
            event_day[key] += value
            event_hour[key] += value
            event_timeline[key] += value
        if likely_error_deaths > 0:
            removal_events.append(
                {
                    "time": current_time,
                    "count": likely_error_deaths,
                    "day": event_day,
                    "hour": event_hour,
                    "timeline": event_timeline,
                }
            )

    for removal in removal_events:
        target = removal["time"] - AUTO_DELETION_DELAY
        remaining = int(removal["count"])
        matched = 0
        for event in error_events:
            if event["remaining"] <= 0 or abs((event["time"] - target).total_seconds()) > AUTO_DELETION_TOLERANCE.total_seconds():
                continue
            quantity = min(remaining, event["remaining"])
            matched += quantity
            remaining -= quantity
            event["remaining"] -= quantity
            if remaining <= 0:
                break
        removal["day"]["autoDeletionCandidates"] += matched
        removal["hour"]["autoDeletionCandidates"] += matched
        removal["timeline"]["autoDeletionCandidates"] += matched
        removal["day"]["manualOrUnmatchedCandidates"] += remaining
        removal["hour"]["manualOrUnmatchedCandidates"] += remaining
        removal["timeline"]["manualOrUnmatchedCandidates"] += remaining

    usage_by_date = {
        item["date"]: item
        for item in aggregate_daily((row for _, row in prepared), local_now)
        if item["date"] >= window_start.date().isoformat()
    }
    for local_day, bucket in daily.items():
        start_of_day = datetime.combine(local_day, time.min, tzinfo=SHANGHAI)
        end_of_day = min(start_of_day + timedelta(days=1), local_now)
        expected_seconds = max((end_of_day - start_of_day).total_seconds(), 1.0)
        bucket["coverage"] = round(min(bucket["_coveredSeconds"] / expected_seconds, 1.0), 3)
        bucket["observedHours"] = round(bucket["_coveredSeconds"] / 3600.0, 2)
        usage = usage_by_date.get(local_day.isoformat())
        if usage is not None:
            bucket["estimated5h"] = usage.get("estimated5h")
            bucket["estimated7d"] = usage.get("estimated7d")
        del bucket["_coveredSeconds"]

    for offset in range(DEATH_ANALYSIS_DAYS):
        local_day = window_start.date() + timedelta(days=offset)
        day_start = datetime.combine(local_day, time.min, tzinfo=SHANGHAI)
        for hour in range(24):
            hour_start = day_start + timedelta(hours=hour)
            hour_end = min(hour_start + timedelta(hours=1), local_now)
            if hour_end > hour_start:
                expected_seconds = (hour_end - hour_start).total_seconds()
                hourly[hour]["_expectedSeconds"] += expected_seconds
                timeline[(local_day, hour)]["_expectedSeconds"] = expected_seconds

    total_exposure_account_hours = sum(bucket["_exposureAccountHours"] for bucket in hourly)
    overall_error_rate = (
        sum(bucket["newErrors"] for bucket in hourly) / total_exposure_account_hours * 100
        if total_exposure_account_hours > 0
        else 0.0
    )
    overall_removal_rate = (
        sum(bucket["inferredAccountRemovals"] for bucket in hourly) / total_exposure_account_hours * 100
        if total_exposure_account_hours > 0
        else 0.0
    )

    for bucket in hourly:
        exposure = bucket["_exposureAccountHours"]
        bucket["observedDays"] = len(bucket["_observedDates"])
        bucket["observedHours"] = round(bucket["_coveredSeconds"] / 3600.0, 2)
        bucket["coverage"] = round(
            min(bucket["_coveredSeconds"] / bucket["_expectedSeconds"], 1.0)
            if bucket["_expectedSeconds"] > 0
            else 0.0,
            3,
        )
        if exposure > 0:
            bucket["errorRatePercent"] = round(bucket["newErrors"] / exposure * 100, 4)
            bucket["removalRatePercent"] = round(bucket["inferredAccountRemovals"] / exposure * 100, 4)
            bucket["likelyErrorDeathRatePercent"] = round(bucket["likelyErrorDeaths"] / exposure * 100, 4)
        del bucket["_coveredSeconds"]
        del bucket["_expectedSeconds"]
        del bucket["_exposureAccountHours"]
        del bucket["_observedDates"]

    for bucket in timeline.values():
        exposure = bucket["_exposureAccountHours"]
        bucket["observed"] = bucket["sampleCount"] > 0 or bucket["_coveredSeconds"] > 0
        bucket["observedMinutes"] = round(bucket["_coveredSeconds"] / 60.0, 1)
        bucket["coverage"] = round(
            min(bucket["_coveredSeconds"] / bucket["_expectedSeconds"], 1.0)
            if bucket["_expectedSeconds"] > 0
            else 0.0,
            3,
        )
        if exposure > 0:
            bucket["errorRatePercent"] = round(bucket["newErrors"] / exposure * 100, 4)
            bucket["removalRatePercent"] = round(bucket["inferredAccountRemovals"] / exposure * 100, 4)
        del bucket["_coveredSeconds"]
        del bucket["_expectedSeconds"]
        del bucket["_exposureAccountHours"]

    snapshot_rows = [(timestamp, row) for timestamp, row in prepared if timestamp >= window_start]
    current = snapshot_rows[-1][1] if snapshot_rows else {}
    current_error = _number(current.get("error")) or 0.0
    upcoming, due_next_hour = _upcoming_auto_deletions(error_events, current_error, local_now)
    recent_error_trend = _recent_error_trend(prepared, local_now)
    eligible_hours = [
        bucket
        for bucket in hourly
        if bucket["observedDays"] >= 2
        and bucket["observedHours"] >= 1
        and bucket["errorRatePercent"] is not None
        and bucket["removalRatePercent"] is not None
    ]
    scores = [max(float(bucket["errorRatePercent"]), float(bucket["removalRatePercent"])) for bucket in eligible_hours]
    overall_score = max(overall_error_rate, overall_removal_rate)
    high_cutoff = max(_percentile(scores, 0.8) if scores else 0.0, overall_score * 1.5, 0.01)

    current_bucket = hourly[local_now.hour]
    confidence = _timing_confidence(current_bucket["observedDays"])
    current_score = max(float(current_bucket["errorRatePercent"] or 0), float(current_bucket["removalRatePercent"] or 0))
    signal_count = current_bucket["newErrors"] + current_bucket["inferredAccountRemovals"]
    historical_high = current_score >= high_cutoff and (signal_count >= 2 or current_score >= 0.5)
    recent_high = recent_error_trend["signalLevel"] == "high"
    recent_rising = recent_error_trend["signalLevel"] == "medium"
    reasons: list[str] = []
    if historical_high or recent_high:
        timing_level = "high"
        action = "avoid"
        if historical_high:
            reasons.append(f"过去7天 {current_bucket['label']} 是错误或移除高发时段")
        if recent_high:
            reasons.append("最近30-60分钟错误总数正在连续上升")
    elif confidence == "insufficient" and recent_error_trend["signalLevel"] == "insufficient":
        timing_level = "insufficient"
        action = "insufficient"
        reasons.append("当前小时的有效历史不足2天，暂时无法判断补号时机风险")
    elif signal_count > 0 or recent_rising:
        timing_level = "medium"
        action = "caution"
        if signal_count > 0:
            reasons.append(f"过去7天 {current_bucket['label']} 出现过新增错误或账号移除")
        if recent_rising:
            reasons.append("最近30-60分钟错误总数有上升")
    else:
        timing_level = "low"
        action = "suitable"
        reasons.append(f"过去7天 {current_bucket['label']} 未见明显错误或移除高峰")

    if due_next_hour > 0:
        reasons.append(f"未来1小时约有 {round(due_next_hour, 1)} 个现有错误账号到达24小时删除时点")

    ranked = sorted(
        eligible_hours,
        key=lambda bucket: max(float(bucket["errorRatePercent"] or 0), float(bucket["removalRatePercent"] or 0)),
    )
    safe_ranked = [
        bucket
        for bucket in ranked
        if not (
            max(float(bucket["errorRatePercent"] or 0), float(bucket["removalRatePercent"] or 0)) >= high_cutoff
            and (
                bucket["newErrors"] + bucket["inferredAccountRemovals"] >= 2
                or max(float(bucket["errorRatePercent"] or 0), float(bucket["removalRatePercent"] or 0)) >= 0.5
            )
        )
    ]
    suggested_hours = [bucket["hour"] for bucket in safe_ranked[:3]]
    peak_hours = [
        bucket["hour"]
        for bucket in reversed(ranked)
        if bucket["newErrors"] > 0 or bucket["inferredAccountRemovals"] > 0
    ][:3]

    analysis = {
        "windowDays": DEATH_ANALYSIS_DAYS,
        "windowStart": window_start.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "windowEnd": local_now.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "timezone": "Asia/Shanghai",
        "snapshotCount": len(snapshot_rows),
        "firstSnapshotAt": snapshot_rows[0][0].astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z") if snapshot_rows else None,
        "lastSnapshotAt": snapshot_rows[-1][0].astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z") if snapshot_rows else None,
        "autoDeletionDelayHours": 24,
        "method": "aggregateSnapshotInference",
        "limitations": [
            "仅有汇总快照，无法识别具体账号",
            "同一采样间隔内新增与删除可能相互抵消，移除数量是净下降下界",
            "人工删除与24小时自动删除仅按错误下降和时间相关性推断",
            "未来24小时自动删除量按过去24小时错误新增分布估算，并受当前错误总数上限约束",
        ],
        "daily": list(daily.values()),
        "hourly": hourly,
        "timeline": list(timeline.values()),
        "recentErrorTrend": recent_error_trend,
        "upcoming24hAutoDeletions": upcoming,
        "peakHours": peak_hours,
        "suggestedReplenishmentHours": suggested_hours,
    }
    timing_risk = {
        "level": timing_level,
        "action": action,
        "evaluatedHour": current_bucket["hour"],
        "hourLabel": current_bucket["label"],
        "confidence": confidence,
        "newErrors": current_bucket["newErrors"],
        "inferredAccountRemovals": current_bucket["inferredAccountRemovals"],
        "errorRatePercent": current_bucket["errorRatePercent"],
        "removalRatePercent": current_bucket["removalRatePercent"],
        "autoDeletionCandidates": current_bucket["autoDeletionCandidates"],
        "dueNextHour": round(due_next_hour, 1),
        "recentErrorSignalLevel": recent_error_trend["signalLevel"],
        "reasons": reasons,
        "peakHours": peak_hours,
        "suggestedHours": suggested_hours,
    }
    return analysis, timing_risk


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(math.ceil(percentile * len(ordered)) - 1, len(ordered) - 1))
    return ordered[index]


def _confidence(sample_count: int) -> str:
    if sample_count >= 6:
        return "high"
    if sample_count >= 3:
        return "medium"
    if sample_count >= 1:
        return "low"
    return "insufficient"


def forecast_metric(
    daily: list[dict[str, Any]],
    target_day: date,
    metric: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    target_type, calendar_fallback = day_type(target_day)
    candidates = [
        row
        for row in daily
        if row.get("eligible") and row.get("dayType") == target_type and _number(row.get(metric)) is not None
    ][-FORECAST_SAMPLE_LIMIT:]
    values = [float(row[metric]) for row in candidates]
    source = "history"

    if values:
        weights = list(range(1, len(values) + 1))
        baseline = sum(value * weight for value, weight in zip(values, weights)) / sum(weights)
        differences = [current - previous for previous, current in zip(values, values[1:])]
        raw_trend = statistics.median(differences) if differences else 0.0
        trend = max(min(raw_trend, baseline * 0.25), -baseline * 0.25)
        estimate = max(baseline + trend, 0.0)
        if len(values) >= 3:
            errors = [abs(value - baseline) for value in values]
            buffer = max(_percentile(errors, 0.8), estimate * 0.1)
        else:
            buffer = max(estimate * 0.25, 1.0 if estimate > 0 else 0.0)
        trend_percent = 0.0 if baseline <= 0 else trend / baseline
    else:
        local_now = (now or datetime.now(timezone.utc)).astimezone(SHANGHAI)
        current = next((row for row in reversed(daily) if row.get("date") == local_now.date().isoformat()), None)
        value = _number(current.get(metric)) if current else None
        elapsed_fraction = max(
            (local_now - datetime.combine(local_now.date(), time.min, tzinfo=SHANGHAI)).total_seconds() / 86400.0,
            1 / 24,
        )
        if (
            current
            and current.get("dayType") == target_type
            and float(current.get("observedHours") or 0) >= MIN_PARTIAL_HOURS
            and value is not None
        ):
            estimate = max(value / elapsed_fraction, 0.0)
            buffer = max(estimate * 0.35, 1.0 if estimate > 0 else 0.0)
            source = "partial"
            trend_percent = 0.0
        else:
            return {
                "value": None,
                "upper": None,
                "sampleCount": 0,
                "confidence": "insufficient",
                "source": "insufficient",
                "trendPercent": None,
                "calendarFallback": calendar_fallback,
            }

    return {
        "value": round(estimate, 1),
        "upper": round(estimate + buffer, 1),
        "sampleCount": len(values),
        "confidence": _confidence(len(values)) if values else "low",
        "source": source,
        "trendPercent": round(trend_percent, 3),
        "calendarFallback": calendar_fallback,
    }


def _forecast_for_date(
    daily: list[dict[str, Any]], target_day: date, now: datetime
) -> dict[str, Any]:
    classification, calendar_fallback = day_type(target_day)
    metrics = {
        "estimated5h": forecast_metric(daily, target_day, "estimated5h", now),
        "estimated7d": forecast_metric(daily, target_day, "estimated7d", now),
        "accountDecrease": forecast_metric(daily, target_day, "accountDecrease", now),
    }
    confidences = [item["confidence"] for item in metrics.values()]
    confidence = min(confidences, key=lambda item: {"insufficient": 0, "low": 1, "medium": 2, "high": 3}[item])
    return {
        "date": target_day.isoformat(),
        "dayType": classification,
        "calendarFallback": calendar_fallback or any(item["calendarFallback"] for item in metrics.values()),
        "estimated5h": metrics["estimated5h"]["value"],
        "upper5h": metrics["estimated5h"]["upper"],
        "estimated7d": metrics["estimated7d"]["value"],
        "upper7d": metrics["estimated7d"]["upper"],
        "accountDecrease": metrics["accountDecrease"]["value"],
        "upperAccountDecrease": metrics["accountDecrease"]["upper"],
        "confidence": confidence,
        "sampleCount": min(item["sampleCount"] for item in metrics.values()),
        "trend5hPercent": metrics["estimated5h"]["trendPercent"],
        "trend7dPercent": metrics["estimated7d"]["trendPercent"],
    }


def _next_day_of_type(start: date, desired: str) -> tuple[date, bool]:
    fallback = False
    current = start
    for _ in range(370):
        classification, used_fallback = day_type(current)
        fallback = fallback or used_fallback
        if classification == desired:
            return current, fallback
        current += timedelta(days=1)
    return start, True


def _window_forecast(
    daily: list[dict[str, Any]], now: datetime, metric: str, hours: float
) -> tuple[float | None, float | None, str]:
    cursor = now
    remaining_hours = hours
    estimate = 0.0
    upper = 0.0
    confidence = "high"
    rank = {"insufficient": 0, "low": 1, "medium": 2, "high": 3}
    while remaining_hours > 0:
        end_of_day = datetime.combine(cursor.date() + timedelta(days=1), time.min, tzinfo=SHANGHAI)
        segment_hours = min(remaining_hours, (end_of_day - cursor).total_seconds() / 3600.0)
        forecast = forecast_metric(daily, cursor.date(), metric, now)
        if forecast["value"] is None or forecast["upper"] is None:
            return None, None, "insufficient"
        fraction = segment_hours / 24.0
        estimate += float(forecast["value"]) * fraction
        upper += float(forecast["upper"]) * fraction
        if rank[forecast["confidence"]] < rank[confidence]:
            confidence = forecast["confidence"]
        cursor = end_of_day
        remaining_hours -= segment_hours
    return round(estimate, 1), round(upper, 1), confidence


def build_analytics(
    group_name: str,
    rows: list[dict[str, Any]],
    days_requested: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    local_now = (now or datetime.now(timezone.utc)).astimezone(SHANGHAI)
    daily_all = aggregate_daily(rows, local_now)
    daily = daily_all[-days_requested:]
    tomorrow_date = local_now.date() + timedelta(days=1)
    next_workday, workday_fallback = _next_day_of_type(tomorrow_date, "workday")
    next_non_workday, non_workday_fallback = _next_day_of_type(tomorrow_date, "nonWorkday")

    next_three_days = [
        _forecast_for_date(daily_all, tomorrow_date + timedelta(days=offset), local_now)
        for offset in range(3)
    ]
    tomorrow = next_three_days[0]
    workday = _forecast_for_date(daily_all, next_workday, local_now)
    non_workday = _forecast_for_date(daily_all, next_non_workday, local_now)
    rolling5h, rolling5h_upper, confidence5h = _window_forecast(daily_all, local_now, "estimated5h", 5)
    rolling7d, rolling7d_upper, confidence7d = _window_forecast(daily_all, local_now, "estimated7d", 24)
    rolling_loss, rolling_loss_upper, confidence_loss = _window_forecast(daily_all, local_now, "accountDecrease", 24)
    rank = {"insufficient": 0, "low": 1, "medium": 2, "high": 3}
    rolling_confidence = min([confidence5h, confidence7d, confidence_loss], key=lambda item: rank[item])
    death_analysis, replenishment_timing_risk = analyze_death_patterns(rows, local_now)

    current = rows[-1] if rows else None
    remaining5h = _number(current.get("remainingCapacity5h")) if current else None
    if remaining5h is None and current:
        remaining5h = _number(current.get("remaining5h"))
    remaining7d = _number(current.get("remainingCapacity7d")) if current else None
    if remaining7d is None and current:
        remaining7d = _number(current.get("remaining7d"))

    gap5h = None if rolling5h_upper is None or remaining5h is None else max(rolling5h_upper - remaining5h, 0.0)
    gap7d = None if rolling7d_upper is None or remaining7d is None else max(rolling7d_upper - remaining7d, 0.0)
    account_gap = rolling_loss_upper
    available_gaps = [value for value in [gap5h, gap7d, account_gap] if value is not None]
    replenish = math.ceil(max(available_gaps)) if len(available_gaps) == 3 else None

    reasons: list[str] = []
    high = False
    medium = False
    coverage5h = None if rolling5h_upper in (None, 0) or remaining5h is None else remaining5h / rolling5h_upper
    coverage7d = None if rolling7d_upper in (None, 0) or remaining7d is None else remaining7d / rolling7d_upper
    total = _number(current.get("total")) if current else None
    active = _number(current.get("active")) if current else None
    schedulable = _number(current.get("schedulable")) if current else None
    error = _number(current.get("error")) if current else None
    limited = _number(current.get("limited")) if current else None
    protected = _number(current.get("quotaProtected")) if current else None
    disabled = _number(current.get("disabled")) if current else None
    schedulable_rate = None if not active else (schedulable or 0) / active
    error_rate = None if not total else (error or 0) / total
    unhealthy_rate = None if not total else ((limited or 0) + (protected or 0) + (error or 0) + (disabled or 0)) / total

    for label, coverage in [("5h", coverage5h), ("7d", coverage7d)]:
        if coverage is not None and coverage < 1:
            high = True
            reasons.append(f"{label}额度不足以覆盖预测需求")
        elif coverage is not None and coverage < 1.5:
            medium = True
            reasons.append(f"{label}额度缓冲低于1.5倍")
    if schedulable_rate is not None and schedulable_rate < 0.1:
        high = True
        reasons.append("可调度率低于10%")
    elif schedulable_rate is not None and schedulable_rate < 0.3:
        medium = True
        reasons.append("可调度率低于30%")
    if error_rate is not None and error_rate >= 0.25:
        high = True
        reasons.append("错误账号占比达到25%")
    if unhealthy_rate is not None and unhealthy_rate >= 0.45:
        medium = True
        reasons.append("异常状态账号占比达到45%")
    trend_values = [tomorrow.get("trend5hPercent"), tomorrow.get("trend7dPercent")]
    if any(value is not None and value >= 0.25 for value in trend_values):
        medium = True
        reasons.append("预计消耗趋势增长达到25%")
    if rolling_confidence == "low":
        medium = True
        reasons.append("预测样本较少")

    if high:
        risk_level = "high"
    elif medium:
        risk_level = "medium"
    elif replenish is None:
        risk_level = "insufficient"
        reasons.append("有效历史不足2小时")
    else:
        risk_level = "low"
        reasons.append("当前容量可覆盖预测需求")

    calendar_fallback = (
        workday_fallback
        or non_workday_fallback
        or any(row.get("calendarFallback") for row in daily)
        or tomorrow.get("calendarFallback", False)
    )
    return {
        "groupName": group_name,
        "timezone": "Asia/Shanghai",
        "generatedAt": local_now.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "calendarFallback": calendar_fallback,
        "current": current,
        "daily": daily,
        "forecasts": {
            "tomorrow": tomorrow,
            "nextThreeDays": next_three_days,
            "nextWorkday": workday,
            "nextNonWorkday": non_workday,
            "rolling24h": {
                "estimated5h": rolling5h,
                "upper5h": rolling5h_upper,
                "estimated7d": rolling7d,
                "upper7d": rolling7d_upper,
                "accountDecrease": rolling_loss,
                "upperAccountDecrease": rolling_loss_upper,
                "confidence": rolling_confidence,
            },
        },
        "recommendation": {
            "replenish": replenish,
            "horizonHours": 24,
            "gap5h": _round_metric(gap5h),
            "gap7d": _round_metric(gap7d),
            "accountGap": _round_metric(account_gap),
        },
        "risk": {"level": risk_level, "reasons": list(dict.fromkeys(reasons))},
        "replenishmentTimingRisk": replenishment_timing_risk,
        "deathAnalysis": death_analysis,
        "dataCoverage": {
            "daysRequested": days_requested,
            "completeDays": sum(1 for row in daily if row["isComplete"]),
            "eligibleDays": sum(1 for row in daily if row["eligible"]),
            "firstDate": daily[0]["date"] if daily else None,
            "lastDate": daily[-1]["date"] if daily else None,
        },
    }
