"""
database.py — MongoDB Layer
All persistent storage: record group, target groups, audio settings, sudo users.
Uses a write-through in-memory cache for fast reads.
"""
import threading
from datetime import datetime
from typing import Dict, List, Optional

from pymongo import MongoClient, ASCENDING
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
from loguru import logger


class Database:
    """MongoDB operations with a write-through in-memory cache."""

    DEFAULT_AUDIO: Dict = {
        "volume": 100,    # internal 0-200 (user-facing 0-60)
        "bass": 0,         # 0-60
        "treble": 0,       # 0-60
        "clarity": 0,      # 0-60
        "gain": 0,         # -15 to +15 dB
        "muted": False,
    }

    def __init__(self, uri: str):
        self._uri = uri
        self._client: Optional[MongoClient] = None
        self._db = None
        self._cache: Dict = {}
        self._lock = threading.RLock()
        self._connected = False

    # ------------------------------------------------------------------ #
    #  Connection                                                          #
    # ------------------------------------------------------------------ #

    def connect(self) -> bool:
        try:
            self._client = MongoClient(
                self._uri,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000,
                socketTimeoutMS=10000,
                maxPoolSize=10,
                retryWrites=True,
                w="majority",
            )
            self._client.admin.command("ping")
            self._db = self._client["vcbridge"]
            self._ensure_indexes()
            self._connected = True
            logger.success("MongoDB connected successfully")
            return True
        except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
            logger.error(f"MongoDB connection failed: {exc}")
            return False
        except Exception as exc:
            logger.error(f"MongoDB unexpected error: {exc}")
            return False

    def _ensure_indexes(self):
        self._db["settings"].create_index([("key", ASCENDING)], unique=True)
        self._db["target_groups"].create_index([("chat_id", ASCENDING)], unique=True)
        self._db["sudo_users"].create_index([("user_id", ASCENDING)], unique=True)

    def _col(self, name: str):
        if not self._connected:
            raise RuntimeError("Database.connect() must be called first")
        return self._db[name]

    # ------------------------------------------------------------------ #
    #  Generic key-value settings                                          #
    # ------------------------------------------------------------------ #

    def get_setting(self, key: str, default=None):
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        try:
            doc = self._col("settings").find_one({"key": key})
            val = doc["value"] if doc else default
            with self._lock:
                self._cache[key] = val
            return val
        except Exception as exc:
            logger.error(f"get_setting({key!r}): {exc}")
            return default

    def set_setting(self, key: str, value) -> bool:
        try:
            self._col("settings").update_one(
                {"key": key},
                {"$set": {"key": key, "value": value, "updated_at": datetime.utcnow()}},
                upsert=True,
            )
            with self._lock:
                self._cache[key] = value
            return True
        except Exception as exc:
            logger.error(f"set_setting({key!r}): {exc}")
            return False

    # ------------------------------------------------------------------ #
    #  Target groups                                                       #
    # ------------------------------------------------------------------ #

    _TGT_KEY = "__tgt_groups"

    def get_target_groups(self) -> List[int]:
        with self._lock:
            if self._TGT_KEY in self._cache:
                return list(self._cache[self._TGT_KEY])
        try:
            docs = list(self._col("target_groups").find({}, {"chat_id": 1, "_id": 0}))
            groups = [d["chat_id"] for d in docs]
            with self._lock:
                self._cache[self._TGT_KEY] = set(groups)
            return groups
        except Exception as exc:
            logger.error(f"get_target_groups: {exc}")
            return []

    def add_target_group(self, chat_id: int) -> bool:
        try:
            self._col("target_groups").update_one(
                {"chat_id": chat_id},
                {"$set": {"chat_id": chat_id, "added_at": datetime.utcnow()}},
                upsert=True,
            )
            with self._lock:
                self._cache.pop(self._TGT_KEY, None)
            return True
        except Exception as exc:
            logger.error(f"add_target_group({chat_id}): {exc}")
            return False

    def remove_target_group(self, chat_id: int) -> bool:
        try:
            self._col("target_groups").delete_one({"chat_id": chat_id})
            with self._lock:
                self._cache.pop(self._TGT_KEY, None)
            return True
        except Exception as exc:
            logger.error(f"remove_target_group({chat_id}): {exc}")
            return False

    def clear_target_groups(self) -> bool:
        try:
            self._col("target_groups").delete_many({})
            with self._lock:
                self._cache.pop(self._TGT_KEY, None)
            return True
        except Exception as exc:
            logger.error(f"clear_target_groups: {exc}")
            return False

    # ------------------------------------------------------------------ #
    #  Audio settings                                                      #
    # ------------------------------------------------------------------ #

    def get_audio_settings(self) -> dict:
        stored = self.get_setting("audio_settings", {})
        return {**self.DEFAULT_AUDIO, **(stored or {})}

    def update_audio_settings(self, **kwargs) -> bool:
        current = self.get_audio_settings()
        current.update(kwargs)
        return self.set_setting("audio_settings", current)

    # ------------------------------------------------------------------ #
    #  Record group                                                        #
    # ------------------------------------------------------------------ #

    def get_record_group(self, fallback: int = 0) -> int:
        return self.get_setting("record_group", fallback) or fallback

    def set_record_group(self, chat_id: int) -> bool:
        return self.set_setting("record_group", chat_id)

    # ------------------------------------------------------------------ #
    #  Sudo users (persisted)                                             #
    # ------------------------------------------------------------------ #

    def get_sudo_users(self) -> List[int]:
        try:
            docs = list(self._col("sudo_users").find({}, {"user_id": 1, "_id": 0}))
            return [d["user_id"] for d in docs]
        except Exception as exc:
            logger.error(f"get_sudo_users: {exc}")
            return []

    def add_sudo_user(self, user_id: int) -> bool:
        try:
            self._col("sudo_users").update_one(
                {"user_id": user_id},
                {"$set": {"user_id": user_id, "added_at": datetime.utcnow()}},
                upsert=True,
            )
            return True
        except Exception as exc:
            logger.error(f"add_sudo_user({user_id}): {exc}")
            return False

    def remove_sudo_user(self, user_id: int) -> bool:
        try:
            self._col("sudo_users").delete_one({"user_id": user_id})
            return True
        except Exception as exc:
            logger.error(f"remove_sudo_user({user_id}): {exc}")
            return False

    # ------------------------------------------------------------------ #
    #  Cache utilities                                                     #
    # ------------------------------------------------------------------ #

    def invalidate(self, key: str = None):
        with self._lock:
            if key:
                self._cache.pop(key, None)
            else:
                self._cache.clear()

    @property
    def connected(self) -> bool:
        return self._connected
