import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("weread.app:create_app", factory=True, host=os.environ.get("WEREAD_HOST", "127.0.0.1"), port=int(os.environ.get("WEREAD_PORT", "8000")), workers=1)
