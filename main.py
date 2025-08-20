from bleak import BleakScanner
from asyncio import new_event_loop, set_event_loop, get_event_loop, sleep as async_sleep
from time import sleep, time_ns
from binascii import hexlify
from json import dumps
from sys import argv
from datetime import datetime

UPDATE_DURATION = 1
MIN_RSSI = -60
AIRPODS_MANUFACTURER = 76
AIRPODS_DATA_LENGTH = 54
RECENT_BEACONS_MAX_T_NS = 10_000_000_000  # 10 Seconds

recent_beacons = []
matching_devices = {}

def get_best_result(device, adv_data):
    recent_beacons.append({
        "time": time_ns(),
        "device": device,
        "adv": adv_data
    })

    strongest_beacon = None
    i = 0
    while i < len(recent_beacons):
        entry = recent_beacons[i]
        elapsed = time_ns() - entry["time"]

        if elapsed > RECENT_BEACONS_MAX_T_NS:
            recent_beacons.pop(i)
            continue

        if strongest_beacon is None or entry["adv"].rssi > strongest_beacon["adv"].rssi:
            strongest_beacon = entry

        i += 1

    if strongest_beacon and strongest_beacon["device"].address == device.address:
        strongest_beacon = {
            "device": device,
            "adv": adv_data
        }

    return strongest_beacon

def detection_callback(device, adv_data):
    if adv_data and adv_data.rssi >= MIN_RSSI and AIRPODS_MANUFACTURER in adv_data.manufacturer_data:
        best = get_best_result(device, adv_data)
        if best:
            matching_devices[device.address] = best

async def get_device():
    scanner = BleakScanner(detection_callback)
    await scanner.start()
    await async_sleep(3.0)
    await scanner.stop()

    for entry in matching_devices.values():
        adv = entry["adv"]
        data = adv.manufacturer_data[AIRPODS_MANUFACTURER]
        data_hex = hexlify(bytearray(data))
        if len(data_hex) == AIRPODS_DATA_LENGTH:
            return data_hex

    return False

def get_data_hex():
    new_loop = new_event_loop()
    set_event_loop(new_loop)
    loop = get_event_loop()
    a = loop.run_until_complete(get_device())
    loop.close()
    return a

def is_flipped(raw):
    return (int("" + chr(raw[10]), 16) & 0x02) == 0

def get_data():
    raw = get_data_hex()

    if not raw:
        return dict(status=0, model="AirPods not found")

    flip: bool = is_flipped(raw)

    # Detect model
    model_char = chr(raw[7])
    if model_char == 'e':
        model = "AirPodsPro"
    elif model_char == '3':
        model = "AirPods3"
    elif model_char == 'f':
        model = "AirPods2"
    elif model_char == '2':
        model = "AirPods1"
    elif model_char == 'a':
        model = "AirPodsMax"
    else:
        model = "unknown"

    # Special handling for AirPods Max
    if model == "AirPodsMax":
        left_raw = int("" + chr(raw[12 if flip else 13]), 16)
        right_raw = int("" + chr(raw[13 if flip else 12]), 16)

        left_status = 100 if left_raw == 10 else (left_raw * 10 + 5 if left_raw <= 10 else -1)
        right_status = 100 if right_raw == 10 else (right_raw * 10 + 5 if right_raw <= 10 else -1)

        # Pick the more valid battery level
        valid_levels = [b for b in [left_status, right_status] if b > 10 and b <= 100]
        charge = max(valid_levels) if valid_levels else -1

        charging_status = int("" + chr(raw[14]), 16)
        charging_left = (charging_status & (0b00000010 if flip else 0b00000001)) != 0
        charging_right = (charging_status & (0b00000001 if flip else 0b00000010)) != 0
        charging = charging_left or charging_right

        return dict(
            status=1,
            charge=charge,
            charging=charging,
            model=model,
            date=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            raw=raw.decode("utf-8")
        )

    # Standard AirPods (Pro, 1, 2, 3)
    left_raw = int("" + chr(raw[12 if flip else 13]), 16)
    left_status = 100 if left_raw == 10 else (left_raw * 10 + 5 if left_raw <= 10 else -1)

    right_raw = int("" + chr(raw[13 if flip else 12]), 16)
    right_status = 100 if right_raw == 10 else (right_raw * 10 + 5 if right_raw <= 10 else -1)

    case_raw = int("" + chr(raw[15]), 16)
    case_status = 100 if case_raw == 10 else (case_raw * 10 + 5 if case_raw <= 10 else -1)

    charging_status = int("" + chr(raw[14]), 16)
    charging_left = (charging_status & (0b00000010 if flip else 0b00000001)) != 0
    charging_right = (charging_status & (0b00000001 if flip else 0b00000010)) != 0
    charging_case = (charging_status & 0b00000100) != 0

    return dict(
        status=1,
        charge=dict(
            left=left_status,
            right=right_status,
            case=case_status
        ),
        charging_left=charging_left,
        charging_right=charging_right,
        charging_case=charging_case,
        model=model,
        date=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        raw=raw.decode("utf-8")
    )

def display_data_as_table(data: dict):
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    console = Console()
    console.clear()

    table = Table(
        title=f"AirPods Status - {data['model']} @ {data['date']}",
        title_style="bold cyan",
        border_style="cyan"
    )

    table.add_column("Component", style="cyan", no_wrap=True)
    table.add_column("Battery Level", justify="center")
    table.add_column("Charging", justify="center")

    def style_battery(percent):
        if not isinstance(percent, int) or percent < 0 or percent > 100:
            return Text("Unknown", style="dim")
        elif percent >= 80:
            return Text(f"{percent}%", style="green")
        elif percent >= 40:
            return Text(f"{percent}%", style="yellow")
        else:
            return Text(f"{percent}%", style="red")

    def style_charging(is_charging):
        return Text("Yes", style="green") if is_charging else Text("No", style="red")

    charge_data = data.get("charge", {})
    model = data.get("model")

    if model == "AirPodsMax":
        charge = style_battery(charge_data if isinstance(charge_data, int) else -1)
        charging = style_charging(data.get("charging"))
        table.add_row("AirPods Max", charge, charging)

    else:
        table.add_row(
            "Left Pod",
            style_battery(charge_data.get("left", -1)),
            style_charging(data.get("charging_left"))
        )
        table.add_row(
            "Right Pod",
            style_battery(charge_data.get("right", -1)),
            style_charging(data.get("charging_right"))
        )
        table.add_row(
            "Case",
            style_battery(charge_data.get("case", -1)),
            style_charging(data.get("charging_case"))
        )

    console.print(table)

def run():
    output_file = argv[-1] if len(argv) > 1 else None

    try:
        while True:
            data = get_data()

            if data["status"] == 1:
                if output_file:
                    with open(output_file, "a") as f:
                        f.write(dumps(data) + "\n")
                else:
                    display_data_as_table(data)

            sleep(UPDATE_DURATION)

    except KeyboardInterrupt:
        print("\n[INFO] Script interrupted.")

if __name__ == '__main__':
    run()
