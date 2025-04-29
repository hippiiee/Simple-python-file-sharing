#!/bin/bash

echo "Starting file sharing server"
echo "Maximum file size: $(python -c "print('%.2f' % (int('$FILE_SHARE_MAX_SIZE') / 1024 / 1024 / 1024))") GB"
echo "File retention period: $FILE_SHARE_RETENTION_DAYS days"
echo "Scheduled cleanup time: $FILE_SHARE_CLEANUP_TIME"

uvicorn main:app --host 0.0.0.0 --port 8000

