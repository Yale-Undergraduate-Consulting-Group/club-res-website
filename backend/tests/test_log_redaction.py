"""Access log filtering never retains URL bearer secrets."""
import logging
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.log_redaction import RedactAccessPath
for path in ['/api/auth/google?invitation=SECRET', '/api/auth/google/callback?code=SECRET&state=other', '/api/workspace/shared/SECRET']:
    record=logging.LogRecord('uvicorn.access',20,'',0,'%s %s %s %s %s',('client','GET',path,'1.1',200),None)
    assert RedactAccessPath().filter(record)
    assert 'SECRET' not in record.getMessage()
record=logging.LogRecord('uvicorn.access',20,'',0,'ordinary message',(),None)
assert RedactAccessPath().filter(record) and record.getMessage()=='ordinary message'
print('access log redaction: ok')
