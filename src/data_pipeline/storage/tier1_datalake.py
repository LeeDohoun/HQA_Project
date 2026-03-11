import os
import json
import asyncio
from datetime import datetime
from typing import List, Dict, Any
import aiofiles

class DatalakeStorage:
    def __init__(self, base_path: str = "./data_lake"):
        self.base_path = base_path
        os.makedirs(self.base_path, exist_ok=True)

    def _get_partitioned_path(self, source_type: str, date: datetime) -> str:
        """S3 Hive-style partitioning for local files"""
        year = date.strftime("%Y")
        month = date.strftime("%m")
        day = date.strftime("%d")
        
        dir_path = os.path.join(
            self.base_path,
            f"source_type={source_type}",
            f"year={year}",
            f"month={month}",
            f"day={day}"
        )
        os.makedirs(dir_path, exist_ok=True)
        return dir_path

    async def save_jsonl(self, source_type: str, records: List[Dict[str, Any]]):
        """Save a list of parsed dictionaries as JSONL in the partitioned path"""
        if not records:
            return

        now = datetime.utcnow()
        dir_path = self._get_partitioned_path(source_type, now)
        
        # Unique filename using timestamp
        file_name = f"data_{now.strftime('%H%M%S')}.jsonl"
        full_path = os.path.join(dir_path, file_name)

        async with aiofiles.open(full_path, mode="a", encoding="utf-8") as f:
            for record in records:
                line = json.dumps(record, ensure_ascii=False)
                await f.write(line + "\n")
                
        # Return path for Queue message passing
        return full_path
