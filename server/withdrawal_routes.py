from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query

try:
    from .pixel_manager import PixelManagerError
except ImportError:
    from pixel_manager import PixelManagerError


@dataclass(frozen=True)
class WithdrawalRouteHandlers:
    plan_for_request: Callable[..., dict[str, Any]]
    preview: Callable[..., Any]
    preview_post: Callable[..., Any]
    list_jobs: Callable[..., Any]
    history: Callable[..., Any]
    create: Callable[..., Any]
    get: Callable[..., Any]
    accelerate: Callable[..., Any]
    retry: Callable[..., Any]


def create_withdrawal_router(
    *,
    api_prefix: str,
    require_manager: Callable[..., Any],
    pixel_http_error: Callable[[PixelManagerError], HTTPException],
    service: Any,
) -> tuple[APIRouter, WithdrawalRouteHandlers]:
    router = APIRouter(prefix=api_prefix)

    def plan_for_request(mode: str, requested_amount: Any | None = None) -> dict[str, Any]:
        try:
            return service.latest_plan(mode, requested_amount)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    async def checked_plan_for_request(
        payload: dict[str, Any], manager: Any
    ) -> dict[str, Any]:
        mode = str(payload.get("mode") or "cost").strip().lower()
        requested = payload.get("amount")
        requested_amount = None if requested in (None, "") else requested
        account_amounts = payload.get("accountAmounts", payload.get("account_amounts"))
        try:
            return await service.preview_plan(mode, requested_amount, account_amounts, manager)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def validate_targets(plan: dict[str, Any], manager: Any) -> dict[str, str]:
        target_ids = service.target_ids(manager)
        missing = [
            item["email"]
            for item in plan["items"]
            if item["amount"] > 0 and item["email"].lower() not in target_ids
        ]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"PixelAPI 目标缺少账号：{'、'.join(missing)}",
            )
        return target_ids

    def validate_preflight(plan: dict[str, Any]) -> None:
        unverified = [
            item["email"]
            for item in plan["items"]
            if item["amount"] > 0
            and item.get("status") != "skipped"
            and (item.get("eligibility") or {}).get("status") == "unknown"
        ]
        if unverified:
            raise HTTPException(
                status_code=503,
                detail=f"Pixel 提现预检未能确认：{'、'.join(unverified)}，本次不会提交",
            )

    @router.get("/withdrawals/preview")
    async def preview(
        mode: str = Query(default="cost"),
        amount: float | None = Query(default=None),
        manager: Any = Depends(require_manager),
    ) -> dict[str, Any]:
        plan = await checked_plan_for_request({"mode": mode, "amount": amount}, manager)
        validate_targets(plan, manager)
        return plan

    @router.post("/withdrawals/preview")
    async def preview_post(
        payload: dict[str, Any],
        manager: Any = Depends(require_manager),
    ) -> dict[str, Any]:
        plan = await checked_plan_for_request(payload, manager)
        validate_targets(plan, manager)
        return plan

    @router.get("/withdrawals")
    async def list_jobs(
        manager: Any = Depends(require_manager),
    ) -> dict[str, Any]:
        return {"job": service.current_job()}

    @router.get("/withdrawals/history")
    async def history(
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        manager: Any = Depends(require_manager),
    ) -> dict[str, Any]:
        return service.job_history(limit=limit, offset=offset)

    @router.post("/withdrawals")
    async def create(
        payload: dict[str, Any],
        manager: Any = Depends(require_manager),
    ) -> dict[str, Any]:
        plan = await checked_plan_for_request(payload, manager)
        target_ids = validate_targets(plan, manager)
        validate_preflight(plan)
        missing_receipts: list[str] = []
        for item in plan["items"]:
            if item["amount"] <= 0 or item.get("status") == "skipped":
                continue
            target_id = target_ids[item["email"].lower()]
            try:
                receipt = await manager.receipt_code(target_id, item["paymentMethod"])
            except PixelManagerError as exc:
                raise pixel_http_error(exc) from exc
            if not receipt:
                missing_receipts.append(f"{item['email']}（{item['ownerLabel']}）")
        if missing_receipts:
            raise HTTPException(
                status_code=400,
                detail=f"以下账号未配置对应收款码：{'、'.join(missing_receipts)}",
            )
        try:
            job = service.create_job(plan, target_ids)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        service.wake_event.set()
        return {"job": job}

    @router.get("/withdrawals/{job_id}")
    async def get(
        job_id: str,
        manager: Any = Depends(require_manager),
    ) -> dict[str, Any]:
        job = service.job_detail(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="提现任务不存在")
        return {"job": job}

    @router.post("/withdrawals/{job_id}/accelerate")
    async def accelerate(
        job_id: str,
        manager: Any = Depends(require_manager),
    ) -> dict[str, Any]:
        try:
            job = service.accelerate_job(job_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not job:
            raise HTTPException(status_code=404, detail="提现任务不存在")
        return {"job": job}

    @router.post("/withdrawals/{job_id}/retry")
    async def retry(
        job_id: str,
        manager: Any = Depends(require_manager),
    ) -> dict[str, Any]:
        try:
            job = await service.retry_job(job_id, manager)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not job:
            raise HTTPException(status_code=404, detail="提现任务不存在")
        return {"job": job}

    return router, WithdrawalRouteHandlers(
        plan_for_request=plan_for_request,
        preview=preview,
        preview_post=preview_post,
        list_jobs=list_jobs,
        history=history,
        create=create,
        get=get,
        accelerate=accelerate,
        retry=retry,
    )
