#!/usr/bin/env python3
"""
MIDI Recorder for Raspberry Pi
Records MIDI input from piano to organized file structure
"""

import os
import sys
import time
import signal
import logging
import threading
import queue
from datetime import datetime
from pathlib import Path
from sdnotify import SystemdNotifier
import mido
from mido import MidiFile, MidiTrack, Message, MetaMessage
import psutil

# Configuration
BASE_DIR = Path("/home/chris/midi_recordings")
SESSION_TIMEOUT = 5
IDLE_CHECK_INTERVAL = 5  # Check less frequently when idle
DEFAULT_TEMPO = 500000  # microseconds per beat (120 BPM)
TICKS_PER_BEAT = 480
LOW_BLACK_NOTE = 22   # A#0/Bb0
HIGH_BLACK_NOTE = 106  # A#7/Bb7
SHORTCUT_TIMEOUT = 1.0  # seconds between shortcut presses

notifier = SystemdNotifier()
notifier.notify("READY=1")

class MidiRecorder:
    def __init__(self):
        self.recording = False
        self.last_activity = None
        self.current_file = None
        self.current_track = None
        self.session_start_time = None
        self.first_message_time = None
        self.last_message_time = None
        self.midi_port = None
        self.running = True
        self.in_low_power = False
        self.message_queue = queue.Queue()
        self.low_note_state = {'count': 0, 'buffer': [], 'last_time': 0}
        self.high_note_state = {'count': 0, 'buffer': [], 'last_time': 0}
        
        # Setup logging
        self.setup_logging()
        
        # Setup signal handlers
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)
        
    def setup_logging(self):
        """Configure logging"""
        log_dir = Path("/var/log/midi_recorder")
        log_dir.mkdir(exist_ok=True)
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_dir / "midi_recorder.log"),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)
        
    def signal_handler(self, sig, frame):
        """Handle shutdown signals gracefully"""
        self.logger.info("Shutdown signal received")
        self.running = False
        self.stop_recording()
        sys.exit(0)
        
    def midi_callback(self, msg):
        """Callback for incoming MIDI messages"""
        # Get timestamp as close to receipt as possible
        timestamp = time.perf_counter()
        # Add message to queue with high-precision timestamp
        self.message_queue.put((msg, timestamp))
        
    def find_midi_port(self):
        """Find and connect to MIDI input port"""
        try:
            ports = mido.get_input_names()
            if not ports:
                self.logger.warning("No MIDI ports found")
                return None
                
            # Try to find a port with 'piano' in the name first
            port_name = None
            for port in ports:
                if 'pia' in port.lower():
                    port_name = port
                    break
                    
            # Otherwise use the first available port
            if not port_name:
                port_name = ports[0]
                
            self.logger.info(f"Using MIDI port: {port_name}")
            
            # Open port with callback
            # Note: mido with python-rtmidi backend provides best timing
            return mido.open_input(port_name, callback=self.midi_callback)
            
        except Exception as e:
            self.logger.error(f"Error opening MIDI port: {e}")
            return None
            
    def create_session_path(self):
        """Create directory structure and return session file path"""
        now = datetime.now()
        year = now.strftime("%Y")
        month = now.strftime("%m-%B")
        day = now.strftime("%d")
        
        dir_path = BASE_DIR / year / month / day
        dir_path.mkdir(parents=True, exist_ok=True)
        
        # Create session filename with timestamp
        session_name = now.strftime("session_%H%M%S.mid")
        return dir_path / session_name
        
    def start_recording(self):
        """Start a new recording session"""
        if self.recording:
            return
            
        self.session_start_time = time.perf_counter()
        self.last_activity = time.time()
        self.first_message_time = None
        self.last_message_time = None
        
        # Create new MIDI file with proper settings
        self.current_file = MidiFile(ticks_per_beat=TICKS_PER_BEAT)
        self.current_track = MidiTrack()
        
        # Add tempo meta message
        tempo_msg = MetaMessage('set_tempo', tempo=DEFAULT_TEMPO, time=0)
        self.current_track.append(tempo_msg)
        
        self.current_file.tracks.append(self.current_track)
        
        self.recording = True
        self.in_low_power = False
        self.logger.info("Started new recording session")
        
    def stop_recording(self, suffix="", skip_queue=False, skip_buffer=False):
        """Stop recording and save the file"""
        if not self.recording:
            return

        self.recording = False

        if skip_queue:
            with self.message_queue.mutex:
                self.message_queue.queue.clear()
        else:
            self.process_message_queue()

        if not skip_buffer:
            self.flush_shortcut_buffers()
        else:
            self.low_note_state = {'count': 0, 'buffer': [], 'last_time': 0}
            self.high_note_state = {'count': 0, 'buffer': [], 'last_time': 0}

        if self.current_file and len(self.current_track) > 1:  # More than just tempo
            file_path = self.create_session_path()
            if suffix:
                file_path = file_path.with_name(file_path.stem + suffix + file_path.suffix)
            self.current_file.save(file_path)
            self.logger.info(f"Saved recording to {file_path}")

            duration = self.last_message_time - self.first_message_time if self.first_message_time else 0
            self.logger.info(f"Session duration: {duration:.1f} seconds, "
                           f"Messages: {len(self.current_track) - 1}")

        self.current_file = None
        self.current_track = None
        
    def enter_low_power_mode(self):
        """Reduce resource usage when idle"""
        if not self.in_low_power:
            self.in_low_power = True
            self.logger.info("Entering low power mode")
            
            # Reduce CPU governor if available (Pi specific)
            try:
                os.system("echo powersave | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor > /dev/null 2>&1")
            except:
                pass
                
    def exit_low_power_mode(self):
        """Return to normal operation"""
        if self.in_low_power:
            self.in_low_power = False
            self.logger.info("Exiting low power mode")
            
            # Restore CPU governor
            try:
                os.system("echo ondemand | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor > /dev/null 2>&1")
            except:
                pass
                
    def write_message(self, msg, msg_timestamp):
        """Write a MIDI message to the current track with timing"""
        if self.current_track is not None:
            if self.last_message_time is None:
                delta_seconds = 0
            else:
                delta_seconds = msg_timestamp - self.last_message_time

            beats_per_second = 1000000.0 / DEFAULT_TEMPO
            delta_ticks = int(delta_seconds * TICKS_PER_BEAT * beats_per_second)
            delta_ticks = max(0, delta_ticks)

            msg_copy = msg.copy()
            msg_copy.time = delta_ticks
            self.current_track.append(msg_copy)
            self.last_message_time = msg_timestamp

    def flush_buffer(self, state):
        for m, ts in state['buffer']:
            self.write_message(m, ts)
        state['buffer'].clear()
        state['count'] = 0
        state['last_time'] = 0

    def flush_shortcut_buffers(self):
        self.flush_buffer(self.low_note_state)
        self.flush_buffer(self.high_note_state)

    def handle_shortcuts(self, msg, msg_timestamp):
        """Handle bookmark and session end shortcuts"""
        if msg.type in ['note_on', 'note_off']:
            for note, state, suffix in [
                (LOW_BLACK_NOTE, self.low_note_state, ''),
                (HIGH_BLACK_NOTE, self.high_note_state, '-bookmark')
            ]:
                if msg.note == note:
                    if msg.type == 'note_on' and state['count'] > 0 and \
                            msg_timestamp - state['last_time'] > SHORTCUT_TIMEOUT:
                        self.flush_buffer(state)
                    state['buffer'].append((msg, msg_timestamp))
                    if msg.type == 'note_on':
                        state['count'] += 1
                        state['last_time'] = msg_timestamp
                        if state['count'] >= 3:
                            with self.message_queue.mutex:
                                self.message_queue.queue.clear()
                            self.stop_recording(suffix=suffix,
                                                skip_queue=True,
                                                skip_buffer=True)
                            return True
                    return True

            if self.low_note_state['count'] > 0:
                self.flush_buffer(self.low_note_state)
            if self.high_note_state['count'] > 0:
                self.flush_buffer(self.high_note_state)
            return False

        if self.low_note_state['count'] > 0:
            self.flush_buffer(self.low_note_state)
        if self.high_note_state['count'] > 0:
            self.flush_buffer(self.high_note_state)
        return False

    def process_midi_message(self, msg, msg_timestamp):
        """Process incoming MIDI message with accurate timing"""
        if msg.type in ['clock', 'active_sensing']:
            return

        self.last_activity = time.time()

        if not self.recording:
            self.start_recording()
            self.first_message_time = msg_timestamp

        if self.in_low_power:
            self.exit_low_power_mode()

        if not self.handle_shortcuts(msg, msg_timestamp):
            self.write_message(msg, msg_timestamp)
            
    def process_message_queue(self):
        """Process all messages in the queue"""
        messages_processed = 0
        
        while not self.message_queue.empty():
            try:
                msg, timestamp = self.message_queue.get_nowait()
                self.process_midi_message(msg, timestamp)
                messages_processed += 1
            except queue.Empty:
                break
                
        return messages_processed
        
    def check_session_timeout(self):
        """Check if session has timed out"""
        if self.recording and self.last_activity:
            if time.time() - self.last_activity > SESSION_TIMEOUT:
                self.logger.info("Session timeout - stopping recording")
                self.stop_recording()
                self.enter_low_power_mode()
                
    def run(self):
        """Main loop"""
        self.logger.info("MIDI Recorder starting...")
        self.logger.info(f"Using MIDI backend: {mido.backend.name}")
        
        # Start a separate thread for port monitoring
        port_thread = threading.Thread(target=self.port_monitor_thread)
        port_thread.daemon = True
        port_thread.start()
        
        last_queue_check = time.perf_counter()
        
        while self.running:
            try:
                # Process messages more frequently when active
                current_time = time.perf_counter()
                
                # Always process pending messages
                messages = self.process_message_queue()
                
                # Check session timeout periodically
                if current_time - last_queue_check > 1.0:
                    self.check_session_timeout()
                    notifier.notify("WATCHDOG=1")
                    last_queue_check = current_time
                
                # Sleep based on activity
                if self.in_low_power:
                    time.sleep(IDLE_CHECK_INTERVAL)
                else:
                    # Very short sleep to maintain responsiveness
                    # This allows near real-time processing while preventing CPU spinning
                    time.sleep(0.001)  # 1ms
                    
            except Exception as e:
                self.logger.error(f"Error in main loop: {e}")
                time.sleep(1)
                
    def port_monitor_thread(self):
        """Monitor MIDI port connection in separate thread"""
        self.current_port_name = None      # track the port name we’re using
        while self.running:
            try:
                ports = mido.get_input_names()

                # still connected?
                if (self.midi_port is None or
                        self.current_port_name not in ports):
                    # clean up old handle
                    if self.midi_port:
                        try:
                            self.midi_port.close()
                        except Exception:
                            pass
                        self.midi_port = None
                        self.logger.info("MIDI port lost")

                    # look for any port that contains “piano”
                    port_name = next((p for p in ports
                                      if 'pia' in p.lower()), None)

                    if port_name:
                        self.midi_port = mido.open_input(
                            port_name, callback=self.midi_callback)
                        self.current_port_name = port_name
                        self.logger.info(f"Connected to {port_name}")
                    else:
                        self.current_port_name = None  # nothing available

                time.sleep(1)           # adjust as needed
            except Exception as e:
                self.logger.error(f"Port monitor error: {e}")
                time.sleep(2)


if __name__ == "__main__":
    recorder = MidiRecorder()
    recorder.run()
