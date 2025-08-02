#!/bin/bash
# MIDI Files Backup Script
# Syncs MIDI recordings to NFS server

# Configuration
SOURCE_DIR="/home/chris/midi_recordings"
NFS_SERVER="192.168.1.100"  # Change to your NFS server IP
NFS_PATH="/mnt/nfs/midi_backup"
LOG_FILE="/var/log/midi_recorder/backup.log"

# Function to log messages
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Check if source directory exists
if [ ! -d "$SOURCE_DIR" ]; then
    log "ERROR: Source directory $SOURCE_DIR does not exist"
    exit 1
fi

# Check network connectivity
ping -c 1 -W 5 "$NFS_SERVER" > /dev/null 2>&1
if [ $? -ne 0 ]; then
    log "ERROR: Cannot reach NFS server at $NFS_SERVER"
    exit 1
fi

log "Starting MIDI backup to $NFS_SERVER:$NFS_PATH"

# Perform rsync with retry logic
MAX_RETRIES=3
RETRY_COUNT=0

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    rsync -avz --delete-after \
          --exclude="*.tmp" \
          --exclude="*.lock" \
          --log-file="$LOG_FILE" \
          "$SOURCE_DIR/" \
          "$NFS_SERVER:$NFS_PATH/"
    
    if [ $? -eq 0 ]; then
        log "Backup completed successfully"
        
        # Calculate statistics
        FILE_COUNT=$(find "$SOURCE_DIR" -name "*.mid" | wc -l)
        TOTAL_SIZE=$(du -sh "$SOURCE_DIR" | cut -f1)
        log "Backed up $FILE_COUNT MIDI files, total size: $TOTAL_SIZE"
        
        exit 0
    else
        RETRY_COUNT=$((RETRY_COUNT + 1))
        log "WARNING: Backup failed, attempt $RETRY_COUNT of $MAX_RETRIES"
        sleep 30
    fi
done

log "ERROR: Backup failed after $MAX_RETRIES attempts"
exit 1
