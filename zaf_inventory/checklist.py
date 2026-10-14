"""The device test checklist (no Qt imports, so it can be tested on its own).

Each test is (key, label, applies). ``applies`` is None for tests every phone
should be put through, or "apple" / "android" for features only that platform has,
which lets the app mark the others N/A in one click. Keys are stored in the
database, so never rename one - add a new test instead.
"""
PASS, FAIL, NA = "Pass", "Fail", "N/A"
RESULTS = (PASS, FAIL, NA)
APPLE, ANDROID = "apple", "android"
NOTE_FOR = {APPLE: "Apple only", ANDROID: "Android only"}
CUSTOM_PREFIX = "custom:"
CUSTOM_SECTION = "Custom tests"

SECTIONS = [
    ("Power & Charging", [
        ("power_on", "Powers on and boots to the home screen", None),
        ("power_btn", "Power / side button works", None),
        ("reboot", "Restarts normally, no random reboots or freezes", None),
        ("charge_wired", "Charges with a cable", None),
        ("charge_port", "Charging port: cable seats firmly, no wobble", None),
        ("charge_fast", "Fast charging", None),
        ("charge_wireless", "Wireless charging", None),
        ("battery_hold", "Battery holds charge, no sudden drops", None),
        ("battery_ok", "Battery health acceptable (see Device details)", None),
        ("heat", "No overheating during use or charging", None),
    ]),
    ("Display & Touch", [
        ("lcd_pixels", "No dead pixels, lines or spots", None),
        ("lcd_burn", "No burn-in, ghosting or image retention", None),
        ("lcd_tint", "Even colour: no yellow tint or backlight bleed", None),
        ("lcd_bright", "Brightness and auto-brightness", None),
        ("lcd_touch", "Touch responds across the whole screen", None),
        ("lcd_multi", "Multi-touch (pinch and zoom)", None),
        ("lcd_gesture", "Edge swipes and gestures", None),
        ("lcd_rotate", "Auto-rotate", None),
        ("lcd_truetone", "True Tone", APPLE),
        ("lcd_haptic", "3D Touch / Haptic Touch", APPLE),
        ("lcd_refresh", "High refresh rate (90 / 120 Hz)", None),
        ("lcd_aod", "Always-on display", None),
        ("lcd_pen", "Stylus / S Pen", ANDROID),
    ]),
    ("Cameras", [
        ("cam_rear_photo", "Rear camera: photo", None),
        ("cam_rear_video", "Rear camera: video recording", None),
        ("cam_rear_focus", "Rear camera: autofocus", None),
        ("cam_ois", "Optical image stabilisation", None),
        ("cam_wide", "Ultra-wide camera", None),
        ("cam_tele", "Telephoto / zoom camera", None),
        ("cam_macro", "Macro / depth camera", None),
        ("cam_portrait", "Portrait mode", None),
        ("cam_flash", "Flash / torch", None),
        ("cam_front_photo", "Front camera: photo", None),
        ("cam_front_video", "Front camera: video", None),
        ("cam_lens_clean", "Lenses clean: no dust or fogging inside", None),
    ]),
    ("Audio", [
        ("aud_ear", "Earpiece (call audio)", None),
        ("aud_loud", "Loudspeaker", None),
        ("aud_stereo", "Stereo speakers (top and bottom)", None),
        ("aud_mic_bottom", "Microphone: bottom", None),
        ("aud_mic_top", "Microphone: rear / video", None),
        ("aud_jack", "Headphone jack", None),
        ("aud_wired", "Wired headphones (jack or adapter)", None),
        ("aud_speakerphone", "Speakerphone call: hear and be heard", None),
    ]),
    ("Network & Connectivity", [
        ("net_sim", "SIM detected and network registers", None),
        ("net_call_out", "Make a call", None),
        ("net_call_in", "Receive a call", None),
        ("net_sms", "Send and receive SMS", None),
        ("net_data", "Mobile data (4G / 5G)", None),
        ("net_5g", "5G", None),
        ("net_volte", "VoLTE / Wi-Fi calling", None),
        ("net_dual", "Dual SIM", None),
        ("net_esim", "eSIM", None),
        ("net_wifi24", "Wi-Fi 2.4 GHz", None),
        ("net_wifi5", "Wi-Fi 5 GHz", None),
        ("net_bt", "Bluetooth pairs and connects", None),
        ("net_gps", "GPS / location", None),
        ("net_nfc", "NFC / contactless payments", None),
        ("net_hotspot", "Personal hotspot", None),
    ]),
    ("Sensors & Haptics", [
        ("sen_prox", "Proximity sensor (screen off during a call)", None),
        ("sen_light", "Ambient light sensor", None),
        ("sen_motion", "Accelerometer / gyroscope", None),
        ("sen_compass", "Compass / magnetometer", None),
        ("sen_baro", "Barometer", None),
        ("sen_vib", "Vibration motor / Taptic Engine", None),
        ("sen_fp", "Fingerprint sensor / Touch ID", None),
        ("sen_face", "Face ID / face unlock", None),
        ("sen_lidar", "LiDAR scanner", None),
    ]),
    ("Buttons & Ports", [
        ("btn_volup", "Volume up", None),
        ("btn_voldown", "Volume down", None),
        ("btn_mute", "Mute switch / Action button", APPLE),
        ("btn_home", "Home button", None),
        ("btn_simtray", "SIM tray opens and seats correctly", None),
        ("btn_ports", "Ports clean: no corrosion or debris", None),
    ]),
    ("Software & Security", [
        ("sw_icloud", "iCloud / Activation Lock is off", APPLE),
        ("sw_frp", "Google account / FRP lock is off", ANDROID),
        ("sw_mdm", "No MDM / remote management / enrolment", None),
        ("sw_netlock", "Network unlocked (or carrier lock confirmed)", None),
        ("sw_imei", "IMEI clean: not blacklisted or barred", None),
        ("sw_reset", "Factory reset completed", None),
        ("sw_update", "Software up to date, OS version recorded", None),
        ("sw_parts", "No non-genuine / unknown part warnings", None),
    ]),
    ("Cosmetic & Physical", [
        ("cos_screen", "Screen glass: no cracks or deep scratches", None),
        ("cos_back", "Back glass / casing: no cracks", None),
        ("cos_frame", "Frame and edges: no dents or deep scuffs", None),
        ("cos_camglass", "Camera glass: no cracks or scratches", None),
        ("cos_buttons", "Buttons: no sticking or damage", None),
        ("cos_water", "Liquid contact indicator not triggered", None),
        ("cos_tamper", "No signs of previous repair or tampering", None),
        ("cos_swell", "No battery swelling or lifting screen", None),
    ]),
    ("Accessories & Extras", [
        ("acc_charger", "Charger included", None),
        ("acc_cable", "Cable included", None),
        ("acc_box", "Original box included", None),
        ("acc_pin", "SIM ejector tool included", None),
    ]),
]

ALL_KEYS = [key for _, items in SECTIONS for key, _, _ in items]
LABELS = {key: label for _, items in SECTIONS for key, label, _ in items}
SECTION_OF = {key: name for name, items in SECTIONS for key, _, _ in items}
APPLIES = {key: applies for _, items in SECTIONS for key, _, applies in items if applies}
TOTAL_STANDARD = len(ALL_KEYS)


def is_custom(key: str) -> bool:
    return key.startswith(CUSTOM_PREFIX)


def label_of(key: str) -> str:
    return key[len(CUSTOM_PREFIX):] if is_custom(key) else LABELS.get(key, key)


def section_of(key: str) -> str:
    return CUSTOM_SECTION if is_custom(key) else SECTION_OF.get(key, "Other")


def is_apple(device: dict) -> bool:
    model = str(device.get("model") or "").lower()
    return str(device.get("brand") or "").strip().lower() == "apple" \
        or model.startswith(("iphone", "ipad", "ipod"))


def not_applicable_keys(device: dict) -> list:
    """Tests this phone can't have, judged from its brand (Apple-only / Android-only)."""
    apple = is_apple(device)
    return [key for key, applies in APPLIES.items()
            if (applies == APPLE and not apple) or (applies == ANDROID and apple)]


def summarise(results: dict) -> dict:
    """Count a {key: {"result", "note"}} mapping, including custom tests."""
    counts = {PASS: 0, FAIL: 0, NA: 0}
    custom = 0
    for key, row in results.items():
        custom += is_custom(key)
        if row.get("result") in counts:
            counts[row["result"]] += 1
    total = TOTAL_STANDARD + custom
    done = sum(counts.values())
    return {"pass": counts[PASS], "fail": counts[FAIL], "na": counts[NA], "done": done,
            "total": total, "untested": max(0, total - done)}


def summary_text(counts: dict) -> str:
    if not counts["done"]:
        return "Not tested"
    if counts["fail"]:
        return f"{counts['fail']} FAIL · {counts['done']}/{counts['total']}"
    if counts["done"] >= counts["total"]:
        return "All passed"
    return f"{counts['done']}/{counts['total']} tested"
