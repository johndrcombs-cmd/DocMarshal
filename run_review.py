import json
from datetime import datetime
from pathlib import Path

from dotdocs.runner import process_inbox

ROOT = Path(__file__).resolve().parent
config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
report_name = "DOT_review_" + datetime.now().strftime("%Y-%m-%d_%H%M%S")
summary = process_inbox(
    config["scan_incoming"],
    config["scan_review"],
    config["fleet_database"],
    config["unit_folders_root"],
    farm_asset_folders_root=config["farm_asset_folders_root"],
    report_name=report_name,
)
print(json.dumps(summary, indent=2))
