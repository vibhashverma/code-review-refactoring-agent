"""Write the FastAPI OpenAPI schema to openapi.json."""

import json
from pathlib import Path

from main import app

target = Path(__file__).resolve().parent.parent / "openapi.json"
target.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")
print(f"wrote {target}")
