"""
JSON File Publisher
===================

Writes collected data to JSON files in compact format.

Features:
    - Compact JSON format (no line breaks, tabs)
    - Same data format as MQTT publisher
    - File rotation support
    - Append or overwrite modes

Output Format (per line):
    {"plc_id":1,"timestamp":"2024-01-01T12:00:00.000","count":10,"data":[...]}

Example YAML config:
    publisher:
      json_file:
        enabled: true
        file_path: "data/output.json"
        mode: "append"
        max_size_mb: 10
        backup_count: 5
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import BasePublisher
from ..core.interfaces import ProcessedData
from ..core.config import JsonFileConfig, PublisherConfig
from ..utils.logging import LoggerFactory


logger = LoggerFactory.get_publish_logger()


class JsonFilePublisher(BasePublisher):
    """
    JSON File Publisher.

    Writes collected data to JSON files in compact format (no whitespace).
    Each publish batch is written as a single line in the output file.
    """

    def __init__(
        self,
        name: str,
        json_config: JsonFileConfig,
        publisher_config: Optional[PublisherConfig] = None,
    ):
        """
        Initialize JSON file publisher.

        Args:
            name: Publisher name
            json_config: JSON file configuration
            publisher_config: General publisher configuration
        """
        if publisher_config is None:
            publisher_config = PublisherConfig()

        super().__init__(
            name=name,
            config=publisher_config,
        )

        self._json_config = json_config
        self._file_path = Path(json_config.file_path)
        self._mode = json_config.mode
        self._max_size_bytes = json_config.max_size_mb * 1024 * 1024
        self._backup_count = json_config.backup_count
        self._file_handle = None
        self._total_written = 0

    async def _do_connect(self) -> bool:
        """
        Open file for writing.

        Returns:
            True if file opened successfully
        """
        try:
            # Ensure directory exists
            self._file_path.parent.mkdir(parents=True, exist_ok=True)

            # Check if rotation is needed
            if self._mode == 'append' and self._file_path.exists():
                self._check_rotation()

            # Open file
            mode = 'a' if self._mode == 'append' else 'w'
            self._file_handle = open(self._file_path, mode, encoding='utf-8')

            logger.info(
                f"[{self._name}] JSON file publisher ready - "
                f"file={self._file_path}, mode={self._mode}"
            )
            return True

        except Exception as e:
            logger.error(f"[{self._name}] Failed to open file: {e}")
            return False

    async def _do_disconnect(self) -> None:
        """Close file handle."""
        if self._file_handle:
            try:
                self._file_handle.close()
                self._file_handle = None
                logger.info(
                    f"[{self._name}] JSON file publisher stopped. "
                    f"Total written: {self._total_written} records"
                )
            except Exception as e:
                logger.error(f"[{self._name}] Error closing file: {e}")

    async def _do_publish(self, data: List[ProcessedData]) -> bool:
        """
        Write data to JSON file.

        Args:
            data: List of processed data to write

        Returns:
            True if write succeeded
        """
        if not data:
            return True

        if not self._file_handle:
            logger.error(f"[{self._name}] File handle not initialized")
            return False

        try:
            # Check rotation before writing (append mode)
            if self._mode == 'append':
                self._check_rotation()

            # Create batch message (same format as MQTT)
            message = self._create_batch_message(data)

            # Write as compact JSON (no whitespace) + newline
            json_line = json.dumps(message, ensure_ascii=False, separators=(',', ':'))
            self._file_handle.write(json_line + '\n')
            self._file_handle.flush()

            self._total_written += len(data)

            logger.verbose(
                f"[{self._name}] Written {len(data)} records to {self._file_path.name}"
            )
            return True

        except Exception as e:
            logger.error(f"[{self._name}] Write error: {e}")
            return False

    def _create_batch_message(self, data: List[ProcessedData]) -> Dict[str, Any]:
        """
        Create batch message in same format as MQTT publisher.

        Args:
            data: Data list

        Returns:
            JSON-serializable dictionary
        """
        plc_id = data[0].plc_id if data else 0

        return {
            "plc_id": plc_id,
            "timestamp": datetime.now().isoformat(timespec='milliseconds'),
            "count": len(data),
            "data": [item.to_dict() for item in data]
        }

    def _check_rotation(self) -> None:
        """Check if file rotation is needed and perform rotation."""
        if not self._file_path.exists():
            return

        try:
            current_size = self._file_path.stat().st_size
            if current_size < self._max_size_bytes:
                return

            # Close current file if open
            if self._file_handle:
                self._file_handle.close()
                self._file_handle = None

            # Rotate files
            self._rotate_files()

            # Reopen file
            self._file_handle = open(self._file_path, 'a', encoding='utf-8')

            logger.info(
                f"[{self._name}] File rotated - "
                f"size was {current_size / 1024 / 1024:.2f}MB"
            )

        except Exception as e:
            logger.error(f"[{self._name}] Rotation error: {e}")

    def _rotate_files(self) -> None:
        """Rotate backup files."""
        # Remove oldest backup
        oldest = Path(f"{self._file_path}.{self._backup_count}")
        if oldest.exists():
            oldest.unlink()

        # Shift existing backups
        for i in range(self._backup_count - 1, 0, -1):
            src = Path(f"{self._file_path}.{i}")
            dst = Path(f"{self._file_path}.{i + 1}")
            if src.exists():
                src.rename(dst)

        # Move current file to .1
        if self._file_path.exists():
            self._file_path.rename(Path(f"{self._file_path}.1"))

    def get_stats(self) -> Dict[str, Any]:
        """Get publisher statistics."""
        stats = super().get_stats()
        stats.update({
            "file_path": str(self._file_path),
            "mode": self._mode,
            "total_written": self._total_written,
        })

        # Add file size if exists
        if self._file_path.exists():
            try:
                stats["file_size_mb"] = self._file_path.stat().st_size / 1024 / 1024
            except Exception as e:
                logger.verbose(f"[{self._name}] Unable to get file size: {e}")

        return stats
