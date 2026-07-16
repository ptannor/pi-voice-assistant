import os
import sys
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
MAC_BIN_DIR = REPO_ROOT / ".respeaker_xvf3800" / "host_control" / "mac_arm64"
PI_BIN_DIR = REPO_ROOT / ".respeaker_xvf3800" / "host_control" / "rpi_64bit"

def _run_xvf_host(command: str, value: str | None = None) -> bool:
    """Helper to run the platform-appropriate xvf_host binary."""
    try:
        is_mac = sys.platform == "darwin"
        
        if is_mac:
            bin_dir = MAC_BIN_DIR
            env_var = "DYLD_LIBRARY_PATH"
        else:
            bin_dir = PI_BIN_DIR
            env_var = "LD_LIBRARY_PATH"
            
        xvf_host_path = bin_dir / "xvf_host"
        
        if not xvf_host_path.exists():
            return False
            
        args = [str(xvf_host_path), command]
        if value is not None:
            args.append(value)
            
        env = os.environ.copy()
        env[env_var] = str(bin_dir)
        
        subprocess.run(
            args,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=sys.stderr,
            check=True,
            cwd=str(bin_dir)
        )
        return True
    except Exception:
        return False

def set_led_off():
    _run_xvf_host("LED_EFFECT", "0")

def set_led_listening():
    # Set to single color mode (3) and use Cyan (0x00FFFF) for solid, instant feedback
    _run_xvf_host("LED_EFFECT", "3")
    _run_xvf_host("LED_COLOR", "0x00FFFF")

def set_led_thinking():
    # 2 is Rainbow - colorful spinning animation indicating thinking
    _run_xvf_host("LED_EFFECT", "2")

def set_led_speaking():
    # 1 is Breath - gentle pulsing cyan/blue light while speaking
    _run_xvf_host("LED_EFFECT", "1")
    _run_xvf_host("LED_COLOR", "0x00FFFF")

