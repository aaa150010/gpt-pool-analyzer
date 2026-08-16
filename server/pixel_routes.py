from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from starlette.datastructures import UploadFile

try:
    from .pixel_manager import (
        PixelExportJobs,
        PixelImportJobs,
        PixelJobCoordinator,
        PixelManagerError,
        PixelValidationError,
        merge_credential_bundles,
        parse_credential_bundle,
    )
except ImportError:
    from pixel_manager import (
        PixelExportJobs,
        PixelImportJobs,
        PixelJobCoordinator,
        PixelManagerError,
        PixelValidationError,
        merge_credential_bundles,
        parse_credential_bundle,
    )


def create_pixel_router(
    *,
    api_prefix: str,
    require_manager: Callable[..., Any],
    require_sensitive_manager: Callable[..., Any],
    pixel_http_error: Callable[[PixelManagerError], HTTPException],
    connect: Callable[..., Any],
    import_records: Callable[[], list[dict[str, Any]]],
    import_record: Callable[[str], dict[str, Any] | None],
    update_import_delete: Callable[[str, dict[str, Any]], dict[str, Any]],
    update_import_share: Callable[[str, str, dict[str, Any]], dict[str, Any]],
    save_import_record: Callable[[dict[str, Any]], None],
    get_import_jobs: Callable[[], PixelImportJobs | None],
    set_import_jobs: Callable[[PixelImportJobs], None],
    get_export_jobs: Callable[[], PixelExportJobs | None],
    set_export_jobs: Callable[[PixelExportJobs], None],
    get_job_coordinator: Callable[[], PixelJobCoordinator],
    get_data_dir: Callable[[], Path],
    get_max_upload_bytes: Callable[[], int],
) -> APIRouter:
    router = APIRouter(prefix=api_prefix)

    @router.get("/pixel-manager/targets")
    async def get_targets(manager: Any = Depends(require_manager)) -> dict[str, Any]:
        return {"targets": manager.targets()}

    @router.post("/pixel-manager/local-bootstrap")
    async def local_bootstrap(
        response: Response,
        payload: dict[str, Any] | None = None,
        manager: Any = Depends(require_sensitive_manager),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        body = payload or {}
        try:
            return await manager.local_bootstrap(
                str(body.get("revision") or ""),
                body.get("refreshTargetIds") or [],
            )
        except PixelManagerError as exc:
            raise pixel_http_error(exc) from exc

    @router.get("/pixel-manager/targets/{target_id}/accounts")
    async def get_accounts(
        target_id: str,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=100, alias="pageSize", ge=1, le=100),
        search: str = Query(default="", max_length=120),
        status: str = Query(default="", max_length=40),
        manager: Any = Depends(require_manager),
    ) -> dict[str, Any]:
        try:
            return await manager.list_accounts(
                target_id,
                page,
                page_size,
                search=search.strip(),
                status=status,
            )
        except PixelManagerError as exc:
            raise pixel_http_error(exc) from exc

    @router.get("/pixel-manager/import-records")
    async def get_import_records(manager: Any = Depends(require_manager)) -> dict[str, Any]:
        return {"records": import_records()}

    @router.get("/pixel-manager/import-records/{record_id}")
    async def get_import_record(record_id: str, manager: Any = Depends(require_manager)) -> dict[str, Any]:
        record = import_record(record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="导入记录不存在")
        return {"record": record}

    @router.post("/pixel-manager/import-records/{record_id}/share")
    async def retry_import_share(
        record_id: str,
        payload: dict[str, Any],
        manager: Any = Depends(require_manager),
    ) -> dict[str, Any]:
        record = import_record(record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="导入记录不存在")
        target_id = str(payload.get("targetId") or "").strip()
        if not target_id:
            raise HTTPException(status_code=400, detail="平台账号不能为空")
        try:
            result = await manager.share_accounts(target_id, payload.get("accountIds") or [])
            return {
                "record": update_import_share(record_id, target_id, result),
                "result": result,
            }
        except PixelManagerError as exc:
            raise pixel_http_error(exc) from exc

    @router.post("/pixel-manager/import-records/{record_id}/delete")
    async def delete_import_accounts(
        record_id: str,
        manager: Any = Depends(require_manager),
    ) -> dict[str, Any]:
        record = import_record(record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="导入记录不存在")
        try:
            result = await manager.delete_import_record(record)
        except PixelManagerError as exc:
            raise pixel_http_error(exc) from exc
        return {"record": update_import_delete(record_id, result), "result": result}

    @router.delete("/pixel-manager/import-records/{record_id}")
    async def remove_import_history(
        record_id: str,
        manager: Any = Depends(require_manager),
    ) -> dict[str, Any]:
        with connect() as conn:
            deleted = conn.execute(
                "DELETE FROM pixel_import_records WHERE record_id = ?",
                (record_id,),
            ).rowcount
        if not deleted:
            raise HTTPException(status_code=404, detail="导入记录不存在")
        return {"ok": True}

    @router.get("/pixel-manager/targets/{target_id}/accounts/{account_id}/usage")
    async def get_account_usage(
        target_id: str,
        account_id: int,
        manager: Any = Depends(require_manager),
    ) -> dict[str, Any]:
        try:
            return await manager.account_usage(target_id, account_id)
        except PixelManagerError as exc:
            raise pixel_http_error(exc) from exc

    @router.post("/pixel-manager/targets/{target_id}/relogin")
    async def relogin_target(
        target_id: str,
        manager: Any = Depends(require_manager),
    ) -> dict[str, Any]:
        try:
            return await manager.relogin(target_id)
        except PixelManagerError as exc:
            raise pixel_http_error(exc) from exc

    @router.post("/pixel-manager/targets/{target_id}/accounts/bulk-delete")
    async def bulk_delete_accounts(
        target_id: str,
        payload: dict[str, Any],
        manager: Any = Depends(require_manager),
    ) -> dict[str, Any]:
        try:
            return await manager.bulk_delete_accounts(target_id, payload.get("accountIds") or [])
        except PixelManagerError as exc:
            raise pixel_http_error(exc) from exc

    @router.post("/pixel-manager/targets/{target_id}/accounts/bulk-test")
    async def bulk_test_accounts(
        target_id: str,
        payload: dict[str, Any],
        manager: Any = Depends(require_manager),
    ) -> dict[str, Any]:
        try:
            return await manager.bulk_test_accounts(target_id, payload.get("accountIds") or [])
        except PixelManagerError as exc:
            raise pixel_http_error(exc) from exc

    @router.post("/pixel-manager/targets/{target_id}/accounts/bulk-update")
    async def bulk_update_accounts(
        target_id: str,
        payload: dict[str, Any],
        manager: Any = Depends(require_manager),
    ) -> dict[str, Any]:
        try:
            return await manager.bulk_update_accounts(
                target_id,
                payload.get("accountIds") or [],
                share_mode=payload.get("shareMode") or ("public" if payload.get("makePublic") else None),
                concurrency=payload.get("concurrency"),
            )
        except PixelManagerError as exc:
            raise pixel_http_error(exc) from exc

    @router.post("/pixel-manager/import", status_code=202)
    async def create_import(
        request: Request,
        target_ids_json: str = Query(alias="targetIds"),
        file_name: str | None = Query(default=None, alias="fileName"),
        manager: Any = Depends(require_manager),
    ) -> dict[str, Any]:
        try:
            async with get_job_coordinator().hold():
                if _has_active_jobs(get_export_jobs(), manager):
                    raise HTTPException(status_code=409, detail="汇总整理任务运行中，暂不能开始导入")
                if _has_active_jobs(get_import_jobs(), manager):
                    raise HTTPException(status_code=409, detail="已有导入任务正在运行，请等待完成")
                target_ids = _target_ids(target_ids_json)
                max_upload_bytes = get_max_upload_bytes()
                content_length = request.headers.get("content-length")
                if content_length and content_length.isdigit() and int(content_length) > max_upload_bytes:
                    raise HTTPException(status_code=413, detail="JSON 文件不能超过 50 MB")
                payload_buffer = bytearray()
                async for chunk in request.stream():
                    if len(payload_buffer) + len(chunk) > max_upload_bytes:
                        raise HTTPException(status_code=413, detail="JSON 文件不能超过 50 MB")
                    payload_buffer.extend(chunk)
                bundle = parse_credential_bundle(file_name or "accounts.json", bytes(payload_buffer))
                jobs = get_import_jobs()
                if jobs is None or jobs.manager is not manager:
                    jobs = PixelImportJobs(manager, record_callback=save_import_record)
                    set_import_jobs(jobs)
                return {"job": await jobs.create(bundle, target_ids)}
        except PixelManagerError as exc:
            raise pixel_http_error(exc) from exc

    @router.post("/pixel-manager/import-batch", status_code=202)
    async def create_import_batch(
        request: Request,
        target_ids_json: str = Query(alias="targetIds"),
        manager: Any = Depends(require_manager),
    ) -> dict[str, Any]:
        uploads: list[UploadFile] = []
        try:
            async with get_job_coordinator().hold():
                if _has_active_jobs(get_export_jobs(), manager):
                    raise HTTPException(status_code=409, detail="汇总整理任务运行中，暂不能开始导入")
                if _has_active_jobs(get_import_jobs(), manager):
                    raise HTTPException(status_code=409, detail="已有导入任务正在运行，请等待完成")
                target_ids = _target_ids(target_ids_json)
                max_upload_bytes = get_max_upload_bytes()
                form = await request.form()
                uploads = [item for item in form.getlist("files") if isinstance(item, UploadFile)]
                if not uploads:
                    raise PixelValidationError("至少选择一个 JSON 文件")
                if len(uploads) > 100:
                    raise PixelValidationError("一次最多选择 100 个 JSON 文件")
                total_bytes = 0
                bundles = []
                for upload in uploads:
                    content = await upload.read(max_upload_bytes + 1)
                    total_bytes += len(content)
                    if total_bytes > max_upload_bytes:
                        raise PixelManagerError("批量 JSON 文件合计不能超过 50 MB", 413)
                    bundles.append(parse_credential_bundle(upload.filename or "accounts.json", content))
                bundle = merge_credential_bundles(bundles)
                jobs = get_import_jobs()
                if jobs is None or jobs.manager is not manager:
                    jobs = PixelImportJobs(manager, record_callback=save_import_record)
                    set_import_jobs(jobs)
                return {"job": await jobs.create(bundle, target_ids)}
        except PixelManagerError as exc:
            raise pixel_http_error(exc) from exc
        finally:
            for upload in uploads:
                await upload.close()

    @router.get("/pixel-manager/import-jobs/{job_id}")
    async def get_import_job(
        job_id: str,
        manager: Any = Depends(require_manager),
    ) -> dict[str, Any]:
        jobs = get_import_jobs()
        if jobs is None or jobs.manager is not manager:
            raise HTTPException(status_code=404, detail="导入任务不存在")
        try:
            return {"job": jobs.get(job_id)}
        except PixelManagerError as exc:
            raise pixel_http_error(exc) from exc

    @router.post("/pixel-manager/import-jobs/{job_id}/accelerate")
    async def accelerate_import_job(
        job_id: str,
        manager: Any = Depends(require_manager),
    ) -> dict[str, Any]:
        jobs = get_import_jobs()
        if jobs is None or jobs.manager is not manager:
            raise HTTPException(status_code=404, detail="导入任务不存在")
        try:
            return {"job": await jobs.accelerate(job_id)}
        except PixelManagerError as exc:
            raise pixel_http_error(exc) from exc

    @router.post("/pixel-manager/import-jobs/{job_id}/retry", status_code=202)
    async def retry_import_job(
        job_id: str,
        manager: Any = Depends(require_manager),
    ) -> dict[str, Any]:
        try:
            async with get_job_coordinator().hold():
                if _has_active_jobs(get_export_jobs(), manager):
                    raise HTTPException(status_code=409, detail="汇总整理任务运行中，暂不能重试导入")
                jobs = get_import_jobs()
                if jobs is None or jobs.manager is not manager:
                    raise HTTPException(status_code=404, detail="导入任务不存在")
                return {"job": await jobs.retry(job_id)}
        except PixelManagerError as exc:
            raise pixel_http_error(exc) from exc

    @router.post("/pixel-manager/targets/{target_id}/share")
    async def share_accounts(
        target_id: str,
        payload: dict[str, Any],
        manager: Any = Depends(require_manager),
    ) -> dict[str, Any]:
        try:
            return await manager.share_accounts(target_id, payload.get("accountIds") or [])
        except PixelManagerError as exc:
            raise pixel_http_error(exc) from exc

    @router.post("/pixel-manager/share-all")
    async def share_all_accounts(
        payload: dict[str, Any] | None = None,
        manager: Any = Depends(require_manager),
    ) -> dict[str, Any]:
        try:
            body = payload or {}
            return await manager.share_all_accounts(
                body.get("targetIds") or [],
                concurrency=body.get("concurrency"),
            )
        except PixelManagerError as exc:
            raise pixel_http_error(exc) from exc

    @router.get("/pixel-manager/export")
    async def export_accounts(manager: Any = Depends(require_manager)) -> Response:
        try:
            export = await manager.export_all()
        except PixelManagerError as exc:
            raise pixel_http_error(exc) from exc
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return Response(
            content=export.content,
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="pixel-accounts-{stamp}.json"',
                "X-Pixel-Source-Count": str(export.source_count),
                "X-Pixel-Deduplicated-Count": str(export.deduplicated_count),
                "X-Pixel-Duplicate-Count": str(export.duplicate_count),
                "X-Pixel-Batch-Count": str(export.batch_count),
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
            },
        )

    @router.post("/pixel-manager/export-jobs", status_code=202)
    async def create_export_job(
        payload: dict[str, Any],
        manager: Any = Depends(require_manager),
    ) -> dict[str, Any]:
        if not payload.get("deleteAllAndReimport"):
            raise HTTPException(status_code=400, detail="汇总整理任务必须确认删除并重新导入")
        try:
            async with get_job_coordinator().hold():
                if _has_active_jobs(get_import_jobs(), manager):
                    raise HTTPException(status_code=409, detail="导入任务运行中，暂不能开始汇总整理")
                if _has_active_jobs(get_export_jobs(), manager):
                    raise HTTPException(status_code=409, detail="已有汇总整理任务正在运行，请等待完成")
                jobs = get_export_jobs()
                if jobs is None or jobs.manager is not manager:
                    jobs = PixelExportJobs(manager, get_data_dir() / "pixel_exports")
                    set_export_jobs(jobs)
                return {"job": await jobs.create_rebuild(payload.get("targetIds") or [])}
        except PixelManagerError as exc:
            raise pixel_http_error(exc) from exc

    @router.get("/pixel-manager/export-jobs/{job_id}")
    async def get_export_job(
        job_id: str,
        manager: Any = Depends(require_manager),
    ) -> dict[str, Any]:
        jobs = get_export_jobs()
        if jobs is None or jobs.manager is not manager:
            raise HTTPException(status_code=404, detail="汇总整理任务不存在")
        try:
            return {"job": jobs.get(job_id)}
        except PixelManagerError as exc:
            raise pixel_http_error(exc) from exc

    @router.get("/pixel-manager/export-jobs/{job_id}/download")
    async def download_export_backup(
        job_id: str,
        manager: Any = Depends(require_manager),
    ) -> Response:
        jobs = get_export_jobs()
        if jobs is None or jobs.manager is not manager:
            raise HTTPException(status_code=404, detail="汇总整理任务不存在")
        try:
            file_name, content = jobs.backup_content(job_id)
        except PixelManagerError as exc:
            raise pixel_http_error(exc) from exc
        return Response(
            content=content,
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{file_name}"',
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
            },
        )

    return router


def _has_active_jobs(jobs: Any, manager: Any) -> bool:
    if jobs is None or getattr(jobs, "manager", None) is not manager:
        return False
    checker = getattr(jobs, "has_active_job", None)
    return bool(checker and checker())


def _target_ids(value: str) -> list[str]:
    try:
        target_ids = json.loads(value)
        if not isinstance(target_ids, list):
            raise ValueError
        return target_ids
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="上传账号选择无效") from exc
