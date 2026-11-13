"""从使用者自行获取的 FinQA JSON 建立本地只读证据库，不下载或分发数据。"""

import argparse
import json
from pathlib import Path
import re
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from app.repositories.db_models import CompanyAlias, FinancialReport
from app.repositories.finqa import normalize_company

_COMPANY_NAMES = {
    "AAPL": ("Apple", "Apple Inc", "苹果"),
    "AMZN": ("Amazon", "Amazon.com", "亚马逊"),
    "MSFT": ("Microsoft", "微软"),
    "GOOG": ("Google", "谷歌"),
}


def read_reports(path: Path, split: str) -> list[FinancialReport]:
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(f"{path.name} 顶层必须是数组")
    reports = []
    seen = set()
    for index, item in enumerate(records):
        if not isinstance(item, dict):
            raise ValueError(f"{path.name} 第 {index + 1} 项不是对象")
        source_id = item.get("id", "")
        if not isinstance(source_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]+/\d{4}/[A-Za-z0-9._-]+\.pdf-\d+", source_id):
            raise ValueError(f"{path.name} 第 {index + 1} 项报告 ID 无效")
        if source_id in seen:
            raise ValueError(f"{path.name} 存在重复报告 ID")
        seen.add(source_id)
        for key in ("pre_text", "post_text"):
            if not isinstance(item.get(key), list) or not all(isinstance(v, str) for v in item[key]):
                raise ValueError(f"{path.name} 第 {index + 1} 项 {key} 必须是字符串数组")
        table = item.get("table")
        if not isinstance(table, list) or not all(isinstance(row, list) and all(isinstance(c, str) for c in row) for row in table):
            raise ValueError(f"{path.name} 第 {index + 1} 项 table 必须是二维字符串数组")
        reports.append(FinancialReport(
            id=f"{split}:{source_id}", source_id=source_id, split=split,
            pre_text=item["pre_text"], post_text=item["post_text"], table=table,
        ))
    return reports


def import_reports(source: Path, destination: Path) -> int:
    """先验证所有输入，再事务性写入；重复执行更新相同来源，不清空已有数据。"""
    reports = []
    for split in ("train", "dev", "test"):
        path = source / f"{split}.json"
        if path.exists():
            reports.extend(read_reports(path, split))
    if not reports:
        raise ValueError("输入目录需要至少一个非空的 train.json、dev.json 或 test.json")
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(URL.create("sqlite", database=str(destination)))
    try:
        FinancialReport.__table__.create(engine, checkfirst=True)
        CompanyAlias.__table__.create(engine, checkfirst=True)
        with Session(engine) as db, db.begin():
            for report in reports:
                db.merge(report)
            for ticker in {r.source_id.split("/")[0] for r in reports}:
                for alias in (ticker, *_COMPANY_NAMES.get(ticker, ())):
                    db.merge(CompanyAlias(alias=normalize_company(alias), ticker=ticker, source="finqa-import"))
    finally:
        engine.dispose()
    return len(reports)


def main() -> None:
    parser = argparse.ArgumentParser(description="导入自行获取的 FinQA 公开 JSON，不包含题目标签或会话数据")
    parser.add_argument("--source-dir", type=Path, required=True, help="包含 train/dev/test.json 的目录")
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "data" / "agent.db")
    args = parser.parse_args()
    try:
        count = import_reports(args.source_dir, args.database)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"导入失败：{exc}\n")
    print(f"已导入 {count} 条财报片段；未导入会话、日志或问题答案标签。")


if __name__ == "__main__":
    main()
