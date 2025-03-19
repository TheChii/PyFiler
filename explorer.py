#original
import os
import curses
import shutil
import sys
from datetime import datetime
import time
import psutil
import platform
import subprocess
from functools import lru_cache
import threading
from collections import deque

class FileManager:


    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.current_path = os.getcwd()
        self.selected_idx = 0
        self.files = []
        self.filtered_files = []
        self.show_hidden = False
        self.status_msg = ""
        self.message_timeout = 0
        self.clipboard = {'files': [], 'operation': None}
        self.selected_files = set()
        self.search_mode = False
        self.search_query = ""
        self.search_base_path = os.getcwd()
        self.search_results = []
        self.history = []
        self.history_index = -1
        self.sort_mode = 'name'
        self.loading = False
        self.last_key_time = 0

        self.search_queue = deque()
        self.search_thread = None
        self.search_active = False
        self.search_lock = threading.Lock()
        self.result_batch = []
        self.batch_size = 50  
        self.tabs = [{'path': os.getcwd(), 'index': 0}]
        self.current_tab = 0

        self.dir_cache = {}  
        self.metadata_cache = {}

        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, curses.COLOR_BLACK)  # Directory
        curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)  # Executable
        curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)  # Archive
        curses.init_pair(4, curses.COLOR_RED, curses.COLOR_BLACK)  # Error/Message
        curses.init_pair(5, curses.COLOR_MAGENTA, curses.COLOR_BLACK)  # Image
        curses.init_pair(6, curses.COLOR_WHITE, curses.COLOR_BLUE)  # Selection
        curses.init_pair(7, curses.COLOR_BLACK, curses.COLOR_WHITE)  # Input
        curses.init_pair(8, curses.COLOR_BLACK, curses.COLOR_YELLOW)  # Multi-select

        self.colors = {
            'directory': 1,
            'executable': 2,
            'archive': 3,
            'image': 5,
            'default': curses.COLOR_WHITE
        }

    def get_file_type(self, filename):
        if self.search_mode:
            full_path = os.path.join(self.search_base_path, filename)
        else:
            full_path = os.path.join(self.current_path, filename)
        
        if os.path.isdir(full_path):
            return 'directory'
        if os.access(full_path, os.X_OK):
            return 'executable'
        ext = os.path.splitext(filename)[1].lower()
        if ext in ['.zip', '.tar', '.gz', '.bz2']:
            return 'archive'
        if ext in ['.png', '.jpg', '.jpeg', '.gif']:
            return 'image'
        return 'default'

    @lru_cache(maxsize=100)
    def get_cached_listdir(self, path):
        """Cached directory listing with invalidation based on mtime"""
        try:
            # Get the directory's modification time to include in the cache key
            mtime = os.path.getmtime(path)
        except:
            return []
        
        try:
            return os.listdir(path)
        except:
            return []
        
        # This return is theoretically unreachable due to the try-except blocks above
        return []

    def refresh_files(self):
        self.loading = True
        try:
            current_mtime = os.path.getmtime(self.current_path)
            cached = self.dir_cache.get(self.current_path)
            
            if cached and cached[0] == current_mtime:
                entries = cached[1]
            else:
                with os.scandir(self.current_path) as scan:
                    entries = list(scan)
                self.dir_cache[self.current_path] = (current_mtime, entries)
                self.metadata_cache[self.current_path] = {}

            self.files = []
            for entry in entries:
                if self.show_hidden or not entry.name.startswith('.'):
                    try:
                        meta = {
                            'name': entry.name,
                            'is_dir': entry.is_dir(),
                            'size': entry.stat().st_size if not entry.is_dir() else 0,
                            'mtime': entry.stat().st_mtime,
                            'type': self.determine_file_type(entry)
                        }
                        self.metadata_cache[self.current_path][entry.name] = meta
                        self.files.append(entry.name)
                    except OSError:
                        continue

            if self.sort_mode == 'name':
                self.files.sort(key=lambda x: (
                    not self.metadata_cache[self.current_path][x]['is_dir'], 
                    x.lower()
                ))
            elif self.sort_mode == 'size':
                self.files.sort(key=lambda x: (
                    self.metadata_cache[self.current_path][x]['size']
                ))
            elif self.sort_mode == 'modified':
                self.files.sort(key=lambda x: (
                    self.metadata_cache[self.current_path][x]['mtime']
                ), reverse=True)

            self.apply_search_filter()
        except PermissionError:
            self.show_message("Permission denied", 2)
        finally:
            self.loading = False

    def determine_file_type(self, entry):
        """Determine file type using DirEntry attributes"""
        if entry.is_dir():
            return 'directory'
        if platform.system() != 'Windows' and (os.access(entry.path, os.X_OK)):
            return 'executable'
        ext = os.path.splitext(entry.name)[1].lower()
        if ext in ['.zip', '.tar', '.gz', '.bz2']:
            return 'archive'
        if ext in ['.png', '.jpg', '.jpeg', '.gif']:
            return 'image'
        return 'default'

    def get_file_metadata(self, filename):
        """Retrieve cached metadata for file"""
        return self.metadata_cache[self.current_path].get(filename, {})

    def apply_search_filter(self):
        if self.search_mode:
            query = self.search_query.lower()
            self.filtered_files = [
                rel_path for rel_path in self.search_results
                if query in rel_path.lower()
            ]
        else:
            if self.search_query:
                self.filtered_files = [
                    f for f in self.files
                    if self.search_query.lower() in f.lower()
                ]
            else:
                self.filtered_files = self.files.copy()

    def draw_borders(self):
        height, width = self.stdscr.getmaxyx()
        self.stdscr.hline(0, 0, curses.ACS_HLINE, width)
        self.stdscr.hline(height - 2, 0, curses.ACS_HLINE, width)
        self.stdscr.vline(0, 0, curses.ACS_VLINE, height - 2)
        self.stdscr.vline(0, width - 1, curses.ACS_VLINE, height - 2)
        self.stdscr.addch(0, 0, curses.ACS_ULCORNER)
        self.stdscr.addch(0, width - 1, curses.ACS_URCORNER)
        self.stdscr.addch(height - 2, 0, curses.ACS_LLCORNER)
        self.stdscr.addch(height - 2, width - 1, curses.ACS_LRCORNER)

    def draw_header(self):
        LOADING_ANIMATION = ['⣾', '⣽', '⣻', '⢿', '⡿', '⣟', '⣯', '⣷']
        SEARCH_ANIMATION = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
        self.draw_tab_bar()
        height, width = self.stdscr.getmaxyx()

        if self.loading:
            frame = int(time.time() * 8) % len(LOADING_ANIMATION)
            loading_indicator = LOADING_ANIMATION[frame]
        else:
            loading_indicator = '✓'

        header = f" {loading_indicator} 📁 {self.current_path} "
        
        if not self.search_mode:
            try:
                usage = psutil.disk_usage(self.current_path)
                free_space = f"{usage.free / (1024**3):.1f}GB free"
                header += f" [{free_space}]"
            except Exception as e:
                header += " [Space: N/A]"
        
        if self.search_mode:
            header += f" [Search: {self.search_query}]"
        
        header = header[:width - 20]  # Leave space for sort indicator
        self.stdscr.addstr(1, 2, header.ljust(width-4), curses.color_pair(1) | curses.A_BOLD)

        # Sort indicator with animation during operations
        sort_text = f"Sort: {self.sort_mode.title()}"
        if self.loading:
            frame = int(time.time() * 4) % len(SEARCH_ANIMATION)
            sort_text = f"{SEARCH_ANIMATION[frame]} {sort_text}"
        self.stdscr.addstr(1, width - len(sort_text) - 2, sort_text, curses.color_pair(3))

    def draw_progress(self, current: int, total: int, message: str):
        height, width = self.stdscr.getmaxyx()
        bar_width = min(width - 20, 50)  # Maximum 50 characters for bar
        progress = current / total if total > 0 else 0
        filled = int(bar_width * progress)
        
        bar_chars = ['▏', '▎', '▍', '▌', '▋', '▊', '▉']
        fractional = int((bar_width * progress - filled) * len(bar_chars))
        
        bar = '[' + '█' * filled 
        if current < total:
            bar += bar_chars[fractional] if fractional > 0 else ''
        bar += ' ' * (bar_width - filled - 1) + ']'
        
        progress_text = f"{message} {bar} {current}/{total}"
        self.stdscr.addstr(height - 4, 2, progress_text.ljust(width-4), curses.color_pair(3))
        self.stdscr.refresh()

    def draw_list(self):
        height, width = self.stdscr.getmaxyx()
        max_items = height - 5
        files = self.filtered_files if self.search_mode else self.files
        start_idx = max(0, self.selected_idx - max_items + 1)

        for i in range(max_items):
            curr_idx = start_idx + i
            if curr_idx >= len(files):
                break

            filename = files[curr_idx]
            meta = self.get_file_metadata(filename)
            
            is_dir = meta.get('is_dir', False)
            file_type = meta.get('type', 'default')
            size = meta.get('size', 0)
            mtime = meta.get('mtime', 0)
            
            filepath = os.path.join(self.search_base_path, filename) if self.search_mode else os.path.join(self.current_path, filename)
            is_dir = os.path.isdir(filepath)
            prefix = "  📁 " if is_dir else "  📄 "
            color_type = self.get_file_type(filename)
            color = self.colors.get(color_type, curses.COLOR_WHITE)
            
            select_indicator = "✓ " if filename in self.selected_files else "  "
            display_name = f"{select_indicator}{prefix}{filename}"
            
            display_name = display_name[:width - 20]
            if len(display_name) > width - 20:
                display_name = display_name[:-3] + "..."

            if curr_idx == self.selected_idx:
                self.stdscr.addstr(3 + i, 2, display_name.ljust(width - 4), curses.color_pair(6))
            else:
                color_pair = 8 if filename in self.selected_files else color
                self.stdscr.addstr(3 + i, 2, display_name, curses.color_pair(color_pair))

            if not is_dir:
                try:
                    stat = os.stat(filepath)
                    size = stat.st_size
                    mtime = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')
                    size_str = f"{size / 1024:.1f}KB" if size < 1024 * 1024 else f"{size / (1024 * 1024):.1f}MB"
                    info = f"{size_str}  {mtime}"
                    self.stdscr.addstr(3 + i, width - len(info) - 2, info, curses.color_pair(3))
                except:
                    pass

    def draw_footer(self):
        height, width = self.stdscr.getmaxyx()
        footer_parts = [
            "[F1]Help", "[F5]Refresh", "[F6]Sort", "[PgUp/PgDn]Tabs",
            "[↑/↓]Nav", "[↵]Open", "[←]Back", "[Space]Select",
            "[C]Copy", "[X]Cut", "[V]Paste", "[D]Delete",
            "[S]Search", "[Esc]Cancel", "[Q]uit"
        ]
        footer = " ".join(footer_parts)
        self.stdscr.addstr(height - 1, 2, footer[:width - 4], curses.color_pair(4))
        
        if self.status_msg and time.time() < self.message_timeout:
            msg = f" {self.status_msg} "
            self.stdscr.addstr(height - 3, 2, msg, curses.color_pair(4) | curses.A_REVERSE)

    def show_message(self, msg, timeout=3):
        self.status_msg = msg
        self.message_timeout = time.time() + timeout

    def get_input(self, prompt):
        height, width = self.stdscr.getmaxyx()
        self.stdscr.addstr(height - 3, 2, prompt.ljust(width-4), curses.color_pair(7))
        curses.echo()
        input_str = self.stdscr.getstr(height - 3, 2 + len(prompt), 60).decode()
        curses.noecho()
        self.stdscr.move(height - 3, 2)
        self.stdscr.clrtoeol()
        return input_str.strip()

    def handle_input(self, key):
        if self.search_mode:
            self.handle_search_input(key)
            return

        if key == curses.KEY_UP:
            self.selected_idx = max(0, self.selected_idx - 1)
        elif key == curses.KEY_DOWN:
            files = self.filtered_files if self.search_mode else self.files
            self.selected_idx = min(len(files) - 1, self.selected_idx + 1)
        elif key == curses.KEY_LEFT or key == 27:  # 27 is ESC
            self.navigate_up()
        elif key == 10:  # ENTER
            self.navigate_into()
        elif key == ord(' '):
            self.toggle_selection()
        elif key == ord('q'):
            sys.exit()
        elif key == ord('h'):
            self.show_hidden = not self.show_hidden
            self.refresh_files()
        elif key == ord('d'):
            self.delete_files()
        elif key == ord('c'):
            self.copy_files()
        elif key == ord('x'):
            self.cut_files()
        elif key == ord('v'):
            self.paste_files()
        elif key == ord('s'):
            self.start_search()
        elif key == curses.KEY_F5:
            self.refresh_files()
        elif key == curses.KEY_F6:
            self.cycle_sort_mode()

        elif key == curses.KEY_NPAGE or key == curses.KEY_CTAB:  # Page Down/Ctrl+I
            self._next_tab()
        elif key == curses.KEY_PPAGE:  # Page Up
            self._prev_tab()
        elif key == ord('t'):
            self._next_tab()  # New tab with Ctrl+T
        elif key == ord('w'):  # Close tab with Ctrl+W
            self.close_current_tab()

        elif key == 27:  
            next_key = self.stdscr.getch()
            if next_key == curses.KEY_LEFT:
                self.navigate_history_back()
            elif next_key == curses.KEY_RIGHT:
                self.navigate_history_forward()

    def cycle_sort_mode(self):
        modes = ['name', 'size', 'modified']
        current_index = modes.index(self.sort_mode)
        self.sort_mode = modes[(current_index + 1) % len(modes)]
        self.refresh_files()

    def draw_tab_bar(self):
        height, width = self.stdscr.getmaxyx()
        tab_bar = ""
        for i, tab in enumerate(self.tabs):
            prefix = ">" if i == self.current_tab else " "
            path = os.path.basename(tab['path']) or tab['path']
            tab_str = f"{prefix} Tab {i+1}: {path} {prefix}"
            tab_bar += tab_str[:width//4] + "|"
        self.stdscr.addstr(0, 2, tab_bar[:width-4], curses.color_pair(3))

    def _next_tab(self):
        # Save current state before switching
        self._save_current_tab_state()
        
        if self.current_tab < len(self.tabs) - 1:
            self.current_tab += 1
        else:
            # Create new tab with current state
            new_tab = {
                'path': self.current_path,
                'index': self.selected_idx,
                'search_mode': False,
                'search_query': "",
                'history': [],
                'history_index': -1,
                'sort_mode': self.sort_mode,
                'show_hidden': self.show_hidden
            }
            self.tabs.append(new_tab)
            self.current_tab = len(self.tabs) - 1
        self._load_tab()

    def _prev_tab(self):
        self._save_current_tab_state()
        if self.current_tab > 0:
            self.current_tab -= 1
            self._load_tab()

    def _save_current_tab_state(self):
        self.tabs[self.current_tab].update({
            'path': self.current_path,
            'index': self.selected_idx,
            'search_mode': self.search_mode,
            'search_query': self.search_query,
            'history': self.history.copy(),
            'history_index': self.history_index,
            'sort_mode': self.sort_mode,
            'show_hidden': self.show_hidden
        })

    def _load_tab(self):
        tab = self.tabs[self.current_tab]
        self.current_path = tab['path']
        self.selected_idx = tab['index']
        self.search_mode = tab.get('search_mode', False)
        self.search_query = tab.get('search_query', "")
        self.history = tab.get('history', [])
        self.history_index = tab.get('history_index', -1)
        self.sort_mode = tab.get('sort_mode', 'name')
        self.show_hidden = tab.get('show_hidden', False)
        self.refresh_files()

    def close_current_tab(self):
        if len(self.tabs) > 1:
            del self.tabs[self.current_tab]
            self.current_tab = min(self.current_tab, len(self.tabs)-1)
            self._load_tab()
            self.show_message("Tab closed", 2)

    def navigate_history_back(self):
        if self.history_index > 0:
            self.history_index -= 1
            self.current_path = self.history[self.history_index]
            self.refresh_files()

    def navigate_history_forward(self):
        if self.history_index < len(self.history) - 1:
            self.history_index += 1
            self.current_path = self.history[self.history_index]
            self.refresh_files()

    def toggle_selection(self):
        files = self.filtered_files if self.search_mode else self.files
        if self.selected_idx < len(files):
            filename = files[self.selected_idx]
            if filename in self.selected_files:
                self.selected_files.remove(filename)
            else:
                self.selected_files.add(filename)
            self.selected_idx = min(self.selected_idx + 1, len(files) - 1)

    def get_selected_files(self):
        if self.search_mode:
            selected = []
            files_list = self.filtered_files
            if self.selected_files:
                for filename in self.selected_files:
                    selected.append(os.path.join(self.search_base_path, filename))
            else:
                filename = files_list[self.selected_idx]
                selected.append(os.path.join(self.search_base_path, filename))
            return selected
        else:
            selected = []
            files_list = self.files
            if self.selected_files:
                for filename in self.selected_files:
                    selected.append(os.path.join(self.current_path, filename))
            else:
                filename = files_list[self.selected_idx]
                selected.append(os.path.join(self.current_path, filename))
            return selected

    def copy_files(self):
        self.clipboard['files'] = self.get_selected_files()
        self.clipboard['operation'] = 'copy'
        self.show_message(f"Copied {len(self.clipboard['files'])} items")
        self.selected_files.clear()

    def cut_files(self):
        self.clipboard['files'] = self.get_selected_files()
        self.clipboard['operation'] = 'cut'
        self.show_message(f"Cut {len(self.clipboard['files'])} items")
        self.selected_files.clear()

    def paste_files(self):
        if not self.clipboard['files']:
            return

        total_files = len(self.clipboard['files'])
        start_time = time.time()
        success_count = 0
        skipped_count = 0
        
        for i, src_path in enumerate(self.clipboard['files']):
            # Update progress every 100ms or for every file
            if time.time() - start_time > 0.1 or i == total_files - 1:
                self.draw_progress(i+1, total_files, 
                                f"Pasting ({'Copying' if self.clipboard['operation'] == 'copy' else 'Moving'})")
                start_time = time.time()
            
            try:
                dest_path = os.path.join(self.current_path, os.path.basename(src_path))
                
                if os.path.exists(dest_path):
                    if not self.confirm_action(f"Overwrite {os.path.basename(src_path)}?"):
                        skipped_count += 1
                        continue

                if self.clipboard['operation'] == 'copy':
                    if os.path.isdir(src_path):
                        shutil.copytree(src_path, dest_path, dirs_exist_ok=True)
                    else:
                        shutil.copy2(src_path, dest_path)
                else:
                    shutil.move(src_path, dest_path)
                    
                success_count += 1
            except Exception as e:
                self.show_message(f"Error: {str(e)}", 2)
        
        # Clear progress bar and show result
        self.stdscr.move(self.stdscr.getmaxyx()[0]-4, 2)
        self.stdscr.clrtoeol()
        
        result_msg = f"Pasted {success_count} items"
        if skipped_count > 0:
            result_msg += f", skipped {skipped_count}"
        self.show_message(result_msg, 3)

        if self.clipboard['operation'] == 'cut':
            self.clipboard['files'] = []
            self.clipboard['operation'] = None

        self.refresh_files()




















    def start_search(self):
        self.search_mode = True
        self.search_query = ""
        self.search_results = []
        self.search_base_path = self.current_path
        self.selected_idx = 0
        self.search_thread = threading.Thread(target=self.perform_search_action)
        self.search_thread.start()

    def cancel_search(self):
        self.search_mode = False
        self.search_query = ""
        self.search_results = []
        self.selected_idx = 0
        self.refresh_files()

    def _process_search_results(self):
        processed = 0
        while self.search_queue and processed < self.batch_size:
            try:
                item = self.search_queue.popleft()
                self.search_results.append(item)
                processed += 1
            except IndexError:
                break

        if processed > 0:
            self.apply_search_filter()

        if self.search_thread and not self.search_thread.is_alive():
            self.apply_search_filter()
            self.show_message("Search complete")

    def handle_search_input(self, key):
        if key == 27:  # ESC
            self.cancel_search()
        elif key in (curses.KEY_BACKSPACE, 127):
            self.search_query = self.search_query[:-1]
            self.apply_search_filter()
        elif key == curses.KEY_UP:
            self.selected_idx = max(0, self.selected_idx - 1)
        elif key == curses.KEY_DOWN:
            self.selected_idx = min(len(self.filtered_files) - 1, self.selected_idx + 1)
        elif key == 10:  # ENTER
            self.navigate_into_search_result()
        elif 32 <= key <= 126:
            self.search_query += chr(key)
            self.apply_search_filter()
        elif 32 <= key <= 126:
            self.search_query += chr(key)
            self.debounced_filter()
        
        self.selected_idx = max(0, min(self.selected_idx, len(self.filtered_files)-1))

    def debounced_filter(self):
        """Apply filter after 300ms pause in typing"""
        # Cancel any existing timer
        if self.search_timer:
            self.search_timer.cancel()

        now = time.time()
        
        # Apply immediately if no recent activity
        if now - self.last_key_time > 0.3:
            self.apply_search_filter()

        # Schedule new delayed filter
        self.search_timer = threading.Timer(0.3, self.apply_search_filter)
        self.search_timer.start()
        self.last_key_time = now

    def perform_search_action(self):
        """Optimized search with batched results"""
        self.search_results = []
        base_path = os.path.abspath(self.search_base_path)
        batch = []
        
        for root, dirs, files in os.walk(base_path):
            for entry in list(dirs) + files:
                rel_path = os.path.relpath(os.path.join(root, entry), base_path)
                batch.append(rel_path)
                
                if len(batch) >= self.batch_size:
                    with self.search_lock:
                        self.search_results.extend(batch)
                    batch = []
                    self.apply_search_filter()
                    time.sleep(0.01)  # Yield to main thread
            
            # Cancel search if mode changed
            if not self.search_mode:
                break
        
        # Process remaining items
        if batch:
            with self.search_lock:
                self.search_results.extend(batch)
        self.apply_search_filter()

    def navigate_into_search_result(self):
        if not self.filtered_files:
            return
        
        selected = self.filtered_files[self.selected_idx]
        full_path = os.path.join(self.search_base_path, selected)
        
        if os.path.isdir(full_path):
            self.current_path = full_path
            self.cancel_search()
            self.refresh_files()
        else:
            try:
                if platform.system() == 'Windows':
                    os.startfile(full_path)
                elif platform.system() == 'Darwin':
                    subprocess.run(["open", full_path], check=False)
                else:
                    subprocess.run(["xdg-open", full_path], check=False)
            except Exception as e:
                self.show_message(f"Error opening: {str(e)}", 2)
        self.cancel_search()


    def delete_files(self):
        targets = self.get_selected_files()
        if not self.confirm_action(f"Delete {len(targets)} items?"):
            return
        
        for path in targets:
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                self.show_message(f"Deleted {len(targets)} items")
            except Exception as e:
                self.show_message(f"Error: {str(e)}", 2)
        self.selected_files.clear()
        self.refresh_files()

    def confirm_action(self, prompt):
        height, width = self.stdscr.getmaxyx()
        # Clear any existing messages
        self.stdscr.move(height - 3, 2)
        self.stdscr.clrtoeol()
        
        # Draw confirmation prompt
        self.stdscr.addstr(height - 4, 2, f"{prompt} (y/n) ", curses.color_pair(4))
        self.stdscr.refresh()
        
        # Switch to blocking input with no timeout
        curses.cbreak()  # Disable line buffering
        self.stdscr.nodelay(False)  # Blocking input
        response = -1
        while response not in [ord('y'), ord('Y'), ord('n'), ord('N')]:
            response = self.stdscr.getch()
        
        # Restore input settings
        self.stdscr.nodelay(True)  # Restore main loop's non-blocking mode
        curses.nocbreak()
        self.stdscr.timeout(100)  # Restore main loop's timeout
        
        # Clear confirmation prompt
        self.stdscr.move(height - 4, 2)
        self.stdscr.clrtoeol()
        self.stdscr.refresh()
        
        return response in [ord('y'), ord('Y')]

    def navigate_up(self):
        new_path = os.path.dirname(self.current_path)
        if os.path.exists(new_path):
            self.navigate_to(new_path)
            self.selected_idx = 0

    def navigate_into(self):
        if not self.files:
            return
        selected = self.files[self.selected_idx]
        path = os.path.join(self.current_path, selected)
        
        if os.path.isdir(path):
            try:
                self.navigate_to(path)
                self.selected_idx = 0
            except PermissionError:
                self.show_message("Permission denied", 2)
        else:
            try:
                if platform.system() == 'Windows':
                    os.startfile(path)
                elif platform.system() == 'Darwin':
                    subprocess.run(['open', path], check=True)
                else:
                    subprocess.run(['xdg-open', path], check=True)
                self.show_message(f"Opened {selected}")
            except Exception as e:
                self.show_message(f"Error opening file: {str(e)}", 2)

    def navigate_to(self, path):
        if os.path.exists(path):
            self.add_history(self.current_path)
            self.current_path = path
            self.refresh_files()
            self.selected_idx = 0

    def add_history(self, path):
        """Maintain navigation history"""
        if self.history and self.history[self.history_index] == path:
            return
        self.history = self.history[:self.history_index+1]
        self.history.append(path)
        self.history_index = len(self.history) - 1

    def run(self):
        self.refresh_files()
        while True:
            if self.search_mode and self.search_thread and self.search_thread.is_alive():
                self._process_search_results()

            self.stdscr.clear()
            self.draw_borders()
            self.draw_header()
            self.draw_list()
            self.draw_footer()
            
            self.stdscr.timeout(100)
            key = self.stdscr.getch()
            if key != -1:
                self.handle_input(key)  

def main(stdscr):
    curses.curs_set(0)
    fm = FileManager(stdscr)
    fm.run()

if __name__ == "__main__":
    curses.wrapper(main)    