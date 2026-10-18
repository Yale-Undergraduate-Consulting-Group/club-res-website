"""Keep OAuth codes and bearer share/invitation tokens out of access logs."""
import logging
from urllib.parse import urlsplit

class RedactAccessPath(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Uvicorn access records: client, method, path-with-query, version, status.
        if isinstance(record.args, tuple) and len(record.args) == 5:
            values = list(record.args)
            path = urlsplit(str(values[2])).path
            if path.startswith('/api/workspace/shared/'):
                path = '/api/workspace/shared/[redacted]'
            values[2] = path
            record.args = tuple(values)
        return True
