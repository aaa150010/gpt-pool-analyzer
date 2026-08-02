from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class WithdrawalAccount:
    email: str
    owner: str
    payment_method: str
    owner_label: str


WITHDRAWAL_ACCOUNTS = (
    WithdrawalAccount("1745627971@qq.com", "owner", "wechat", "自己微信"),
    WithdrawalAccount("2108462529@qq.com", "owner", "alipay", "自己支付宝"),
    WithdrawalAccount("2328406178@qq.com", "owner", "wechat", "老弟微信"),
    WithdrawalAccount("3595633153@qq.com", "owner", "alipay", "老弟支付宝"),
    WithdrawalAccount("3976534719@qq.com", "owner", "alipay", "老弟支付宝"),
    WithdrawalAccount("252715669@qq.com", "partner", "wechat", "社会哥微信"),
    WithdrawalAccount("2209245787@qq.com", "partner", "alipay", "社会哥支付宝"),
)
ACCOUNT_BY_EMAIL = {item.email: item for item in WITHDRAWAL_ACCOUNTS}
NOTIFICATION_RECIPIENTS = ("1745627971@qq.com", "252715669@qq.com")
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _money(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def _integer_balance(value: Any) -> int:
    return max(int(math.floor(_money(value) + 1e-9)), 0)


def _display_time(value: Any) -> str:
    if not value:
        return "-"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return str(value)


def _allocate_cost(accounts: list[dict[str, Any]], target: int) -> list[int]:
    amounts = [0] * len(accounts)
    remaining = max(int(target), 0)
    for index, account in enumerate(accounts):
        capacity = _integer_balance(account.get("balance"))
        amount = min((capacity // 5) * 5, (remaining // 5) * 5)
        amounts[index] = amount
        remaining -= amount
        if remaining == 0:
            break
    if remaining:
        for index, account in enumerate(accounts):
            capacity = _integer_balance(account.get("balance"))
            available = capacity - amounts[index]
            amount = min(available, remaining)
            amounts[index] += amount
            remaining -= amount
            if remaining == 0:
                break
    return amounts


def plan_withdrawal(
    mode: str,
    cost: float,
    balances: list[dict[str, Any]],
    requested_amount: float | None = None,
) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    by_email = {str(item.get("email") or item.get("name") or "").strip().lower(): item for item in balances}
    for account in WITHDRAWAL_ACCOUNTS:
        source = by_email.get(account.email.lower(), {})
        normalized.append({"email": account.email, "balance": _money(source.get("balance", source.get("amount")))})

    total_cost = round(max(_money(cost), 0.0), 2)
    if mode == "cost":
        if requested_amount is None:
            target = max(int(math.ceil(total_cost - 1e-9)), 0)
        else:
            try:
                requested_number = float(requested_amount)
            except (TypeError, ValueError) as exc:
                raise ValueError("提现金额必须为整数") from exc
            if not math.isfinite(requested_number) or not requested_number.is_integer():
                raise ValueError("提现金额必须为整数")
            target = int(requested_number)
        if target <= 0:
            raise ValueError("提现金额必须大于 0")
        available = sum(_integer_balance(item["balance"]) for item in normalized)
        if target > available:
            raise ValueError(f"提现金额超过七个账号可提现整数余额（最多 {available} 元）")
        amounts = _allocate_cost(normalized, target)
        requested = float(target)
    elif mode == "full":
        amounts = [_integer_balance(item["balance"]) for item in normalized]
        requested = float(sum(amounts))
    else:
        raise ValueError("提现模式无效")

    items: list[dict[str, Any]] = []
    for sequence, (account, source, amount) in enumerate(zip(WITHDRAWAL_ACCOUNTS, normalized, amounts), start=1):
        status = "skipped" if amount <= 0 else "queued"
        items.append(
            {
                "sequence": sequence,
                "email": account.email,
                "owner": account.owner,
                "ownerLabel": account.owner_label,
                "paymentMethod": account.payment_method,
                "balance": round(source["balance"], 2),
                "amount": amount,
                "status": status,
                "statusLabel": "已跳过" if status == "skipped" else "待执行",
                "error": "余额不足 1 元" if amount <= 0 and mode == "full" else None,
            }
        )
    return {
        "mode": mode,
        "cost": total_cost,
        "requestedAmount": requested,
        "totalAmount": float(sum(amounts)),
        "items": items,
        "balanceSnapshotAt": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def settlement_for(plan: dict[str, Any]) -> dict[str, Any]:
    finalized = plan.get("status") in {"completed", "failed"}

    def effective_amount(item: dict[str, Any]) -> float:
        if finalized and item.get("status") != "submitted":
            return 0.0
        return float(item.get("amount") or 0)

    amounts = [(item, effective_amount(item)) for item in plan.get("items", [])]
    gross = round(sum(amount for _, amount in amounts), 2)
    cost = round(float(plan.get("cost") or 0), 2)
    owner_actual = round(sum(amount for item, amount in amounts if item.get("owner") == "owner"), 2)
    partner_actual = round(sum(amount for item, amount in amounts if item.get("owner") == "partner"), 2)
    if plan.get("mode") == "cost":
        owner_expected = gross
        partner_expected = 0.0
        profit = 0.0
        rounding_remainder = round(max(gross - cost, 0), 2)
        cost_recovery = round(min(gross, cost), 2)
        unrecovered_cost = round(max(cost - gross, 0), 2)
    else:
        profit = round(gross - cost, 2)
        if profit > 0:
            owner_expected = round(cost + profit * 0.6, 2)
            partner_expected = round(profit * 0.4, 2)
        else:
            owner_expected = gross
            partner_expected = 0.0
        rounding_remainder = 0.0
        cost_recovery = round(min(gross, cost), 2)
        unrecovered_cost = round(max(cost - gross, 0), 2)
    return {
        "gross": gross,
        "cost": cost,
        "profit": profit,
        "ownerActual": owner_actual,
        "partnerActual": partner_actual,
        "ownerExpected": owner_expected,
        "partnerExpected": partner_expected,
        "partnerToOwner": round(max(partner_actual - partner_expected, 0), 2),
        "ownerToPartner": round(max(partner_expected - partner_actual, 0), 2),
        "roundingRemainder": rounding_remainder,
        "costRecovery": cost_recovery,
        "unrecoveredCost": unrecovered_cost,
    }


def render_withdrawal_email(job: dict[str, Any], status: str = "已完成") -> tuple[str, str]:
    mode_label = "成本提现" if job.get("mode") == "cost" else "全部提现"
    settlement = (
        settlement_for(job)
        if job.get("status") in {"completed", "failed"}
        else (job.get("settlement") or settlement_for(job))
    )
    actual_amount = float(settlement.get("gross") or 0)
    post_cost = job.get("postWithdrawalCost")
    post_balance = job.get("postWithdrawalBalance")
    discounted_profit = job.get("discountedProfit")
    if post_cost is None:
        post_cost = float(settlement.get("cost") or 0)
    if post_balance is None and job.get("balanceSnapshotTotal") is not None:
        post_balance = round(max(float(job.get("balanceSnapshotTotal") or 0) - actual_amount, 0), 2)
    if discounted_profit is None and post_balance is not None:
        discounted_profit = round(float(post_balance) - float(post_cost), 2)

    lines = [
        f"任务编号：{job.get('jobId', '-')}",
        f"提现模式：{mode_label}",
        f"总成本：{float(settlement.get('cost') or 0):.2f} 元",
        f"计划提现：{float(job.get('requestedAmount') or 0):.2f} 元",
        f"实际提交：{float(settlement.get('gross') or 0):.2f} 元",
        f"余额快照时间：{_display_time(job.get('balanceSnapshotAt'))}",
    ]

    cost_history = job.get("costHistory") or []
    lines += ["", "成本历史明细（任务创建时冻结）："]
    if cost_history:
        for index, addition in enumerate(cost_history, start=1):
            lines.append(
                f"{index}. 成本日期：{_display_time(addition.get('date'))} | "
                f"金额：{float(addition.get('amount') or 0):.2f} 元 | "
                f"备注：{addition.get('note') or '-'} | "
                f"录入时间：{_display_time(addition.get('createdAt') or addition.get('created_at'))}"
            )
        lines.append(f"成本历史合计：{float(job.get('costHistoryTotal') or 0):.2f} 元")
    else:
        lines.append("暂无成本历史明细。")

    cost_status = job.get("costSettlementStatus")
    if cost_status == "cleared":
        lines.append(
            f"成本历史处理：本次成本已收回，已自动清空任务快照中的成本记录，"
            f"共 {float(job.get('costClearedAmount') or 0):.2f} 元。"
        )
    elif cost_status == "already_cleared":
        lines.append("成本历史处理：任务快照中的记录此前已清空，本次未重复扣减。")
    elif cost_status == "not_recovered":
        lines.append("成本历史处理：本次未完整收回成本，成本记录继续保留。")

    if job.get("mode") == "cost":
        lines += ["", "成本提现不参与 60%/40% 利润分成。"]
    elif float(settlement.get("profit") or 0) > 0:
        lines.extend(
            [
                "",
                f"可分配利润：{float(settlement.get('profit') or 0):.2f} 元",
                f"星星应得 = {float(settlement.get('cost') or 0):.2f} + {float(settlement.get('profit') or 0):.2f} × 60% = {float(settlement.get('ownerExpected') or 0):.2f} 元",
                f"社会哥应得 = {float(settlement.get('profit') or 0):.2f} × 40% = {float(settlement.get('partnerExpected') or 0):.2f} 元",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "可分配利润：0.00 元",
                f"实际提现总额 {float(settlement.get('gross') or 0):.2f} 元低于总成本 {float(settlement.get('cost') or 0):.2f} 元。",
                f"星星应得 = 实际提现总额 = {float(settlement.get('ownerExpected') or 0):.2f} 元",
                "社会哥应得 = 0.00 元",
                f"亏损 {float(settlement.get('unrecoveredCost') or 0):.2f} 元由星星承担。",
            ]
        )
    lines += ["", "账号明细："]
    for item in job.get("items", []):
        reason = f"（{item.get('error')}）" if item.get("error") else ""
        lines.append(
            f"{item.get('email', '-'):<22} {item.get('ownerLabel', '-'):<10} "
            f"{float(item.get('amount') or 0):.0f} 元  "
            f"{item.get('statusLabel') or item.get('status') or '-'}{reason}"
        )
    settlement_lines = [
        "",
        f"实际提现到星星账号：{float(settlement.get('ownerActual') or 0):.2f} 元",
        f"实际提现到社会哥账号：{float(settlement.get('partnerActual') or 0):.2f} 元",
    ]
    if job.get("mode") == "cost":
        settlement_lines += [
            f"社会哥需要转给星星：{float(settlement.get('partnerToOwner') or 0):.2f} 元",
            f"星星本次成本回收：{float(settlement.get('costRecovery') or 0):.2f} 元",
            f"尚未收回成本：{float(settlement.get('unrecoveredCost') or 0):.2f} 元",
            f"舍入余量：{float(settlement.get('roundingRemainder') or 0):.2f} 元",
        ]
    else:
        settlement_lines += [
            f"星星应得：{float(settlement.get('ownerExpected') or 0):.2f} 元",
            f"社会哥应得：{float(settlement.get('partnerExpected') or 0):.2f} 元",
            *(["当前为亏损，社会哥应收为 0 元，亏损由星星承担。"] if float(settlement.get("profit") or 0) <= 0 else []),
            "",
            "结算转账：",
            f"社会哥需要转给星星：{float(settlement.get('partnerToOwner') or 0):.2f} 元",
            f"星星需要转给社会哥：{float(settlement.get('ownerToPartner') or 0):.2f} 元",
        ]
    submitted_times = [item.get("submittedAt") for item in job.get("items", []) if item.get("submittedAt")]
    lines += [
        "",
        "执行时间：",
        f"第一笔：{_display_time(min(submitted_times)) if submitted_times else '-'}",
        f"最后一笔：{_display_time(max(submitted_times)) if submitted_times else '-'}",
        f"任务状态：{status}",
        "PixelAPI 状态：已提交，等待平台结算" if job.get("status") == "completed" else "PixelAPI 状态：任务已暂停，已提交的账号不会重复提交",
        "",
        "提现后汇总：",
        f"提现后总成本：{float(post_cost):.2f} 元",
        f"提现后总余额：{float(post_balance):.2f} 元" if post_balance is not None else "提现后总余额：暂未获取",
        (
            f"提现后折后利润：{float(discounted_profit):.2f} 元"
            if discounted_profit is not None
            else "提现后折后利润：暂未计算"
        ),
        "折后利润计算：提现后总余额 - 提现后总成本",
    ]
    lines += settlement_lines
    subject = f"[91] {mode_label}任务 #{job.get('jobId', '-')} {status}"
    return subject, "\n".join(lines)
