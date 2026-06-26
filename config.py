"""
config.py — Configuration Manager
Loads and validates all environment variables from .env file.
"""
import os
from typing import List
from dotenv import load_dotenv


class Config:
    """Centralised configuration loaded from environment / .env file."""

    def __init__(self):
        load_dotenv()

        self.API_ID: int = int(os.getenv("API_ID", "0"))
        self.API_HASH: str = os.getenv("API_HASH", "")
        self.BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
        self.OWNER_ID: int = int(os.getenv("OWNER_ID", "0"))
        self.MONGO_DB_URI: str = os.getenv("MONGO_DB_URI", "")
        self.STRING_SESSION: str = os.getenv("STRING_SESSION", "")
        self.RECORD_GROUP: int = int(os.getenv("RECORD_GROUP", "0"))
        self.SUDO_USERS: List[int] = [
            int(x.strip())
            for x in os.getenv("SUDO_USERS", "").split(",")
            if x.strip().lstrip("-").isdigit()
        ]
        self._validate()

    def _validate(self):
        required = {
            "API_ID": self.API_ID,
            "API_HASH": self.API_HASH,
            "BOT_TOKEN": self.BOT_TOKEN,
            "OWNER_ID": self.OWNER_ID,
            "MONGO_DB_URI": self.MONGO_DB_URI,
            "STRING_SESSION": self.STRING_SESSION,
            "RECORD_GROUP": self.RECORD_GROUP,
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise EnvironmentError(
                f"[Config] Missing required env vars: {', '.join(missing)}\n"
                f"Please copy .env.example -> .env and fill in all values."
            )

    def is_authorized(self, user_id: int) -> bool:
        """True if user is owner or in sudo list."""
        return user_id == self.OWNER_ID or user_id in self.SUDO_USERS

    def is_owner(self, user_id: int) -> bool:
        """True only if user is the owner."""
        return user_id == self.OWNER_ID

    def add_sudo(self, user_id: int):
        if user_id not in self.SUDO_USERS:
            self.SUDO_USERS.append(user_id)

    def remove_sudo(self, user_id: int):
        if user_id in self.SUDO_USERS:
            self.SUDO_USERS.remove(user_id)
