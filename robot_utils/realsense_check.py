import pyrealsense2 as rs
from collections import defaultdict

FORMAT_DESCRIPTIONS = {
    "y8": "8-bit Greyscale (Infrared)",
    "y16": "16-bit Greyscale (Infrared/Raw)",
    "z16": "16-bit Depth",
    "rgb8": "8-bit RGB (Color)",
    "bgr8": "8-bit BGR (Color)",
    "bgra8": "8-bit BGRA (Color with Alpha)",
    "rgba8": "8-bit RGBA (Color with Alpha)",
    "yuyv": "YUV 4:2:2 (Video)",
    "uyvy": "UYVY (Video)",
    "raw10": "10-bit Raw",
    "raw16": "16-bit Raw",
}

def list_profiles():
    ctx = rs.context()
    devices = ctx.query_devices()
    if len(devices) == 0:
        print("No RealSense devices connected")
        return

    print(f"Total devices: {len(devices)}")
    for i, dev in enumerate(devices):
        print(f"[{i}] {dev.get_info(rs.camera_info.name)} (Serial: {dev.get_info(rs.camera_info.serial_number)})")

    for dev in devices:
        dev_name = dev.get_info(rs.camera_info.name)
        print(f"\nDevice: {dev_name}")
        if "d405" in dev_name.lower():
            print("  -> Match Status: D405 detected")
        else:
            print("  -> Match Status: Other RealSense model detected")

        sensors = dev.query_sensors()
        for sensor in sensors:
            sensor_name = sensor.get_info(rs.camera_info.name)
            print(f"  Sensor: {sensor_name}")
            
            # Map to hold profiles: (stream_type, stream_index) -> { (width, height) -> { 'max_fps': 0, 'formats': set() } }
            profiles_map = defaultdict(lambda: defaultdict(lambda: {'max_fps': 0, 'formats': set()}))
            all_formats = set()
            
            for profile in sensor.get_stream_profiles():
                if profile.is_video_stream_profile():
                    sp = profile.as_video_stream_profile()
                    stream_type = profile.stream_type()
                    stream_index = profile.stream_index()
                    res = (sp.width(), sp.height())
                    fps = sp.fps()
                    fmt = str(profile.format()).split('.')[-1]
                    
                    entry = profiles_map[(stream_type, stream_index)][res]
                    if fps > entry['max_fps']:
                        entry['max_fps'] = fps
                    entry['formats'].add(fmt)
                    all_formats.add(fmt)
                    
            if not profiles_map:
                print("    No video stream profiles available.")
                continue

            print("    Grouped Video Profiles:")
            for (stream_type, stream_index), resolutions in sorted(profiles_map.items(), key=lambda x: (str(x[0][0]), x[0][1])):
                stream_name = str(stream_type).split('.')[-1].upper()
                idx_str = f" #{stream_index}" if stream_index > 0 else ""
                print(f"      Stream: {stream_name}{idx_str}")
                for res, data in sorted(resolutions.items(), key=lambda x: (x[0][0], x[0][1]), reverse=True):
                    formats_str = ", ".join(sorted(data['formats']))
                    print(f"        Resolution: {res[0]}x{res[1]} | Max FPS: {data['max_fps']} | Formats: [{formats_str}]")
            
            # Print Glossary of formats found in this sensor
            # if all_formats:
            #     print("    Format Glossary:")
            #     for fmt in sorted(all_formats):
            #         desc = FORMAT_DESCRIPTIONS.get(fmt.lower(), "Unknown format")
            #         print(f"      - {fmt}: {desc}")

if __name__ == "__main__":
    list_profiles()
