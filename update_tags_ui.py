import csv
import json
import os
import queue
import re
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

import exif
import pandas as pd
from scipy.spatial import cKDTree

CONFIG_FILE = Path(__file__).parent / "config.json"
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".JPG", ".JPEG"}
OUTPUT_COLUMNS = ["Filename", "Latitude", "Longitude", "City", "Country", "Date", "Time"]

# Matches embedded YYYYMMDD_HHMMSS (or YYYYMMDD-HHMMSS) as used by phone/camera
# filenames, e.g. "IMG_20180410_203647978.jpg" or "20140618_221441.jpg".
FILENAME_DATETIME_RE = re.compile(r"(\d{4})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})")
MIN_VALID_YEAR = 1995
MAX_VALID_YEAR = 2100

DEFAULT_CONFIG = {
    "photo_directories": [],
    "output_file": str(Path(__file__).parent / "output.csv"),
    "geonames_file": str(Path(__file__).parent / "geonames-all-cities-with-a-population-1000.csv"),
}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            data = json.load(f)
        for k, v in DEFAULT_CONFIG.items():
            data.setdefault(k, v)
        return data
    return DEFAULT_CONFIG.copy()


def save_config(config: dict) -> None:
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def get_gps_coord(filepath: str):
    try:
        with open(filepath, "rb") as f:
            img = exif.Image(f)
        if not hasattr(img, "gps_latitude"):
            return None
        lat = img.gps_latitude
        lon = img.gps_longitude
        lat_d = lat[0] + lat[1] / 60 + lat[2] / 3600
        lon_d = lon[0] + lon[1] / 60 + lon[2] / 3600
        if img.gps_latitude_ref == "S":
            lat_d *= -1
        if img.gps_longitude_ref == "W":
            lon_d *= -1
        return lat_d, lon_d
    except Exception:
        return None


def get_exif_datetime(filepath: str):
    try:
        with open(filepath, "rb") as f:
            img = exif.Image(f)
        for attr in ("datetime_original", "datetime_digitized", "datetime"):
            if hasattr(img, attr):
                dt = datetime.strptime(getattr(img, attr), "%Y:%m:%d %H:%M:%S")
                return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")
    except Exception:
        pass
    return None


def get_filename_datetime(filepath: str):
    name = Path(filepath).name
    for m in FILENAME_DATETIME_RE.finditer(name):
        year, month, day, hour, minute, second = (int(g) for g in m.groups())
        if not (MIN_VALID_YEAR <= year <= MAX_VALID_YEAR):
            continue
        try:
            dt = datetime(year, month, day, hour, minute, second)
        except ValueError:
            continue
        return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")
    return None


def get_date_time(filepath: str):
    """Return (date, time) strings, preferring EXIF over filename inference."""
    return get_exif_datetime(filepath) or get_filename_datetime(filepath) or (None, None)


def find_photo_files(directories: list) -> list:
    files = []
    for d in directories:
        for root, _, names in os.walk(d):
            for name in names:
                if Path(name).suffix in PHOTO_EXTENSIONS:
                    files.append(os.path.join(root, name))
    return sorted(files)


def run_pipeline(config: dict, log_cb, progress_cb, done_cb) -> None:
    try:
        output_file = config["output_file"]
        geonames_file = config["geonames_file"]
        directories = config["photo_directories"]

        existing = set()
        if os.path.exists(output_file):
            try:
                df_ex = pd.read_csv(output_file, header=None, names=OUTPUT_COLUMNS)
                existing = set(df_ex["Filename"].tolist())
                log_cb(f"Loaded {len(existing)} existing records from output file")
            except Exception as e:
                log_cb(f"Warning: could not read existing output: {e}")

        log_cb("Scanning photo directories...")
        all_files = find_photo_files(directories)
        log_cb(f"Found {len(all_files)} photo files total")

        new_files = [f for f in all_files if f not in existing]
        log_cb(f"{len(new_files)} new files to process")

        if not new_files:
            log_cb("Nothing to update — all files already processed.")
            done_cb(True)
            return

        log_cb("Extracting GPS and date/time data from new files...")
        gps_rows = []
        no_gps_rows = []
        total = len(new_files)
        for i, fp in enumerate(new_files):
            coord = get_gps_coord(fp)
            date_str, time_str = get_date_time(fp)
            if coord:
                gps_rows.append({
                    "Filename": fp,
                    "Latitude": coord[0],
                    "Longitude": coord[1],
                    "Date": date_str or "",
                    "Time": time_str or "",
                })
            else:
                no_gps_rows.append((fp, date_str or "", time_str or ""))
            if (i + 1) % 100 == 0 or (i + 1) == total:
                progress_cb(int((i + 1) / total * 50))

        log_cb(f"{len(gps_rows)} new files have GPS data, {len(no_gps_rows)} have none")

        if not gps_rows:
            # Still record no-GPS files so they aren't rescanned next run
            mode = "a" if os.path.exists(output_file) else "w"
            with open(output_file, mode, newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                for fp, date_str, time_str in no_gps_rows:
                    writer.writerow([fp, "", "", "", "", date_str, time_str])
            log_cb(f"Recorded {len(no_gps_rows)} files without GPS. Done.")
            done_cb(True)
            return

        log_cb("Loading geonames database...")
        df_geo = pd.read_csv(geonames_file, delimiter=";")
        df_geo["Coordinates"] = df_geo["Coordinates"].astype(str)
        df_geo[["Lat", "Lon"]] = df_geo["Coordinates"].str.split(",", n=1, expand=True)
        df_geo["Lat"] = pd.to_numeric(df_geo["Lat"], errors="coerce")
        df_geo["Lon"] = pd.to_numeric(df_geo["Lon"], errors="coerce")
        df_geo = df_geo.dropna(subset=["Lat", "Lon"])
        relevant = df_geo[["ASCII Name", "Country name EN", "Lat", "Lon"]].copy()

        log_cb("Finding nearest cities...")
        tree = cKDTree(relevant[["Lat", "Lon"]].values)
        df_new = pd.DataFrame(gps_rows)
        _, indices = tree.query(df_new[["Latitude", "Longitude"]].values, k=1)

        progress_cb(80)

        mode = "a" if os.path.exists(output_file) else "w"
        with open(output_file, mode, newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for i, row in df_new.iterrows():
                match = relevant.iloc[indices[i]]
                city = str(match["ASCII Name"])
                country = str(match["Country name EN"])
                writer.writerow([
                    row["Filename"], row["Latitude"], row["Longitude"],
                    city, country, row["Date"], row["Time"],
                ])
            for fp, date_str, time_str in no_gps_rows:
                writer.writerow([fp, "", "", "", "", date_str, time_str])

        progress_cb(100)
        log_cb(f"Done. Added {len(df_new)} geotagged + {len(no_gps_rows)} no-GPS records to {output_file}")
        done_cb(True)

    except Exception as e:
        import traceback
        log_cb(f"Error: {e}")
        log_cb(traceback.format_exc())
        done_cb(False)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Photo Metadata Updater")
        self.minsize(560, 500)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self.config_data = load_config()
        self._running = False
        self._queue: queue.Queue = queue.Queue()

        self._build_ui()
        self._poll_queue()

    def _build_ui(self):
        # --- Configuration frame ---
        cfg = ttk.LabelFrame(self, text="Configuration", padding=10)
        cfg.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 4))
        cfg.columnconfigure(1, weight=1)

        ttk.Label(cfg, text="Photo Directories:").grid(row=0, column=0, sticky="nw", padx=(0, 8))
        dir_frame = ttk.Frame(cfg)
        dir_frame.grid(row=0, column=1, sticky="nsew")
        dir_frame.columnconfigure(0, weight=1)

        self.dir_listbox = tk.Listbox(dir_frame, height=4, selectmode=tk.SINGLE, activestyle="dotbox")
        self.dir_listbox.grid(row=0, column=0, columnspan=2, sticky="ew")
        for d in self.config_data.get("photo_directories", []):
            self.dir_listbox.insert(tk.END, d)

        ttk.Button(dir_frame, text="Add Directory", command=self._add_dir).grid(row=1, column=0, sticky="w", pady=(3, 0))
        ttk.Button(dir_frame, text="Remove Selected", command=self._remove_dir).grid(row=1, column=1, sticky="w", pady=(3, 0), padx=(6, 0))

        ttk.Label(cfg, text="Output File:").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        out_frame = ttk.Frame(cfg)
        out_frame.grid(row=1, column=1, sticky="ew", pady=(8, 0))
        out_frame.columnconfigure(0, weight=1)
        self.output_var = tk.StringVar(value=self.config_data.get("output_file", ""))
        ttk.Entry(out_frame, textvariable=self.output_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(out_frame, text="…", width=3, command=self._browse_output).grid(row=0, column=1, padx=(4, 0))

        ttk.Label(cfg, text="Geonames File:").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=(4, 0))
        geo_frame = ttk.Frame(cfg)
        geo_frame.grid(row=2, column=1, sticky="ew", pady=(4, 0))
        geo_frame.columnconfigure(0, weight=1)
        self.geonames_var = tk.StringVar(value=self.config_data.get("geonames_file", ""))
        ttk.Entry(geo_frame, textvariable=self.geonames_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(geo_frame, text="…", width=3, command=self._browse_geonames).grid(row=0, column=1, padx=(4, 0))

        # --- Update button ---
        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=1, column=0, pady=6)
        self.update_btn = ttk.Button(btn_frame, text="Update", width=18, command=self._start_update)
        self.update_btn.pack()

        # --- Status frame ---
        status = ttk.LabelFrame(self, text="Status", padding=10)
        status.grid(row=2, column=0, sticky="nsew", padx=10, pady=(4, 10))
        status.columnconfigure(0, weight=1)
        status.rowconfigure(0, weight=1)

        self.log_area = scrolledtext.ScrolledText(status, height=12, state="disabled", wrap=tk.WORD)
        self.log_area.grid(row=0, column=0, sticky="nsew")

        self.progress_bar = ttk.Progressbar(status, mode="determinate", maximum=100)
        self.progress_bar.grid(row=1, column=0, sticky="ew", pady=(6, 0))

    # --- Directory management ---

    def _add_dir(self):
        d = filedialog.askdirectory(title="Select Photo Directory")
        if d and d not in self.dir_listbox.get(0, tk.END):
            self.dir_listbox.insert(tk.END, d)
            self._save_config()

    def _remove_dir(self):
        sel = self.dir_listbox.curselection()
        if sel:
            self.dir_listbox.delete(sel[0])
            self._save_config()

    def _browse_output(self):
        p = filedialog.asksaveasfilename(
            title="Output CSV File",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*")],
            confirmoverwrite=False,
        )
        if p:
            self.output_var.set(p)
            self._save_config()

    def _browse_geonames(self):
        p = filedialog.askopenfilename(
            title="Geonames CSV File",
            filetypes=[("CSV files", "*.csv"), ("All files", "*")],
        )
        if p:
            self.geonames_var.set(p)
            self._save_config()

    # --- Config persistence ---

    def _save_config(self):
        self.config_data["photo_directories"] = list(self.dir_listbox.get(0, tk.END))
        self.config_data["output_file"] = self.output_var.get()
        self.config_data["geonames_file"] = self.geonames_var.get()
        save_config(self.config_data)

    # --- Pipeline execution ---

    def _start_update(self):
        if self._running:
            return

        self._save_config()
        config = self.config_data.copy()

        if not config.get("photo_directories"):
            messagebox.showwarning("No Directories", "Add at least one photo directory first.")
            return

        geonames = config.get("geonames_file", "")
        if not geonames or not os.path.exists(geonames):
            messagebox.showwarning("Geonames File Missing", "Please select a valid geonames CSV file.")
            return

        self._running = True
        self.update_btn.config(state="disabled")
        self.progress_bar["value"] = 0

        log_cb = lambda msg: self._queue.put(("log", msg))
        progress_cb = lambda val: self._queue.put(("progress", val))
        done_cb = lambda ok: self._queue.put(("done", ok))

        threading.Thread(
            target=run_pipeline,
            args=(config, log_cb, progress_cb, done_cb),
            daemon=True,
        ).start()

    def _poll_queue(self):
        try:
            while True:
                kind, value = self._queue.get_nowait()
                if kind == "log":
                    self._append_log(value)
                elif kind == "progress":
                    self.progress_bar["value"] = value
                elif kind == "done":
                    self._running = False
                    self.update_btn.config(state="normal")
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _append_log(self, msg: str):
        self.log_area.config(state="normal")
        self.log_area.insert(tk.END, msg + "\n")
        self.log_area.see(tk.END)
        self.log_area.config(state="disabled")


if __name__ == "__main__":
    App().mainloop()
