import json
import os
import re
import winreg
import psutil
import difflib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class AppIndexer:
    """
    Indexes Windows applications from multiple sources for fast lookup.
    Discovers apps from Start Menu, system tray, registry, and common install locations.
    """

    def __init__(
        self,
        cache_path: Optional[str] = None,
        auto_refresh: bool = True,
        include_processes: bool = True,
    ):
        """
        Initialize the app indexer.

        Args:
            cache_path: Path to cache file (default: ~/.pixelpilot/app_index.json)
            auto_refresh: Whether to auto-refresh cache if older than 7 days
            include_processes: Whether to include running processes in the index
        """
        if cache_path is None:
            cache_dir = Path.home() / ".pixelpilot"
            cache_path = str(cache_dir / "app_index.json")

        path_obj = Path(cache_path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)

        self.cache_path = cache_path
        self.auto_refresh = auto_refresh
        self.include_processes = include_processes
        self.index: Dict[str, Dict] = {}

        self._load_or_build_index()

    def _load_or_build_index(self):
        """Load index from cache or build a new one if needed."""
        should_rebuild = True

        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                if self.auto_refresh:
                    cache_time = datetime.fromisoformat(data.get("timestamp", "2000-01-01"))
                    age = datetime.now() - cache_time
                    if age < timedelta(days=7):
                        self.index = data.get("apps", {})
                        should_rebuild = False
                        print(f"Loaded app index from cache ({len(self.index)} apps)")
                else:
                    self.index = data.get("apps", {})
                    should_rebuild = False

            except Exception as e:
                print(f"Error loading app index cache: {e}")

        if should_rebuild:
            print("Building app index... (this may take a few seconds)")
            self._build_index()
            self._save_cache()

    def _build_index(self):
        """Build the application index from all sources."""

        self.index = {}

        self._index_start_menu()

        if self.include_processes:
            self._index_running_processes()

        self._index_registry()
        self._index_common_executables()

        print(f"App index built: {len(self.index)} applications found")

    def _index_common_executables(self):
        """Index executables from common installation directories (Smart Scan)."""
        include_dirs = [
             os.environ.get("LOCALAPPDATA"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
             os.environ.get("ProgramFiles"),
             os.environ.get("ProgramFiles(x86)")
        ]
        include_dirs = [d for d in include_dirs if d and os.path.exists(d)]

        # Common service/helper executables to ignore to reduce noise
        ignore_names = {
            "uninstall.exe", "update.exe", "updater.exe", "helper.exe", 
            "setup.exe", "installer.exe", "config.exe", "report.exe",
            "crashpad_handler.exe", "notification_helper.exe", "service.exe"
        }

        for base_dir in include_dirs:
            try:
                # Walk with limited depth
                for root, dirs, files in os.walk(base_dir):
                    # Depth check: roughly 3 levels deep from base 
                    # (e.g. Program Files/Vendor/App/app.exe)
                    rel_depth = root.count(os.sep) - base_dir.count(os.sep)
                    if rel_depth > 3:
                        del dirs[:] # Stop deeper recursion
                        continue
                    
                    for file in files:
                        if not file.lower().endswith(".exe"):
                            continue
                        
                        f_lower = file.lower()
                        if f_lower in ignore_names:
                            continue

                        app_name = f_lower[:-4] # remove .exe
                        
                        # Heuristic: If executable name matches folder name, it's likely the main app
                        # e.g. "Spotify.exe" inside "Spotify" folder
                        folder_name = os.path.basename(root).lower()
                        
                        # Assign a confidence boost if folder matches app name
                        is_likely_main = (app_name == folder_name) or (app_name in folder_name)
                        
                        if not is_likely_main:
                            # Skip obscure exes deep in subfolders
                            continue

                        # Only index if we haven't found it via Start Menu (which is better source)
                        # or if we want to overwrite/augment
                        if app_name not in self.index:
                            self.index[app_name] = {
                                "name": file[:-4].title(), # Capitalized name
                                "path": os.path.join(root, file),
                                "type": "executable_scan",
                                "launch_method": "executable",
                                "search_terms": self._generate_search_terms(app_name)
                            }
            except Exception:
                pass

    def _index_start_menu(self):
        """Index applications from Start Menu shortcuts."""

        start_menu_paths = [
            Path(os.environ.get("PROGRAMDATA", "C:\\ProgramData"))
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs",
            Path(os.environ.get("APPDATA", ""))
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs",
        ]

        for base_path in start_menu_paths:
            if not base_path.exists():
                continue

            for lnk_file in base_path.rglob("*.lnk"):
                try:
                    app_name = lnk_file.stem.lower()

                    skip_keywords = ["uninstall", "readme", "help", "documentation"]
                    if any(kw in app_name for kw in skip_keywords):
                        continue

                    self.index[app_name] = {
                        "name": lnk_file.stem,
                        "path": str(lnk_file),
                        "type": "start_menu",
                        "launch_method": "startfile",
                        "search_terms": self._generate_search_terms(lnk_file.stem),
                    }

                except Exception:
                    pass

    def _index_running_processes(self):
        """Index currently running processes (includes system tray apps)."""
        try:
            for proc in psutil.process_iter(["pid", "name", "exe"]):
                try:
                    proc_info = proc.info
                    proc_name = proc_info.get("name", "").lower()
                    proc_exe = proc_info.get("exe", "")

                    if not proc_exe or not proc_name:
                        continue

                    system_procs = [
                        "svchost.exe",
                        "system",
                        "registry",
                        "csrss.exe",
                        "smss.exe",
                        "services.exe",
                        "lsass.exe",
                        "dwm.exe",
                    ]
                    if proc_name in system_procs:
                        continue

                    clean_name = proc_name.replace(".exe", "").lower()

                    if clean_name not in self.index:
                        self.index[clean_name] = {
                            "name": clean_name.title(),
                            "path": proc_exe,
                            "type": "running_process",
                            "launch_method": "executable",
                            "search_terms": self._generate_search_terms(clean_name),
                        }

                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass

        except Exception as e:
            print(f"Error indexing processes: {e}")

    def _index_registry(self):
        """Index applications registered in Windows Registry."""
        reg_paths = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths"),
        ]

        for root_key, subkey_path in reg_paths:
            try:
                with winreg.OpenKey(root_key, subkey_path, 0, winreg.KEY_READ) as key:
                    i = 0
                    while True:
                        try:
                            app_exe = winreg.EnumKey(key, i)
                            app_name = app_exe.replace(".exe", "").lower()

                            try:
                                with winreg.OpenKey(key, app_exe) as app_key:
                                    app_path, _ = winreg.QueryValueEx(app_key, "")

                                    if app_name not in self.index:
                                        self.index[app_name] = {
                                            "name": app_name.title(),
                                            "path": app_path,
                                            "type": "registry",
                                            "launch_method": "executable",
                                            "search_terms": self._generate_search_terms(app_name),
                                        }
                            except Exception:
                                pass

                            i += 1
                        except OSError:
                            break
            except Exception:
                pass

    def _index_program_files(self):
        """
        Index common installation directories.
        NOTE: This can be slow, so it's disabled by default.
        """
        program_dirs = [
            Path("C:\\Program Files"),
            Path("C:\\Program Files (x86)"),
            Path.home() / "AppData" / "Local" / "Programs",
        ]

        for base_dir in program_dirs:
            if not base_dir.exists():
                continue

            for app_dir in base_dir.iterdir():
                if app_dir.is_dir():
                    for exe_file in app_dir.glob("*.exe"):
                        app_name = exe_file.stem.lower()

                        if app_name not in self.index:
                            self.index[app_name] = {
                                "name": exe_file.stem,
                                "path": str(exe_file),
                                "type": "program_files",
                                "launch_method": "executable",
                                "search_terms": self._generate_search_terms(app_name),
                            }

    def _generate_search_terms(self, app_name: str) -> List[str]:
        """
        Generate search terms for fuzzy matching.

        Args:
            app_name: Application name

        Returns:
            List of search terms
        """
        terms = [app_name.lower()]

        terms.append(app_name.replace(" ", "").replace("-", "").lower())

        words = re.split(r"[\s\-_]+", app_name.lower())
        terms.extend(words)

        return list(set(terms))

    def find_app(self, query: str, max_results: int = 5) -> List[Dict]:
        """
        Find applications matching the query using exact, partial, and fuzzy matching.

        Args:
            query: Search query (app name)
            max_results: Maximum number of results to return

        Returns:
            List of matching app info dictionaries, sorted by relevance
        """

        query = query.lower().strip()
        matches = []

        for app_key, app_info in self.index.items():
            score = 0

            if app_key == query:
                score = 100

            elif app_key.startswith(query):
                score = 90

            elif query in app_key:
                score = 70

            else:
                for term in app_info.get("search_terms", []):
                    if term == query:
                        score = max(score, 85)
                    elif term.startswith(query):
                        score = max(score, 75)
                    elif query in term:
                        score = max(score, 60)

            if score < 50:
                fuzzy_score_key = difflib.SequenceMatcher(None, query, app_key).ratio() * 100
                fuzzy_score_name = (
                    difflib.SequenceMatcher(None, query, app_info.get("name", "").lower()).ratio()
                    * 100
                )

                fuzzy_score_terms = 0
                for term in app_info.get("search_terms", []):
                    ratio = difflib.SequenceMatcher(None, query, term).ratio() * 100
                    fuzzy_score_terms = max(fuzzy_score_terms, ratio)

                max_fuzzy = max(fuzzy_score_key, fuzzy_score_name, fuzzy_score_terms)

                if max_fuzzy > 70:
                    score = max(score, int(max_fuzzy * 0.8))

            if app_info.get("type") == "running_process" and score > 0:
                score += 10

            if score > 0:
                matches.append({**app_info, "score": score, "key": app_key})

        matches.sort(key=lambda x: x["score"], reverse=True)

        return matches[:max_results]

    def get_launch_command(self, app_info: Dict) -> Tuple[str, str]:
        """
        Get the best launch command for an application.

        Args:
            app_info: App information dictionary

        Returns:
            Tuple of (method, command)
            - method: 'start_menu' or 'executable'
            - command: The command to execute
        """
        launch_method = app_info.get("launch_method", "start_menu")

        if launch_method == "executable" or launch_method == "startfile":
            return (launch_method, app_info.get("path", ""))
        else:
            return ("start_menu", app_info.get("name", ""))

    def _save_cache(self):
        """Save the index to cache file."""
        try:
            data = {"timestamp": datetime.now().isoformat(), "apps": self.index}

            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            print(f"App index cached to {self.cache_path}")
        except Exception as e:
            print(f"Warning: Could not save app index cache: {e}")

    def refresh(self):
        """Force rebuild the index."""
        self._build_index()
        self._save_cache()

    def __repr__(self):
        return f"AppIndexer({len(self.index)} apps indexed)"


def find_application(app_name: str) -> Optional[Dict]:
    """
    Quick lookup function to find an application.

    Args:
        app_name: Name of application to find

    Returns:
        App info dict or None if not found
    """
    indexer = AppIndexer()
    results = indexer.find_app(app_name, max_results=1)
    return results[0] if results else None


if __name__ == "__main__":
    import os
    import sys
    import subprocess

    if len(sys.argv) < 2:
        print("Usage: python app_indexer.py <app_name> [--launch]")
        sys.exit(1)

    query = sys.argv[1]
    should_launch = "--launch" in sys.argv

    indexer = AppIndexer()
    results = indexer.find_app(query)

    if not results:
        print(f"No applications found matching '{query}'")
    else:
        print(f"Found {len(results)} matches:")
        for i, app in enumerate(results, 1):
            print(f"{i}. {app['name']} (Score: {app['score']}%)")
            print(f"   Type: {app['type']}")
            print(f"   Path: {app.get('path', 'N/A')}")

        if should_launch:
            app = results[0]
            method, cmd = indexer.get_launch_command(app)
            print(f"\nLaunching {app['name']} via {method}...")

            try:
                if method == "executable":
                    subprocess.Popen(cmd)
                elif method == "startfile":
                    os.startfile(cmd)
                else:
                    os.system(f'start "" "{app["name"]}"')
                print("Launch command sent successfully.")
            except Exception as e:
                print(f"Error launching app: {e}")
