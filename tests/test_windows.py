from dotdocs.windows import focus_existing_window


class _FakeUser32:
    def __init__(self):
        self.shown = []
        self.foregrounded = []

    def EnumWindows(self, callback, _lparam):
        for hwnd in (101, 202):
            callback(hwnd, 0)
        return 1

    def IsWindowVisible(self, hwnd):
        return hwnd == 202

    def GetWindowTextLengthW(self, hwnd):
        return len("DocMarshal" if hwnd == 202 else "Other")

    def GetWindowTextW(self, hwnd, buffer, _length):
        buffer.value = "DocMarshal" if hwnd == 202 else "Other"
        return len(buffer.value)

    def IsIconic(self, _hwnd):
        return 1

    def ShowWindow(self, hwnd, command):
        self.shown.append((hwnd, command))
        return 1

    def BringWindowToTop(self, hwnd):
        return 1

    def SetForegroundWindow(self, hwnd):
        self.foregrounded.append(hwnd)
        return 1


def test_focus_existing_window_restores_and_foregrounds_exact_visible_title():
    user32 = _FakeUser32()

    assert focus_existing_window("DocMarshal", user32=user32)
    assert user32.shown == [(202, 9)]
    assert user32.foregrounded == [202]


def test_focus_existing_window_returns_false_when_no_matching_window():
    user32 = _FakeUser32()
    user32.IsWindowVisible = lambda _hwnd: 0

    assert not focus_existing_window("DocMarshal", user32=user32)
    assert user32.shown == []
    assert user32.foregrounded == []
