#!/bin/bash
# Installation script for MIDI Recorder on Raspberry Pi

set -e

echo "Installing MIDI Recorder..."

# Update system
echo "Updating system packages..."
sudo apt-get update
sudo apt-get upgrade -y

# Install dependencies
echo "Installing dependencies..."
sudo apt-get install -y python3-pip python3-dev libasound2-dev libjack-dev rsync

# Install Python packages
echo "Installing Python packages..."
/home/chris/midi_recorder_venv/bin/pip3 install mido python-rtmidi psutil

# Create directories
echo "Creating directories..."
mkdir -p /home/chris/midi_recordings
sudo mkdir -p /var/log/midi_recorder
sudo chown chris:chris /var/log/midi_recorder

# Copy main script
echo "Installing MIDI recorder script..."
cp midi_recorder.py /home/chris/
chmod +x /home/chris/midi_recorder.py

# Copy backup script
echo "Installing backup script..."
cp midi_backup.sh /home/chris/
chmod +x /home/chris/midi_backup.sh

# Install systemd service
echo "Installing systemd service..."
sudo cp midi_recorder.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable midi_recorder.service

# Create logrotate config
echo "Setting up log rotation..."
sudo tee /etc/logrotate.d/midi_recorder > /dev/null <<EOF
/var/log/midi_recorder/*.log {
    weekly
    rotate 4
    compress
    delaycompress
    missingok
    notifempty
    create 644 chris chris
}
EOF

# Install crontab
echo "Installing crontab entries..."
crontab -l > mycron 2>/dev/null || true
cat midi_crontab.txt >> mycron
crontab mycron
rm mycron

# Configure NFS server
read -p "Enter your NFS server IP address: " NFS_IP
sed -i "s/192.168.1.100/$NFS_IP/g" /home/chris/midi_backup.sh

read -p "Enter your NFS backup path (e.g., /mnt/nfs/midi_backup): " NFS_PATH
sed -i "s|/mnt/nfs/midi_backup|$NFS_PATH|g" /home/chris/midi_backup.sh

# Enable I2C for potential future display
echo "Enabling I2C..."
sudo raspi-config nonint do_i2c 0

# Optimize for headless operation
echo "Optimizing for headless operation..."
sudo systemctl disable bluetooth
sudo systemctl disable hciuart
sudo systemctl disable avahi-daemon
sudo systemctl disable triggerhappy

# Set CPU governor for power saving
echo "Setting up power management..."
echo 'echo "ondemand" | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor' | sudo tee /etc/rc.local
sudo chmod +x /etc/rc.local

# Start the service
echo "Starting MIDI Recorder service..."
sudo systemctl start midi_recorder.service

echo "Installation complete!"
echo ""
echo "To check service status: sudo systemctl status midi_recorder"
echo "To view logs: sudo journalctl -u midi_recorder -f"
echo "To test backup: /home/pi/midi_backup.sh"
echo ""
echo "Make sure to:"
echo "1. Connect your piano's MIDI output to the Raspberry Pi"
echo "2. Ensure your NFS server is accessible at $NFS_IP"
echo "3. Create the backup directory on your NFS server: $NFS_PATH"
