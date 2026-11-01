#!/usr/bin/env python3
"""
ControllerMode-MuOS - In-App Updater
Checks GitHub Releases, downloads updates, and applies them seamlessly on muOS.
"""
import json
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile

CURRENT_VERSION = "1.0.0"
REPO = "XenoSlug/ControllerMode-MuOS"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RESTART_CODE = 42

def _version_tuple(v: str):
    clean = v.lstrip("v").strip()
    try:
        return tuple(int(x) for x in clean.split("."))
    except ValueError:
        return (0,)

def is_newer(remote_v: str, local_v: str) -> bool:
    return _version_tuple(remote_v) > _version_tuple(local_v)

class Updater:
    def __init__(self, app_dir: str):
        self.app_dir = app_dir
        self.update_dir = os.path.join(self.app_dir, ".update")
        self.status = "idle"  # idle, checking, available, up_to_date, downloading, ready, error
        self.latest_info = None
        self.download_progress = 0
        self.error_message = ""
        self._lock = threading.Lock()

    def check_update_async(self, callback=None):
        def _task():
            res = self.check_update()
            if callback:
                callback(res)
        threading.Thread(target=_task, daemon=True).start()

    def check_update(self) -> dict or None:
        with self._lock:
            self.status = "checking"
            self.error_message = ""

        try:
            req = urllib.request.Request(
                API_URL,
                headers={
                    "User-Agent": f"ControllerMode-MuOS/{CURRENT_VERSION}",
                    "Accept": "application/vnd.github.v3+json"
                }
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                tag_name = data.get("tag_name", "")

                if is_newer(tag_name, CURRENT_VERSION):
                    # Find .muxzip asset
                    download_url = None
                    asset_size = 0
                    for asset in data.get("assets", []):
                        if asset.get("name", "").endswith(".muxzip") or asset.get("name", "").endswith(".zip"):
                            download_url = asset.get("browser_download_url")
                            asset_size = asset.get("size", 0)
                            break

                    if download_url:
                        info = {
                            "version": tag_name,
                            "name": data.get("name", tag_name),
                            "notes": data.get("body", ""),
                            "url": download_url,
                            "size": asset_size
                        }
                        with self._lock:
                            self.status = "available"
                            self.latest_info = info
                        return info

                with self._lock:
                    self.status = "up_to_date"
                return None

        except urllib.error.URLError as e:
            with self._lock:
                self.status = "error"
                self.error_message = "No internet connection or GitHub unreachable"
            return None
        except Exception as e:
            with self._lock:
                self.status = "error"
                self.error_message = str(e)
            return None

    def apply_update(self, progress_cb=None) -> bool:
        """Downloads, verifies, unpacks, and replaces app files, then exits with code 42."""
        if not self.latest_info:
            return False

        with self._lock:
            self.status = "downloading"
            self.download_progress = 0

        url = self.latest_info["url"]
        temp_zip = os.path.join(self.app_dir, ".update_temp.zip")
        extract_dir = os.path.join(self.update_dir, "new")

        try:
            os.makedirs(self.update_dir, exist_ok=True)
            if os.path.exists(extract_dir):
                shutil.rmtree(extract_dir)

            # Download
            req = urllib.request.Request(url, headers={"User-Agent": f"ControllerMode-MuOS/{CURRENT_VERSION}"})
            with urllib.request.urlopen(req, timeout=30) as resp, open(temp_zip, "wb") as out_f:
                total_size = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                chunk_size = 8192

                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    out_f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        pct = int((downloaded / total_size) * 100)
                        with self._lock:
                            self.download_progress = pct
                        if progress_cb:
                            progress_cb(pct)

            # Extract
            with zipfile.ZipFile(temp_zip, "r") as z:
                z.extractall(extract_dir)

            # Locate root inside zip (e.g. MUOS/application/ControllerMode/ or root)
            app_source = extract_dir
            for candidate in [
                os.path.join(extract_dir, "MUOS", "application", "ControllerMode"),
                os.path.join(extract_dir, "ControllerMode")
            ]:
                if os.path.isdir(candidate):
                    app_source = candidate
                    break

            # Copy files over
            for root, dirs, files in os.walk(app_source):
                rel = os.path.relpath(root, app_source)
                dest_dir = self.app_dir if rel == "." else os.path.join(self.app_dir, rel)
                os.makedirs(dest_dir, exist_ok=True)

                for f in files:
                    if f in ("settings.json", "app.log"):
                        continue  # Preserve user settings and logs
                    src_file = os.path.join(root, f)
                    dst_file = os.path.join(dest_dir, f)
                    shutil.copy2(src_file, dst_file)

            # Clean temporary files
            if os.path.exists(temp_zip):
                os.remove(temp_zip)
            if os.path.exists(extract_dir):
                shutil.rmtree(extract_dir)

            # Ensure scripts are executable
            mux_sh = os.path.join(self.app_dir, "mux_launch.sh")
            if os.path.exists(mux_sh):
                os.chmod(mux_sh, 0o755)

            with self._lock:
                self.status = "ready"

            print("[Updater] Update applied successfully! Restarting application...")
            time.sleep(0.5)
            # Exit with code 42 to tell mux_launch.sh to restart
            sys.exit(RESTART_CODE)

        except Exception as e:
            with self._lock:
                self.status = "error"
                self.error_message = f"Failed to install: {e}"
            if os.path.exists(temp_zip):
                os.remove(temp_zip)
            return False
