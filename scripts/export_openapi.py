import json
import sys
from pathlib import Path

# Add the project root to the path so we can import maxwell_daemon
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import MagicMock

from maxwell_daemon.api.server import create_app


def main() -> None:
    daemon_mock = MagicMock()
    app = create_app(daemon_mock)
    openapi_schema = app.openapi()
    out_path = Path("docs/reference/openapi.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(openapi_schema, f, indent=2)
    print(f"Exported OpenAPI schema to {out_path}")


if __name__ == "__main__":
    main()
