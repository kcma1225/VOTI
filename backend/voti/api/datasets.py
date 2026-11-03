"""/api/datasets, /api/datasets/preview, exports and export downloads."""
from __future__ import annotations

import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, Response
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import datasets as D
from .. import storage
from ..auth import current_user
from ..db import get_session
from ..models import Asset, Dataset, DatasetExport, Job, User
from ..queue import enqueue
from . import schemas as S
from .errors import ApiError
from .views import Paging, audit, get_or_404, job_outs, paging, user_refs

router = APIRouter(tags=["datasets"])


def _normalize_filters(raw: dict | None) -> dict:
    try:
        return D.parse_filters(raw).model_dump(mode="json")
    except ValidationError as e:
        err = e.errors()[0]
        raise ApiError(422, "invalid_filters",
                       f"篩選條件不正確：{'.'.join(str(x) for x in err.get('loc', ()))} {err.get('msg', '')}") from None


def dataset_outs(s: Session, rows: list[Dataset]) -> list[S.DatasetOut]:
    ids = [d.id for d in rows]
    counts = dict(s.execute(select(DatasetExport.dataset_id, func.count()).where(DatasetExport.dataset_id.in_(ids))
                            .group_by(DatasetExport.dataset_id)).all()) if ids else {}
    # latest export per dataset (any status) and latest completed one (for the cached size)
    latest: dict = {}
    completed: dict = {}
    if ids:
        for e in s.scalars(select(DatasetExport).where(DatasetExport.dataset_id.in_(ids))
                           .order_by(DatasetExport.created_at.desc())):
            latest.setdefault(e.dataset_id, e)
            if e.status == "completed":
                completed.setdefault(e.dataset_id, e)
    last_outs = {o.dataset_id: o for o in export_outs(s, list(latest.values()))}
    refs = user_refs(s, {d.created_by for d in rows})
    out = []
    for d in rows:
        done = completed.get(d.id)
        ref = refs.get(d.created_by)
        out.append(S.DatasetOut(id=d.id, name=d.name, description=d.description, filters=d.filters or {},
                                created_by=S.UserRef(id=ref.id, display_name=ref.display_name) if ref else None,
                                created_at=d.created_at, updated_at=d.updated_at, export_count=counts.get(d.id, 0),
                                segment_count=done.segment_count if done else None,
                                total_sec=done.total_sec if done else None, last_export=last_outs.get(d.id)))
    return out


@router.get("/datasets", response_model=S.Page[S.DatasetOut])
def list_datasets(page: Paging = Depends(paging), _: User = Depends(current_user), s: Session = Depends(get_session)):
    total = s.scalar(select(func.count()).select_from(Dataset)) or 0
    rows = s.scalars(select(Dataset).order_by(Dataset.updated_at.desc(), Dataset.id)
                     .limit(page.limit).offset(page.offset)).all()
    return S.Page[S.DatasetOut](items=dataset_outs(s, list(rows)), total=total)


@router.post("/datasets", response_model=S.DatasetOut, status_code=201)
def create_dataset(body: S.DatasetIn, user: User = Depends(current_user), s: Session = Depends(get_session)):
    d = Dataset(name=body.name.strip(), description=body.description, filters=_normalize_filters(body.filters),
                created_by=user.id)
    s.add(d)
    s.flush()
    audit(s, user, "dataset.create", "dataset", d.id, after={"name": d.name, "filters": d.filters})
    s.commit()
    return dataset_outs(s, [d])[0]


@router.post("/datasets/preview", response_model=S.PreviewOut)
def preview(body: S.PreviewIn, _: User = Depends(current_user), s: Session = Depends(get_session)):
    return S.PreviewOut(**D.preview(s, _normalize_filters(body.filters)))


@router.get("/datasets/{dataset_id}", response_model=S.DatasetOut)
def get_dataset(dataset_id: uuid.UUID, _: User = Depends(current_user), s: Session = Depends(get_session)):
    return dataset_outs(s, [get_or_404(s, Dataset, dataset_id, "資料集")])[0]


@router.patch("/datasets/{dataset_id}", response_model=S.DatasetOut)
def patch_dataset(dataset_id: uuid.UUID, body: S.DatasetPatchIn, user: User = Depends(current_user),
                  s: Session = Depends(get_session)):
    d = get_or_404(s, Dataset, dataset_id, "資料集")
    before = {"name": d.name, "description": d.description, "filters": d.filters}
    if body.name is not None:
        d.name = body.name.strip()
    if body.description is not None:
        d.description = body.description
    if body.filters is not None:
        d.filters = _normalize_filters(body.filters)
    audit(s, user, "dataset.update", "dataset", d.id, before=before,
          after={"name": d.name, "description": d.description, "filters": d.filters})
    s.commit()
    return dataset_outs(s, [d])[0]


@router.delete("/datasets/{dataset_id}", status_code=204, response_class=Response)
def delete_dataset(dataset_id: uuid.UUID, user: User = Depends(current_user), s: Session = Depends(get_session)):
    d = get_or_404(s, Dataset, dataset_id, "資料集")
    if s.scalar(select(DatasetExport.id).where(DatasetExport.dataset_id == d.id,
                                               DatasetExport.status.in_(("queued", "running")))):
        raise ApiError(409, "export_running", "資料集仍有匯出工作進行中")
    audit(s, user, "dataset.delete", "dataset", d.id, before={"name": d.name, "filters": d.filters})
    s.delete(d)  # exports cascade; their S3 objects are kept (assets remain listed)
    s.commit()
    return Response(status_code=204)


# ───────────────────────────── exports ─────────────────────────────

def export_outs(s: Session, rows: list[DatasetExport]) -> list[S.DatasetExportOut]:
    job_ids = [e.job_id for e in rows if e.job_id]
    jobs = {j.id: j for j in job_outs(s, s.scalars(select(Job).where(Job.id.in_(job_ids))).all())} if job_ids else {}
    sizes: dict = defaultdict(dict)
    if rows:
        pats = [f"exports/{e.id}/%" for e in rows]
        for key, size in s.execute(select(Asset.key, Asset.bytes).where(or_(*[Asset.key.like(p) for p in pats]))):
            _, eid, name = key.split("/", 2)
            sizes[eid][name] = int(size or 0)
    out = []
    for e in rows:
        files = []
        if e.status == "completed":
            files = ["manifest.jsonl", "manifest.csv", "README.md"] + (["dataset.tar"] if e.archive_asset_id else [])
        opts = (e.stats or {}).get("options") or {}
        job = jobs.get(e.job_id)
        progress = 1.0 if e.status == "completed" else (job.progress if job else 0.0)
        sz = sizes.get(str(e.id), {})
        details = [S.ExportFile(name=n, size_bytes=sz.get(n, 0), url=f"/api/exports/{e.id}/download/{n}")
                   for n in files]
        out.append(S.DatasetExportOut(
            id=e.id, dataset_id=e.dataset_id, status=e.status, format=opts.get("format", "jsonl"),
            include_audio=bool(opts.get("include_audio", True)), filters=e.filters or {},
            segment_count=e.segment_count, total_sec=e.total_sec, stats=e.stats or {}, progress=progress,
            size_bytes=sum(sz.get(n, 0) for n in files) if files else None, files=files, file_details=details,
            job=job, created_by=e.created_by, created_at=e.created_at,
            finished_at=job.finished_at if job and e.status in ("completed", "failed") else None))
    return out


@router.post("/datasets/{dataset_id}/export", response_model=S.DatasetExportOut, status_code=201)
def export_dataset(dataset_id: uuid.UUID, body: S.ExportIn, user: User = Depends(current_user),
                   s: Session = Depends(get_session)):
    d = get_or_404(s, Dataset, dataset_id, "資料集")
    e = DatasetExport(dataset_id=d.id, status="queued", filters=_normalize_filters(d.filters),
                      stats={"options": {"include_audio": body.include_audio, "format": body.format}},
                      created_by=user.id)
    s.add(e)
    s.flush()
    job = enqueue(s, "dataset.export", payload={"export_id": str(e.id), "include_audio": body.include_audio,
                                                "format": body.format}, created_by=user.id)
    e.job_id = job.id
    audit(s, user, "dataset.export", "dataset", d.id, after={"export_id": str(e.id), **body.model_dump()})
    s.commit()
    return export_outs(s, [e])[0]


@router.get("/datasets/{dataset_id}/exports", response_model=S.Page[S.DatasetExportOut])
def list_exports(dataset_id: uuid.UUID, page: Paging = Depends(paging), _: User = Depends(current_user),
                 s: Session = Depends(get_session)):
    get_or_404(s, Dataset, dataset_id, "資料集")
    total = s.scalar(select(func.count()).select_from(DatasetExport).where(DatasetExport.dataset_id == dataset_id))
    rows = s.scalars(select(DatasetExport).where(DatasetExport.dataset_id == dataset_id)
                     .order_by(DatasetExport.created_at.desc()).limit(page.limit).offset(page.offset)).all()
    return S.Page[S.DatasetExportOut](items=export_outs(s, list(rows)), total=total or 0)


@router.get("/exports/{export_id}", response_model=S.DatasetExportOut)
def get_export(export_id: uuid.UUID, _: User = Depends(current_user), s: Session = Depends(get_session)):
    return export_outs(s, [get_or_404(s, DatasetExport, export_id, "匯出")])[0]


CONTENT_TYPES = {"manifest.jsonl": "application/x-ndjson; charset=utf-8", "manifest.csv": "text/csv; charset=utf-8",
                 "README.md": "text/markdown; charset=utf-8", "dataset.tar": "application/x-tar"}


@router.get("/exports/{export_id}/download/{name}", response_class=Response,
            responses={200: {"content": {"application/octet-stream": {}}}})
def download_export(export_id: uuid.UUID, name: str, _: User = Depends(current_user),
                    s: Session = Depends(get_session)):
    if name not in D.EXPORT_FILES:
        raise ApiError(404, "not_found", "沒有這個匯出檔案")
    e = get_or_404(s, DatasetExport, export_id, "匯出")
    if e.status != "completed":
        raise ApiError(409, "export_not_ready", "匯出尚未完成")
    key = f"exports/{e.id}/{name}"
    if storage.head(key) is None:
        raise ApiError(404, "not_found", "找不到匯出檔案")
    obj = storage.open_range(key)
    body = obj["Body"]

    def chunks():  # noqa: ANN202
        try:
            yield from body.iter_chunks(chunk_size=1024 * 1024)
        finally:
            body.close()
    fname = f"{e.id}_{name}"
    return StreamingResponse(chunks(), media_type=CONTENT_TYPES[name],
                             headers={"Content-Length": str(obj["ContentLength"]),
                                      "Content-Disposition": f'attachment; filename="{fname}"'})
