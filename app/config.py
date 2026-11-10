"""Travel Payment Gateway & Back-Office System Configuration."""

import os
from pydantic import BaseModel


class Settings(BaseModel):
    PROJECT_NAME: str = "MOST Travel Payment Gateway"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./travel_payments.db")


settings = Settings()
