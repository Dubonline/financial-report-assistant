"""数据导入正常、重复执行与无效输入保护；只使用临时合成数据。"""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.import_finqa import import_reports
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from app.repositories.db_models import FinancialReport, CompanyAlias


class ImportTests(unittest.TestCase):
    def test_import_idempotence_and_invalid_input(self):
        with TemporaryDirectory() as temp:
            source = Path(temp)
            destination = source / "output" / "agent.db"
            data = [{"id": "EXAMPLE/2014/page_1.pdf-0", "pre_text": ["Synthetic fixture in millions"],
                     "post_text": [], "table": [["", "2014", "2013"], ["revenue", "25", "20"]]}]
            (source / "dev.json").write_text(json.dumps(data))
            self.assertEqual(import_reports(source, destination), 1)
            data[0]["table"][1][1] = "30"
            (source / "dev.json").write_text(json.dumps(data))
            self.assertEqual(import_reports(source, destination), 1)
            engine = create_engine(f"sqlite:///{destination}")
            with Session(engine) as db:
                self.assertEqual(db.scalar(select(func.count()).select_from(FinancialReport)), 1)
                report = db.scalar(select(FinancialReport))
                self.assertEqual(report.table[1][1], "30")
                self.assertEqual(db.scalar(select(CompanyAlias)).alias, "example")
            data[0]["table"] = "invalid"
            (source / "dev.json").write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                import_reports(source, destination)
            with Session(engine) as db:
                self.assertEqual(db.scalar(select(FinancialReport)).table[1][1], "30")
            engine.dispose()

    def test_empty_input(self):
        with TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                import_reports(Path(temp), Path(temp) / "agent.db")
            self.assertFalse((Path(temp) / "agent.db").exists())


if __name__ == "__main__":
    unittest.main()
