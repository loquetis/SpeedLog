"""Windows helpers for SpeedLog: start-with-Windows (HKCU Run) and a minimal tray icon (ctypes, no extra packages)."""
import os
import sys
import threading

IS_WIN = os.name == "nt"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "SpeedLog"


def start_command():
    if getattr(sys, "frozen", False):
        return '"%s" --background' % sys.executable
    return '"%s" "%s" --background' % (sys.executable, os.path.abspath(sys.argv[0]))


def autostart_get():
    """Returns the Run command if SpeedLog is registered to start with Windows, else None."""
    if not IS_WIN:
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            return winreg.QueryValueEx(k, RUN_NAME)[0]
    except OSError:
        return None


def autostart_set(on):
    if not IS_WIN:
        raise OSError("Start with Windows is only available on Windows")
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, start_command())
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except FileNotFoundError:
                pass
    return autostart_get()


class Tray:
    """Notification-area icon with a right-click menu. Runs its own message-loop thread.
    Callbacks run on that thread, so keep them short."""
    WM_APP_TRAY = 0x8000 + 1
    WM_RBUTTONUP, WM_LBUTTONDBLCLK, WM_LBUTTONUP = 0x205, 0x203, 0x202

    def __init__(self, tip, on_open, on_run, on_quit, log=None):
        self.tip, self.on_open, self.on_run, self.on_quit = tip, on_open, on_run, on_quit
        self.log = log or (lambda *a: None)
        self.ready = threading.Event()
        self.ok = False
        self.hwnd = None
        self.thread = None

    def start(self):
        if not IS_WIN:
            return False
        self.thread = threading.Thread(target=self._loop, name="tray", daemon=True)
        self.thread.start()
        self.ready.wait(5)
        return self.ok

    def stop(self):
        if IS_WIN and self.hwnd:
            import ctypes
            from ctypes import wintypes
            pm = ctypes.windll.user32.PostMessageW
            pm.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            pm(self.hwnd, 0x10, 0, 0)   # WM_CLOSE

    def _loop(self):
        import ctypes
        from ctypes import wintypes
        user32, shell32, kernel32 = ctypes.windll.user32, ctypes.windll.shell32, ctypes.windll.kernel32
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        H = wintypes
        kernel32.GetModuleHandleW.restype, kernel32.GetModuleHandleW.argtypes = H.HMODULE, [H.LPCWSTR]
        user32.CreateWindowExW.restype = H.HWND
        user32.CreateWindowExW.argtypes = [H.DWORD, H.LPCWSTR, H.LPCWSTR, H.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, H.HWND, H.HMENU, H.HINSTANCE, H.LPVOID]
        user32.DestroyWindow.argtypes = [H.HWND]
        user32.PostMessageW.argtypes = [H.HWND, H.UINT, H.WPARAM, H.LPARAM]
        user32.CreatePopupMenu.restype = H.HMENU
        user32.AppendMenuW.argtypes = [H.HMENU, H.UINT, ctypes.c_size_t, H.LPCWSTR]
        user32.TrackPopupMenu.argtypes = [H.HMENU, H.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, H.HWND, ctypes.c_void_p]
        user32.DestroyMenu.argtypes = [H.HMENU]
        user32.SetForegroundWindow.argtypes = [H.HWND]
        user32.GetMessageW.argtypes = [ctypes.POINTER(H.MSG), H.HWND, H.UINT, H.UINT]
        user32.DefWindowProcW.restype = LRESULT
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                        ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                        ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 128),
                        ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                        ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD)]
        shell32.Shell_NotifyIconW.argtypes = [H.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
        shell32.ExtractIconExW.argtypes = [H.LPCWSTR, ctypes.c_int, ctypes.POINTER(H.HICON), ctypes.POINTER(H.HICON), H.UINT]
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        ID_OPEN, ID_RUN, ID_QUIT = 1001, 1002, 1003

        def show_menu(hwnd):
            menu = user32.CreatePopupMenu()
            user32.AppendMenuW(menu, 0, ID_OPEN, "Open SpeedLog")
            user32.AppendMenuW(menu, 0, ID_RUN, "Run a test now")
            user32.AppendMenuW(menu, 0x800, 0, None)          # separator
            user32.AppendMenuW(menu, 0, ID_QUIT, "Quit SpeedLog")
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            user32.SetForegroundWindow(hwnd)
            cmd = user32.TrackPopupMenu(menu, 0x100 | 0x80, pt.x, pt.y, 0, hwnd, None)   # TPM_RETURNCMD | TPM_NONOTIFY
            user32.DestroyMenu(menu)
            return cmd

        def proc(hwnd, msg, wp, lp):
            try:
                if msg == self.WM_APP_TRAY:
                    if lp in (self.WM_LBUTTONDBLCLK, self.WM_LBUTTONUP):
                        self.on_open()
                    elif lp == self.WM_RBUTTONUP:
                        cmd = show_menu(hwnd)
                        {ID_OPEN: self.on_open, ID_RUN: self.on_run, ID_QUIT: self.on_quit}.get(cmd, lambda: None)()
                    return 0
                if msg == 0x10:        # WM_CLOSE
                    shell32.Shell_NotifyIconW(2, ctypes.byref(nid))     # NIM_DELETE
                    user32.DestroyWindow(hwnd)
                    return 0
                if msg == 0x2:         # WM_DESTROY
                    user32.PostQuitMessage(0)
                    return 0
            except Exception as e:  # noqa: BLE001
                self.log("tray callback failed: %r" % (e,))
            return user32.DefWindowProcW(hwnd, msg, wp, lp)
        self._proc = WNDPROC(proc)          # keep a reference or it is garbage collected
        try:
            hinst = kernel32.GetModuleHandleW(None)
            wc = WNDCLASSW()
            wc.lpfnWndProc = self._proc
            wc.hInstance = hinst
            wc.lpszClassName = "SpeedLogTrayWindow"
            user32.RegisterClassW(ctypes.byref(wc))
            hwnd = user32.CreateWindowExW(0, wc.lpszClassName, "SpeedLog tray", 0, 0, 0, 0, 0, None, None, hinst, None)
            if not hwnd:
                raise OSError("CreateWindowExW failed")
            self.hwnd = hwnd
            icon = None
            if getattr(sys, "frozen", False):
                large, small = wintypes.HICON(), wintypes.HICON()
                if shell32.ExtractIconExW(sys.executable, 0, None, ctypes.byref(small), 1) > 0:
                    icon = small
            if not icon or not icon.value:
                user32.LoadIconW.restype = wintypes.HICON
                user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
                icon = wintypes.HICON(user32.LoadIconW(None, ctypes.c_void_p(32512)))      # IDI_APPLICATION
            nid.hWnd, nid.uID, nid.uFlags = hwnd, 1, 0x1 | 0x2 | 0x4          # NIF_MESSAGE | NIF_ICON | NIF_TIP
            nid.uCallbackMessage = self.WM_APP_TRAY
            nid.hIcon = icon
            nid.szTip = self.tip[:127]
            self.ok = bool(shell32.Shell_NotifyIconW(0, ctypes.byref(nid)))      # NIM_ADD
            self.log("tray icon added: %s" % self.ok)
        except Exception as e:  # noqa: BLE001
            self.log("tray failed: %r" % (e,))
        finally:
            self.ready.set()
        if not self.ok:
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
