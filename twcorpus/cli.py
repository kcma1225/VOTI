"""本地命令列入口；worker 與網頁介面分開執行。"""
from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

from .config import load_settings
from .core import export_corpus, import_local, statistics
from .db import init_db
from .jobs import recover, run_one


def main():
    parser = argparse.ArgumentParser(description="台灣語音語料本地處理工具")
    sub = parser.add_subparsers(dest="command", required=True)
    worker = sub.add_parser("worker", help="執行佇列工作")
    worker.add_argument("--loop", action="store_true", help="持續執行；Ctrl+C 停止")
    worker.add_argument("--recover-after", type=int, default=20)
    local = sub.add_parser("import-local", help="匯入本地音訊與字幕")
    local.add_argument("audio", type=Path)
    local.add_argument("--subtitle", type=Path)
    local.add_argument("--license-evidence", default="")
    local.add_argument("--language", default="zh")
    sub.add_parser("stats", help="列出即時統計")
    sub.add_parser("doctor", help="檢查 FFmpeg、套件、CPU/GPU 與資料目錄容量")
    sub.add_parser("verify", help="重新計算所有資產 SHA-256，列出缺失或變動")
    saved = sub.add_parser("backup", help="備份 SQLite 與資產到新目錄（不含 .env 與模型）")
    saved.add_argument("target", type=Path)
    loaded = sub.add_parser("restore", help="把備份的資料表載入空的資料庫（先複製 assets/ 到 CORPUS_DATA_DIR）")
    loaded.add_argument("source", type=Path)
    output = sub.add_parser("export", help="輸出音段快照")
    output.add_argument("--review-status", default=None)
    output.add_argument("--corrected-only", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    init_db(settings.database)
    if args.command == "worker":
        if args.loop:
            # Heartbeat on its own thread so the UI still sees the worker during long jobs.
            beat = settings.data_dir / "worker.heartbeat"
            def pulse():
                while True:
                    beat.touch()
                    time.sleep(15)
            threading.Thread(target=pulse, daemon=True).start()
        count = recover(settings, args.recover_after)
        if count: print(f"標記 {count} 個中斷工作；請在介面中重試")
        lane = "all"
        if args.loop:
            # Model jobs (embedding, alignment) run on a second thread; everything else keeps the main one.
            def heavy():
                while True:
                    if not run_one(settings, "heavy"): time.sleep(1)
            threading.Thread(target=heavy, daemon=True).start()
            lane = "light"
        try:
            while True:
                job_id = run_one(settings, lane)
                if job_id: print(f"工作結束：{job_id}", flush=True)
                elif not args.loop: break
                else: time.sleep(0.5)
        except KeyboardInterrupt:
            print("worker 已停止；進行中工作可在超時後重試")
    elif args.command == "import-local":
        print(import_local(settings, args.audio, args.subtitle, language=args.language,
                           license_evidence=args.license_evidence))
    elif args.command == "stats":
        print(statistics(settings))
    elif args.command == "doctor":
        from .diagnostics import compute_devices, storage, tools
        for key, value in {**tools(), **compute_devices()}.items():
            print(f"{key}: {value}")
        space = storage(settings.data_dir)
        print(f"data_dir: {settings.data_dir} ({space['data_bytes'] / 1e6:.1f} MB used, {space['free_bytes'] / 1e9:.1f} GB free)")
        print(f"youtube_api_key: {'set' if settings.youtube_api_key else 'not set'}")
    elif args.command == "verify":
        from .maintenance import verify_assets
        report = verify_assets(settings)
        print(f"assets: {report['assets']}, missing: {len(report['missing'])}, changed: {len(report['changed'])}")
        for path in report["missing"] + report["changed"]:
            print(f"  {path}")
        if report["missing"] or report["changed"]:
            raise SystemExit(1)
    elif args.command == "restore":
        from .maintenance import restore
        print(restore(settings, args.source))
    elif args.command == "backup":
        from .maintenance import backup
        print(backup(settings, args.target))
    elif args.command == "export":
        print(export_corpus(settings, review_status=args.review_status,
                            corrected_only=args.corrected_only))


if __name__ == "__main__":
    main()
