import customtkinter
import ctypes
import ctypes.wintypes
import io
import tkinter as tk
import time

from PIL import Image as PILImage, ImageTk

from app.config import POPUP_WIDTH, POPUP_HEIGHT, IMAGE_THUMB_SIZE, IMAGE_PREVIEW_SIZE, IMAGE_PREVIEW_DELAY

user32 = ctypes.windll.user32

# Fix ctypes prototypes for by-value struct and correct return types
user32.MonitorFromPoint.argtypes = [ctypes.wintypes.POINT, ctypes.wintypes.DWORD]
user32.MonitorFromPoint.restype = ctypes.wintypes.HMONITOR
user32.GetMonitorInfoW.argtypes = [ctypes.wintypes.HMONITOR, ctypes.c_void_p]
user32.GetMonitorInfoW.restype = ctypes.wintypes.BOOL
user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
user32.GetCursorPos.argtypes = [ctypes.POINTER(ctypes.wintypes.POINT)]
user32.GetCursorPos.restype = ctypes.wintypes.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

# Color palette
BG = "#0f0f0f"
SURFACE = "#1a1a1a"
SURFACE_HOVER = "#252525"
SURFACE_PINNED = "#1a1f1a"
SURFACE_PINNED_HOVER = "#222822"
SURFACE_SELECTED = "#2a2a2a"
BORDER = "#2a2a2a"
ACCENT_DIM = "#3d4f8a"
TEXT_PRIMARY = "#e8e8e8"
TEXT_SECONDARY = "#888888"
TEXT_DIM = "#555555"
PIN_COLOR = "#e8b931"
DANGER = "#c44"
IMAGE_BADGE = "#4a6fa5"
SEARCH_BG = "#161616"

PREVIEW_MAX_CHARS = 120
LARGE_TEXT_THRESHOLD = 500

# Fonts (created once, reused)
_FONT_ITEM = ("Segoe UI", 11)
_FONT_SMALL = ("Segoe UI", 9)
_FONT_SECTION = ("Segoe UI", 8)


def relative_time(timestamp):
    diff = time.time() - timestamp
    if diff < 0:
        return "now"
    if diff < 60:
        return "now"
    elif diff < 3600:
        return f"{int(diff // 60)}m"
    elif diff < 86400:
        return f"{int(diff // 3600)}h"
    elif diff < 172800:
        return "1d"
    else:
        return f"{int(diff // 86400)}d"


def _get_cursor_pos():
    point = ctypes.wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("rcMonitor", ctypes.wintypes.RECT),
        ("rcWork", ctypes.wintypes.RECT),
        ("dwFlags", ctypes.wintypes.DWORD),
    ]


def _get_monitor_work_area(x, y):
    """Return (left, top, right, bottom) of the work area on the monitor containing (x, y)."""
    MONITOR_DEFAULTTONEAREST = 2
    point = ctypes.wintypes.POINT(x, y)
    hmon = user32.MonitorFromPoint(point, MONITOR_DEFAULTTONEAREST)
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
        rc = info.rcWork
        return rc.left, rc.top, rc.right, rc.bottom
    # Fallback: use primary monitor via SystemMetrics
    w = user32.GetSystemMetrics(0)  # SM_CXSCREEN
    h = user32.GetSystemMetrics(1)  # SM_CYSCREEN
    if w <= 0 or h <= 0:
        w, h = 1920, 1080
    return 0, 0, w, h


def _set_bg_recursive(widget, bg):
    """Set background color on widget and all descendants."""
    try:
        widget.configure(bg=bg)
    except Exception:
        pass
    for child in widget.winfo_children():
        _set_bg_recursive(child, bg)


class PopupWindow(customtkinter.CTkToplevel):
    """Persistent popup window — created once, shown/hidden on demand."""

    def __init__(self, master, database, paste_engine, monitor=None):
        super().__init__(master)
        self.db = database
        self.paste_engine = paste_engine
        self.monitor = monitor
        self._master = master
        self._visible = False

        self._prev_hwnd = None
        self._selected_index = -1
        self._hovered_index = -1
        self._item_frames = []
        self._item_data = []
        self._search_after_id = None
        self._last_search_text = ""
        self._drag_x = 0
        self._drag_y = 0
        self._thumb_cache = {}
        self._preview_window = None
        self._preview_after_id = None
        self._preview_photo = None
        self._preview_entry_id = None
        self._confirm_clear = False
        self._clear_btn = None
        self._focus_check_id = None

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color=BG)
        self.geometry(f"{POPUP_WIDTH}x{POPUP_HEIGHT}+0+0")

        self._border_frame = customtkinter.CTkFrame(
            self, fg_color=BG, border_color=BORDER, border_width=1, corner_radius=12
        )
        self._border_frame.pack(fill="both", expand=True, padx=1, pady=1)

        self._build_ui()

        self.bind("<Escape>", lambda e: self.close())
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<Up>", lambda e: self._navigate(-1))
        self.bind("<Down>", lambda e: self._navigate(1))
        self.bind("<Return>", lambda e: self._paste_selected())
        self.bind("<Delete>", lambda e: self._delete_selected())
        self.bind("<Control-p>", lambda e: self._pin_selected())

        # Start hidden — show() will make it visible
        self.withdraw()

    # ------------------------------------------------------------------
    # Show / Close (hide)
    # ------------------------------------------------------------------

    def show(self, prev_hwnd=None):
        """Position near cursor, refresh items, and show the popup."""
        self._prev_hwnd = prev_hwnd
        self._visible = True

        # Position near cursor
        cx, cy = _get_cursor_pos()
        ml, mt, mr, mb = _get_monitor_work_area(cx, cy)
        x = max(ml + 10, min(cx - POPUP_WIDTH // 2, mr - POPUP_WIDTH - 10))
        y = max(mt + 10, min(cy - 40, mb - POPUP_HEIGHT - 10))
        self.geometry(f"{POPUP_WIDTH}x{POPUP_HEIGHT}+{x}+{y}")

        # Reset search
        self._last_search_text = ""
        try:
            self.search_entry.delete(0, "end")
        except Exception:
            pass

        # Reset clear confirmation
        self._confirm_clear = False
        try:
            self._clear_btn.configure(text="Clear all", text_color=TEXT_SECONDARY)
        except Exception:
            pass

        # Load fresh data
        self._load_items()

        # Reset scroll to top
        try:
            self._canvas.yview_moveto(0)
        except Exception:
            pass

        # Show and focus
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(10, self._focus_window)

    def close(self):
        """Hide the popup (does not destroy it)."""
        if not self._visible:
            return
        self._visible = False

        # Cancel pending timers
        self._hide_image_preview()
        if self._focus_check_id:
            try:
                self.after_cancel(self._focus_check_id)
            except Exception:
                pass
            self._focus_check_id = None
        if self._search_after_id:
            try:
                self.after_cancel(self._search_after_id)
            except Exception:
                pass
            self._search_after_id = None

        self.withdraw()

    @property
    def is_visible(self):
        return self._visible

    # ------------------------------------------------------------------
    # Focus
    # ------------------------------------------------------------------

    def _focus_window(self):
        if not self._visible:
            return
        try:
            self.focus_force()
            self.search_entry.focus_set()
        except Exception:
            pass

    def focus(self):
        if not self._visible:
            return
        self._focus_window()
        self.lift()

    # ------------------------------------------------------------------
    # UI (shell built once with CTk; list items use plain tk)
    # ------------------------------------------------------------------

    def _build_ui(self):
        c = self._border_frame

        # Header
        header = customtkinter.CTkFrame(c, fg_color="transparent", height=40)
        header.pack(fill="x", padx=14, pady=(10, 0))
        header.pack_propagate(False)

        title = customtkinter.CTkLabel(
            header, text="Clipboard",
            font=("Segoe UI Semibold", 15), text_color=TEXT_PRIMARY
        )
        title.pack(side="left")

        close_btn = customtkinter.CTkButton(
            header, text="\u00d7", width=26, height=26,
            font=("Segoe UI", 14), fg_color="transparent",
            hover_color=SURFACE_HOVER, text_color=TEXT_DIM,
            corner_radius=6, command=self.close
        )
        close_btn.pack(side="right")

        for w in [header, title]:
            w.bind("<Button-1>", self._start_drag)
            w.bind("<B1-Motion>", self._on_drag)

        # Search
        self.search_entry = customtkinter.CTkEntry(
            c, placeholder_text="Search...",
            font=("Segoe UI", 12), height=34,
            fg_color=SEARCH_BG, border_color=BORDER, border_width=1,
            text_color=TEXT_PRIMARY, placeholder_text_color=TEXT_DIM,
            corner_radius=8
        )
        self.search_entry.pack(fill="x", padx=14, pady=(8, 4))
        self.search_entry.bind("<KeyRelease>", self._on_search_change)

        # Scrollable list — plain tk Canvas + Frame (much faster than CTkScrollableFrame)
        list_container = tk.Frame(c._canvas if hasattr(c, '_canvas') else c, bg=BG)
        # Use the CTk frame's internal tk widget as parent
        list_container = tk.Frame(self._border_frame, bg=BG)
        list_container.pack(fill="both", expand=True, padx=6, pady=(2, 2))

        self._canvas = tk.Canvas(
            list_container, bg=BG, highlightthickness=0, borderwidth=0
        )
        self._scrollbar = tk.Scrollbar(
            list_container, orient="vertical", command=self._canvas.yview,
            bg="#1a1a1a", troughcolor=BG, width=8,
            activebackground="#444444", highlightthickness=0, borderwidth=0,
        )
        self._items_inner = tk.Frame(self._canvas, bg=BG)
        self._items_inner_id = self._canvas.create_window(
            (0, 0), window=self._items_inner, anchor="nw"
        )

        def _on_inner_configure(_e):
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))

        def _on_canvas_configure(e):
            self._canvas.itemconfigure(self._items_inner_id, width=e.width)

        self._items_inner.bind("<Configure>", _on_inner_configure)
        self._canvas.bind("<Configure>", _on_canvas_configure)
        self._canvas.configure(yscrollcommand=self._scrollbar.set)

        self._canvas.pack(side="left", fill="both", expand=True)
        self._scrollbar.pack(side="right", fill="y")

        # Mouse wheel scrolling
        def _on_mousewheel(event):
            self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        self._canvas.bind("<MouseWheel>", _on_mousewheel)
        self._items_inner.bind("<MouseWheel>", _on_mousewheel)
        # Also bind on the toplevel so wheel works everywhere
        self.bind("<MouseWheel>", _on_mousewheel)

        self._canvas.configure(yscrollincrement=20)

        # Footer
        footer = customtkinter.CTkFrame(c, fg_color="transparent", height=30)
        footer.pack(fill="x", padx=14, pady=(0, 8))
        footer.pack_propagate(False)

        self.count_label = customtkinter.CTkLabel(
            footer, text="", font=("Segoe UI", 10), text_color=TEXT_DIM
        )
        self.count_label.pack(side="left")

        self._clear_btn = customtkinter.CTkButton(
            footer, text="Clear all", width=60, height=22,
            font=("Segoe UI", 10), fg_color="transparent",
            hover_color=SURFACE_HOVER, text_color=TEXT_SECONDARY,
            corner_radius=4, command=self._clear_all
        )
        self._clear_btn.pack(side="right")

    # ------------------------------------------------------------------
    # Item list (all plain tk for speed)
    # ------------------------------------------------------------------

    def _load_items(self, search_query=None):
        if not self._visible:
            return

        self._hide_image_preview()

        for widget in self._items_inner.winfo_children():
            widget.destroy()

        self._item_frames = []
        self._item_data = []
        self._selected_index = -1
        self._hovered_index = -1
        old_cache = self._thumb_cache
        self._thumb_cache = {}

        entries = self.db.get_history(limit=30, search_query=search_query)

        if not entries:
            empty = tk.Label(
                self._items_inner,
                text="Nothing here yet\nCopy something to get started",
                font=("Segoe UI", 12), fg=TEXT_DIM, bg=BG, justify="center"
            )
            empty.pack(pady=50)
            self.count_label.configure(text="0 items")
            return

        has_pinned = any(e["pinned"] for e in entries)
        shown_unpinned_header = False

        for entry in entries:
            if has_pinned and not entry["pinned"] and not shown_unpinned_header:
                shown_unpinned_header = True
                tk.Label(
                    self._items_inner, text="HISTORY",
                    font=_FONT_SECTION, fg=TEXT_DIM, bg=BG, anchor="w"
                ).pack(fill="x", padx=10, pady=(6, 2))

            if has_pinned and entry["pinned"] and not shown_unpinned_header and len(self._item_frames) == 0:
                tk.Label(
                    self._items_inner, text="PINNED",
                    font=_FONT_SECTION, fg=PIN_COLOR, bg=BG, anchor="w"
                ).pack(fill="x", padx=10, pady=(2, 2))

            idx = len(self._item_frames)
            frame = self._create_item_widget(entry, idx, old_cache)
            self._item_frames.append(frame)
            self._item_data.append(entry)

        n = len(entries)
        self.count_label.configure(text=f"{n} item{'s' if n != 1 else ''}")

    def _create_item_widget(self, entry, index, old_thumb_cache=None):
        is_pinned = entry["pinned"]
        is_image = entry["content_type"] == "image"
        normal_bg = SURFACE_PINNED if is_pinned else SURFACE
        hover_bg = SURFACE_PINNED_HOVER if is_pinned else SURFACE_HOVER

        frame = tk.Frame(self._items_inner, bg=normal_bg, cursor="hand2", padx=0, pady=0)
        frame.pack(fill="x", padx=3, pady=1)

        clickable = [frame]

        if is_image:
            row = tk.Frame(frame, bg=normal_bg)
            row.pack(fill="x", padx=10, pady=5)
            clickable.append(row)

            thumb = self._create_thumbnail(row, entry["id"], normal_bg, old_thumb_cache)
            if thumb:
                thumb.pack(side="left", padx=(0, 8))
                clickable.append(thumb)

            info = tk.Frame(row, bg=normal_bg)
            info.pack(side="left", fill="x", expand=True)
            clickable.append(info)

            badge = tk.Label(
                info, text=f"IMAGE  \u00b7  {entry['preview'] or 'Image'}",
                font=_FONT_ITEM, fg=IMAGE_BADGE, bg=normal_bg, anchor="w"
            )
            badge.pack(anchor="w")
            clickable.append(badge)

            bot = tk.Frame(info, bg=normal_bg)
            bot.pack(fill="x")
            clickable.append(bot)
        else:
            preview_text = entry["preview"] or ""
            content_len = entry.get("content_len", 0) or 0

            if len(preview_text) > PREVIEW_MAX_CHARS:
                preview_text = preview_text[:PREVIEW_MAX_CHARS] + "..."

            preview = tk.Label(
                frame, text=preview_text,
                font=_FONT_ITEM, fg=TEXT_PRIMARY, bg=normal_bg,
                anchor="w", justify="left",
                wraplength=POPUP_WIDTH - 120
            )
            preview.pack(fill="x", padx=10, pady=(5, 0))
            clickable.append(preview)

            bot = tk.Frame(frame, bg=normal_bg)
            bot.pack(fill="x", padx=10, pady=(1, 4))
            clickable.append(bot)

        # Time label
        time_text = relative_time(entry["timestamp"])
        if is_pinned:
            time_text = "Pinned \u00b7 " + time_text

        time_lbl = tk.Label(
            bot, text=time_text, font=_FONT_SMALL,
            fg=PIN_COLOR if is_pinned else TEXT_DIM, bg=normal_bg
        )
        time_lbl.pack(side="left")
        clickable.append(time_lbl)

        if not is_image and content_len >= LARGE_TEXT_THRESHOLD:
            chars_lbl = tk.Label(
                bot, text=f"  \u00b7  {content_len:,} chars",
                font=_FONT_SMALL, fg=TEXT_DIM, bg=normal_bg
            )
            chars_lbl.pack(side="left")
            clickable.append(chars_lbl)

        # Action "buttons" — plain tk labels with hover effects
        del_btn = tk.Label(
            bot, text="Del", font=_FONT_SMALL,
            fg=TEXT_DIM, bg=normal_bg, cursor="hand2", padx=4
        )
        del_btn.pack(side="right")
        del_btn.bind("<Enter>", lambda _e, w=del_btn: w.configure(fg=DANGER))
        del_btn.bind("<Leave>", lambda _e, w=del_btn: w.configure(fg=TEXT_DIM))
        del_btn.bind("<Button-1>", lambda _e, eid=entry["id"]: self._delete_item(eid))

        pin_text = "Unpin" if is_pinned else "Pin"
        pin_btn = tk.Label(
            bot, text=pin_text, font=_FONT_SMALL,
            fg=TEXT_SECONDARY, bg=normal_bg, cursor="hand2", padx=4
        )
        pin_btn.pack(side="right")
        pin_btn.bind("<Enter>", lambda _e, w=pin_btn: w.configure(fg=TEXT_PRIMARY))
        pin_btn.bind("<Leave>", lambda _e, w=pin_btn: w.configure(fg=TEXT_SECONDARY))
        pin_btn.bind("<Button-1>", lambda _e, eid=entry["id"]: self._toggle_pin(eid))

        # Hover
        def on_enter(_e):
            self._hovered_index = index
            if index != self._selected_index:
                _set_bg_recursive(frame, hover_bg)
            if is_image:
                if self._preview_entry_id != entry["id"]:
                    self._hide_image_preview()
                    self._preview_after_id = self.after(
                        IMAGE_PREVIEW_DELAY,
                        lambda: self._show_image_preview(entry["id"], frame)
                    )

        def on_leave(_e):
            # Check if cursor is still inside the frame
            try:
                w = self.winfo_containing(_e.x_root, _e.y_root)
                while w is not None:
                    if w is frame:
                        return
                    w = w.master
            except Exception:
                pass
            self._hovered_index = -1
            if index != self._selected_index:
                _set_bg_recursive(frame, normal_bg)
            if is_image:
                self._hide_image_preview()

        frame.bind("<Enter>", on_enter)
        frame.bind("<Leave>", on_leave)

        for w in clickable:
            w.bind("<Button-1>", lambda _e, eid=entry["id"]: self._on_item_click(eid))

        return frame

    # ------------------------------------------------------------------
    # Thumbnails & image preview
    # ------------------------------------------------------------------

    def _create_thumbnail(self, parent, entry_id, bg_color, old_cache=None):
        try:
            if old_cache and entry_id in old_cache:
                tk_img = old_cache[entry_id]
                label = tk.Label(parent, image=tk_img, bg=bg_color, borderwidth=0)
                self._thumb_cache[entry_id] = tk_img
                return label

            image_data = self.db.get_image_data(entry_id)
            if not image_data:
                return None

            img = PILImage.open(io.BytesIO(image_data))
            try:
                img.thumbnail(IMAGE_THUMB_SIZE, PILImage.Resampling.LANCZOS)
                tk_img = ImageTk.PhotoImage(img)
            finally:
                img.close()

            label = tk.Label(parent, image=tk_img, bg=bg_color, borderwidth=0)
            self._thumb_cache[entry_id] = tk_img
            return label
        except Exception:
            return None

    def _show_image_preview(self, entry_id, widget):
        self._hide_image_preview()
        if not self._visible:
            return
        try:
            image_data = self.db.get_image_data(entry_id)
            if not image_data:
                return

            img = PILImage.open(io.BytesIO(image_data))
            try:
                img.thumbnail(IMAGE_PREVIEW_SIZE, PILImage.Resampling.LANCZOS)
                tk_img = ImageTk.PhotoImage(img)
            finally:
                img.close()

            preview_win = tk.Toplevel(self)
            self._preview_window = preview_win
            self._preview_entry_id = entry_id
            preview_win.overrideredirect(True)
            preview_win.attributes("-topmost", True)
            preview_win.configure(bg=BG)

            border = tk.Frame(
                preview_win, bg=BG, highlightbackground=BORDER,
                highlightthickness=1
            )
            border.pack(fill="both", expand=True, padx=1, pady=1)

            self._preview_photo = tk_img

            label = tk.Label(border, image=tk_img, bg=BG, borderwidth=0)
            label.pack(padx=6, pady=6)

            # Position: right of popup, or left if no space
            self.update_idletasks()
            preview_win.update_idletasks()
            pw = preview_win.winfo_reqwidth()
            ph = preview_win.winfo_reqheight()
            popup_x = self.winfo_x()
            popup_w = self.winfo_width()

            ml, mt, mr, mb = _get_monitor_work_area(popup_x + popup_w // 2, self.winfo_y())

            if popup_x + popup_w + pw + 10 < mr:
                px = popup_x + popup_w + 8
            else:
                px = popup_x - pw - 8

            try:
                wy = widget.winfo_rooty()
            except Exception:
                wy = self.winfo_y()
            py = max(mt + 10, min(wy, mb - ph - 10))

            preview_win.geometry(f"+{px}+{py}")
        except Exception:
            self._hide_image_preview()

    def _hide_image_preview(self):
        if self._preview_after_id:
            try:
                self.after_cancel(self._preview_after_id)
            except Exception:
                pass
            self._preview_after_id = None
        if self._preview_window:
            try:
                self._preview_window.destroy()
            except Exception:
                pass
            self._preview_window = None
        self._preview_photo = None
        self._preview_entry_id = None

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _on_search_change(self, event=None):
        if not self._visible:
            return
        try:
            current = self.search_entry.get()
        except Exception:
            return
        if current == self._last_search_text:
            return
        self._last_search_text = current
        if self._search_after_id:
            self.after_cancel(self._search_after_id)
        self._search_after_id = self.after(150, self._do_search)

    def _do_search(self):
        if not self._visible:
            return
        try:
            query = self.search_entry.get().strip() or None
        except Exception:
            return
        self._load_items(query)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _get_item_normal_bg(self, index):
        if 0 <= index < len(self._item_data):
            return SURFACE_PINNED if self._item_data[index]["pinned"] else SURFACE
        return SURFACE

    def _navigate(self, direction):
        if not self._item_frames:
            return

        if 0 <= self._selected_index < len(self._item_frames):
            _set_bg_recursive(
                self._item_frames[self._selected_index],
                self._get_item_normal_bg(self._selected_index)
            )

        if 0 <= self._hovered_index < len(self._item_frames) and self._hovered_index != self._selected_index:
            _set_bg_recursive(
                self._item_frames[self._hovered_index],
                self._get_item_normal_bg(self._hovered_index)
            )

        self._selected_index += direction
        self._selected_index = max(0, min(self._selected_index, len(self._item_frames) - 1))
        _set_bg_recursive(self._item_frames[self._selected_index], SURFACE_SELECTED)

        # Scroll selected item into view
        try:
            frame = self._item_frames[self._selected_index]
            self._canvas.update_idletasks()
            bbox = self._canvas.bbox("all")
            if bbox is None:
                return
            total_height = bbox[3]
            if total_height <= 0:
                return
            fy = frame.winfo_y()
            fh = frame.winfo_height()
            canvas_h = self._canvas.winfo_height()
            visible_top = self._canvas.canvasy(0)
            visible_bottom = visible_top + canvas_h
            if fy < visible_top:
                self._canvas.yview_moveto(fy / total_height)
            elif fy + fh > visible_bottom:
                self._canvas.yview_moveto((fy + fh - canvas_h) / total_height)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _paste_selected(self):
        if 0 <= self._selected_index < len(self._item_data):
            entry_id = self._item_data[self._selected_index]["id"]
            self._on_item_click(entry_id)

    def _delete_selected(self):
        if 0 <= self._selected_index < len(self._item_data):
            entry_id = self._item_data[self._selected_index]["id"]
            self._delete_item(entry_id)

    def _pin_selected(self):
        if 0 <= self._selected_index < len(self._item_data):
            entry_id = self._item_data[self._selected_index]["id"]
            self._toggle_pin(entry_id)

    def _on_item_click(self, entry_id):
        entry = self.db.get_entry(entry_id)
        if not entry:
            return

        prev_hwnd = self._prev_hwnd
        content = entry["content"]
        content_type = entry.get("content_type", "text")
        image_data = entry.get("image_data") if content_type == "image" else None
        monitor = self.monitor
        paste_engine = self.paste_engine

        self.close()

        paste_engine.paste(content, content_type, prev_hwnd, monitor, image_data=image_data)

    def _toggle_pin(self, entry_id):
        self.db.toggle_pin(entry_id)
        search = self.search_entry.get().strip() or None
        self._load_items(search)

    def _delete_item(self, entry_id):
        self.db.delete_entry(entry_id)
        search = self.search_entry.get().strip() or None
        self._load_items(search)

    def _clear_all(self):
        if self._confirm_clear:
            self.db.clear_all()
            self._confirm_clear = False
            search = self.search_entry.get().strip() or None
            self._load_items(search)
        else:
            self._confirm_clear = True
            self._clear_btn.configure(text="Sure?", text_color=DANGER)
            self.after(2000, self._reset_clear_confirm)

    def _reset_clear_confirm(self):
        if not self._visible:
            return
        self._confirm_clear = False
        try:
            self._clear_btn.configure(text="Clear all", text_color=TEXT_SECONDARY)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Focus management
    # ------------------------------------------------------------------

    def _on_focus_out(self, event):
        if not self._visible:
            return
        if self._focus_check_id is not None:
            self.after_cancel(self._focus_check_id)
        self._focus_check_id = self.after(80, lambda: self._check_focus(0))

    def _check_focus(self, attempt):
        self._focus_check_id = None
        if not self._visible:
            return
        try:
            focused = self.focus_get()
            if focused is not None:
                return
            if self._preview_window is not None:
                try:
                    preview_focused = self._preview_window.focus_get()
                    if preview_focused is not None:
                        return
                except Exception:
                    pass
            foreground = user32.GetForegroundWindow()
            try:
                own_hwnd = int(self.wm_frame(), 16)
                if own_hwnd and foreground == own_hwnd:
                    return
            except Exception:
                pass
            if self._preview_window is not None:
                try:
                    preview_hwnd = int(self._preview_window.wm_frame(), 16)
                    if preview_hwnd and foreground == preview_hwnd:
                        return
                except Exception:
                    pass
            if attempt < 1:
                self.after(80, lambda: self._check_focus(attempt + 1))
                return
            self.close()
        except Exception:
            self.close()

    # ------------------------------------------------------------------
    # Drag
    # ------------------------------------------------------------------

    def _start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _on_drag(self, event):
        x = self.winfo_x() + event.x - self._drag_x
        y = self.winfo_y() + event.y - self._drag_y
        self.geometry(f"+{x}+{y}")
