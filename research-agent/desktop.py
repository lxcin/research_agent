"""PaperPilot Desktop — launches backend + native window."""
import threading
import time
import sys
import os


class PaperPilotAPI:
    """Native API exposed to frontend via pywebview."""

    def pick_folder(self):
        """Open native folder picker dialog."""
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            path = filedialog.askdirectory(title="选择工作区目录")
            root.destroy()
            return path if path else ""
        except Exception:
            return ""

    def get_version(self):
        return "1.0.0"


def main():
    # Add src to path
    src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
    sys.path.insert(0, src_path)

    import uvicorn
    from research_agent.server import app

    # Start backend in a thread
    def run_server():
        uvicorn.run(app, host="127.0.0.1", port=8050, log_level="warning")

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # Wait for server to be ready
    import httpx
    for _ in range(30):
        try:
            httpx.get("http://127.0.0.1:8050/api/health", timeout=1)
            break
        except Exception:
            time.sleep(0.5)

    # Open native window with JS API
    import webview
    api = PaperPilotAPI()
    window = webview.create_window(
        "PaperPilot",
        "http://127.0.0.1:8050",
        width=1200,
        height=800,
        min_size=(800, 600),
        text_select=True,
        js_api=api,
    )
    webview.start()
    sys.exit(0)


if __name__ == "__main__":
    main()