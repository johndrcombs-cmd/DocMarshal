import json
from pathlib import Path

from dotdocs.database import import_fleet_workbook

ROOT = Path(__file__).resolve().parent
config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
stats = import_fleet_workbook(
    config["fleet_workbook"],
    config["fleet_database"],
    manual_assets_path=config.get("manual_assets_registry"),
)
report_path = ROOT / "data" / "import_report.json"
report_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")
print(json.dumps({"database": config["fleet_database"], "report": str(report_path), **stats}, indent=2))
