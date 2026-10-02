"""Start the Private Mortgage Manager web app.

    python run.py            # http://127.0.0.1:5000
"""
import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("PMM_HOST", "127.0.0.1")
    port = int(os.environ.get("PMM_PORT", "5000"))
    app.run(host=host, port=port, debug=os.environ.get("PMM_DEBUG") == "1")
