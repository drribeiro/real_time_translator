"""macOS audio device routing via SwitchAudioSource."""

import atexit
import subprocess
import shutil


def _has_switch_audio():
    return shutil.which("SwitchAudioSource") is not None


def get_current_output() -> str | None:
    """Get current system output device name."""
    if not _has_switch_audio():
        return None
    result = subprocess.run(
        ["SwitchAudioSource", "-c", "-t", "output"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() or None


def get_current_input() -> str | None:
    """Get current system input device name."""
    if not _has_switch_audio():
        return None
    result = subprocess.run(
        ["SwitchAudioSource", "-c", "-t", "input"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() or None


def set_output_device(name: str) -> bool:
    """Set system output device by name."""
    if not _has_switch_audio():
        return False
    result = subprocess.run(
        ["SwitchAudioSource", "-s", name, "-t", "output"],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def set_input_device(name: str) -> bool:
    """Set system input device by name."""
    if not _has_switch_audio():
        return False
    result = subprocess.run(
        ["SwitchAudioSource", "-s", name, "-t", "input"],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def list_output_devices() -> list[str]:
    """List all output device names."""
    if not _has_switch_audio():
        return []
    result = subprocess.run(
        ["SwitchAudioSource", "-a", "-t", "output"],
        capture_output=True, text=True,
    )
    return [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]


class AudioRouter:
    """Manages audio routing changes and restores on exit."""

    def __init__(self):
        self._original_output = None
        self._original_input = None
        self._active = False
        atexit.register(self.restore)

    def redirect_output_to_blackhole(self):
        """Route system output to capture device (mutes original audio)."""
        from config import BLACKHOLE_2CH
        if not self._original_output:
            self._original_output = get_current_output()
        if set_output_device(BLACKHOLE_2CH):
            self._active = True
            return True
        return False

    def redirect_input_to_blackhole(self):
        """Route system input to virtual mic (suppresses real mic)."""
        from config import BLACKHOLE_16CH
        if not self._original_input:
            self._original_input = get_current_input()
        if set_input_device(BLACKHOLE_16CH):
            self._active = True
            return True
        return False

    def restore_output(self):
        """Restore original system output device."""
        if self._original_output:
            set_output_device(self._original_output)
            self._original_output = None

    def restore_input(self):
        """Restore original system input device."""
        if self._original_input:
            set_input_device(self._original_input)
            self._original_input = None

    def restore(self):
        """Restore all original audio routing."""
        self.restore_output()
        self.restore_input()
        self._active = False


if __name__ == "__main__":
    print(f"Current output: {get_current_output()}")
    print(f"Current input:  {get_current_input()}")
    print(f"Output devices: {list_output_devices()}")
    print(f"SwitchAudioSource available: {_has_switch_audio()}")
